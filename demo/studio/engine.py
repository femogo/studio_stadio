"""Motor de ejecuciones: astream → eventos normalizados → bus SSE + tabla events (DISENO.md §5, §6.2)."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from langchain_core.messages import AIMessageChunk
from langgraph.errors import GraphDrained, GraphRecursionError
from langgraph.runtime import RunControl
from langgraph.types import Command, Interrupt, Send

from compiler import BASE, EstudioCache, Env, GpuGate, ValidationError, build, cache_path, flat
from db import DB, Projects, Settings
from modules import Registry

PERSIST_SKIP = {"node.progress", "node.partial"}
ACTIVE = {"pending", "running", "pausing"}
STRATEGIES = ("reject", "enqueue", "interrupt", "rollback")
COMO_KIND = {"texto": "text", "text": "text", "video": "video", "audio": "audio", "imagen": "image", "image": "image"}


class ThreadBusy(Exception):
    pass


class NotInterrupted(Exception):
    pass


def _enc(o):
    if isinstance(o, Interrupt):
        return {"id": o.id, "value": o.value, "response_schema": getattr(o, "response_schema", None)}
    if isinstance(o, Send):
        return {"node": o.node, "arg": o.arg}
    if hasattr(o, "model_dump"):
        try:
            return o.model_dump()
        except Exception:
            pass
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, default=_enc)


# ------------------------------------------------------------------------------------------------- bus
class Bus:
    """Eventos en vivo para los suscriptores SSE + escritura por lotes en `events`."""

    def __init__(self, db: DB):
        self.db = db
        self.subs: list[tuple[str | None, asyncio.Queue]] = []
        self.pending: list[tuple] = []
        self.next_id = 0
        self._agg: dict[tuple, dict] = {}
        self._task = None

    async def start(self):
        r = await self.db.one("SELECT COALESCE(MAX(id), 0) AS m FROM events")
        self.next_id = r["m"]
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.flush()

    def subscribe(self, thread: str | None) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=2000)
        self.subs.append((thread, q))
        return q

    def unsubscribe(self, q):
        self.subs = [(t, x) for t, x in self.subs if x is not q]

    def emit(self, run_id, thread_id, type_, node=None, task_id=None, data=None, ns=""):
        ev = {"run_id": run_id, "thread_id": thread_id, "ns": ns, "type": type_, "node": node, "task_id": task_id,
              "data": data if data is not None else {}, "ts": time.time()}
        if type_ in PERSIST_SKIP:
            # se agregan: último valor por nodo, se emiten cada 200 ms, no se guardan
            key = (thread_id, node, type_)
            if type_ == "node.partial" and key in self._agg:
                prev = self._agg[key]["data"].get("patch") or {}
                ev["data"] = {"patch": {**prev, **(ev["data"].get("patch") or {})}}
            self._agg[key] = ev
            return ev
        self.next_id += 1
        ev["id"] = self.next_id
        self.pending.append((ev["id"], run_id, thread_id, ns, type_, node, task_id, dumps(ev["data"]), ev["ts"]))
        self._fanout(ev)
        return ev

    def _fanout(self, ev):
        for t, q in list(self.subs):
            if t and t != ev["thread_id"]:
                continue
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                if ev["type"] in PERSIST_SKIP or ev["type"] == "node.log":
                    continue
                try:
                    q.get_nowait()
                    q.put_nowait(ev)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    async def flush(self):
        if self.pending:
            rows, self.pending = self.pending, []
            await self.db.execmany("INSERT INTO events(id, run_id, thread_id, ns, type, node, task_id, data, ts) "
                                   "VALUES (?,?,?,?,?,?,?,?,?)", rows)

    def flush_agg(self):
        agg, self._agg = self._agg, {}
        for ev in agg.values():
            self._fanout(ev)

    async def _loop(self):
        n = 0
        while True:
            await asyncio.sleep(0.05)
            n += 1
            if n % 4 == 0:
                self.flush_agg()
            if n % 5 == 0:
                try:
                    await self.flush()
                except Exception:
                    traceback.print_exc()


# ------------------------------------------------------------------------------------------------- ejecución
@dataclass
class RunCtx:
    run_id: str
    thread_id: str
    project: str
    kind: str
    body: dict
    task: asyncio.Task | None = None
    control: RunControl = field(default_factory=RunControl)
    status: str = "pending"
    stopping: bool = False
    pgids: set = field(default_factory=set)
    stop_motivo: str | None = None                      # interrupt | rollback (double-texting, F2)
    queued: bool = False
    position: int = 0
    replaces: str | None = None
    running_nodes: dict = field(default_factory=dict)   # task_id -> node
    finished: set = field(default_factory=set)
    edges: list = field(default_factory=list)


class Engine:
    def __init__(self, home: Path, settings: Settings, db: DB, checkpointer, registry: Registry, projects: Projects,
                 store=None):
        self.home = Path(home)
        self.store = store
        self.settings = settings
        self.db = db
        self.cp = checkpointer
        self.registry = registry
        self.projects = projects
        self.bus = Bus(db)
        self.gpu = GpuGate()
        self.active: dict[str, RunCtx] = {}      # thread_id -> RunCtx
        self.by_run: dict[str, RunCtx] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.closing = False

    # ---------------------------------------------------------------- ciclo de vida
    async def start(self):
        await self.reconcile()
        await self.bus.start()
        for r in await self.db.all("SELECT DISTINCT thread_id FROM run_queue"):
            asyncio.get_running_loop().create_task(self._next_queued(r["thread_id"]))

    async def shutdown(self):
        self.closing = True
        for rc in list(self.active.values()):
            rc.stopping = True
            if rc.task:
                rc.task.cancel()
        tasks = [rc.task for rc in self.active.values() if rc.task]
        if tasks:
            await asyncio.wait(tasks, timeout=5)
        for rc in list(self.by_run.values()):
            for pg in list(rc.pgids):
                _killpg(pg, signal.SIGKILL)
        await self.bus.stop()

    async def reconcile(self):
        rows = await self.db.all("SELECT run_id, thread_id, status, pgids FROM runs WHERE status IN "
                                 "('pending','running','pausing')")
        for r in rows:
            for pg in json.loads(r["pgids"] or "[]"):
                try:
                    cmd = Path(f"/proc/{pg}/cmdline").read_bytes()
                except OSError:
                    continue
                if b"runner.py" in cmd:
                    _killpg(pg, signal.SIGKILL)
            if r["status"] == "pending":
                await self.db.exec("UPDATE runs SET status='error', error=?, t1=? WHERE run_id=?",
                                   ("servidor reiniciado antes de empezar", time.time(), r["run_id"]))
            else:
                await self.db.exec("UPDATE runs SET status='stopped', error=?, t1=? WHERE run_id=?",
                                   ("servidor reiniciado", time.time(), r["run_id"]))

    def env(self) -> Env:
        return Env(home=self.home, settings=self.settings.values, registry=self.registry, gpu=self.gpu,
                   on_pgid=self._on_pgid, before_exclusive=self.unload_ollama, load_project=self.projects.get)

    def _on_pgid(self, run_id, pgid, vivo):
        rc = self.by_run.get(run_id)
        if not rc:
            return
        (rc.pgids.add if vivo else rc.pgids.discard)(pgid)
        asyncio.get_running_loop().create_task(
            self.db.exec("UPDATE runs SET pgids=? WHERE run_id=?", (json.dumps(sorted(rc.pgids)), run_id)))

    async def unload_ollama(self):
        """D6: descargar los modelos de Ollama antes de un nodo 'exclusive'."""
        url = self.settings.get("ollama_url", "http://localhost:11434").rstrip("/")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url + "/api/ps")
            for m in r.json().get("models", []):
                await c.post(url + "/api/generate", json={"model": m["name"], "keep_alive": 0})

    # ---------------------------------------------------------------- consultas
    def busy(self, thread_id) -> bool:
        return thread_id in self.active

    async def thread_status(self, thread_id) -> str:
        if thread_id in self.active:
            return "busy"
        r = await self.db.one("SELECT status FROM runs WHERE thread_id=? ORDER BY t0 DESC LIMIT 1", (thread_id,))
        if not r:
            return "idle"
        return {"interrupted": "interrupted", "error": "error"}.get(r["status"], "idle")

    def graph_for(self, project: dict, extra_keys=()):
        return build(project, checkpointer=self.cp, env=self.env(), extra_keys=extra_keys, load_modules=False,
                     store=self.store).graph

    async def channel_keys(self, thread_id, checkpoint_id=None) -> list:
        cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        if checkpoint_id:
            cfg["configurable"]["checkpoint_id"] = checkpoint_id
        t = await self.cp.aget_tuple(cfg)
        if not t:
            return []
        return [k for k in t.checkpoint.get("channel_values", {}) if not k.startswith(("__", "branch:"))]

    async def history_keys(self, thread_id) -> list:
        """Claves de todos los checkpoints del hilo (para no perder canales al consultar ramas antiguas)."""
        keys = set()
        async for t in self.cp.alist({"configurable": {"thread_id": thread_id}}, limit=200):
            keys |= {k for k in t.checkpoint.get("channel_values", {}) if not k.startswith(("__", "branch:"))}
        return sorted(keys)

    # ---------------------------------------------------------------- acciones
    def _lock(self, tid) -> asyncio.Lock:
        return self._locks.setdefault(tid, asyncio.Lock())

    @staticmethod
    def _kind(body) -> str:
        if body.get("resume") is not None:
            return "resume"
        if body.get("input") is not None:
            return "input"
        if body.get("checkpoint_id"):
            return body.get("kind_hint") if body.get("kind_hint") in ("fork", "replay") else "replay"
        return "continue"

    async def create_run(self, thread: dict, body: dict) -> RunCtx:
        """Crea una ejecución aplicando la estrategia de double-texting si el hilo está ocupado (§5.7, §6.3)."""
        tid = thread["thread_id"]
        if body.get("input") is not None and body.get("resume") is not None:
            raise ValueError("Como mucho uno de input / resume")
        strategy = body.get("multitask_strategy") or self.settings.get("multitask_strategy") or "reject"
        if strategy not in STRATEGIES:
            raise ValueError(f"multitask_strategy debe ser una de: {', '.join(STRATEGIES)}")
        async with self._lock(tid):
            replaces = None
            if tid in self.active:
                if strategy == "reject":
                    raise ThreadBusy(tid)
                if strategy == "enqueue":
                    return await self._enqueue(thread, body)
                old = self.active[tid]
                replaces = old.run_id
                await self._replace(old, strategy, body)
            rc = await self._launch(thread, body)
            rc.replaces = replaces
            return rc

    async def _launch(self, thread: dict, body: dict, run_id: str | None = None) -> RunCtx:
        tid = thread["thread_id"]
        if tid in self.active:
            raise ThreadBusy(tid)
        kind = self._kind(body)
        project = self.projects.get(thread["project"])
        run_id = run_id or str(uuid.uuid4())
        rc = RunCtx(run_id=run_id, thread_id=tid, project=thread["project"], kind=kind, body=body)
        self.active[tid] = rc
        self.by_run[run_id] = rc
        ib, ia = self._breakpoints(project, body)
        asst = body.get("_assistant") or {}
        await self.db.exec("DELETE FROM runs WHERE run_id=? AND status='queued'", (run_id,))
        await self.db.exec(
            "INSERT INTO runs(run_id, thread_id, project, project_version, kind, input, checkpoint_id, interrupt_before, "
            "interrupt_after, step, status, t0, assistant_id, assistant_version, multitask_strategy, cron_id, "
            "experiment_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, tid, thread["project"], project.get("version"), kind,
             dumps(body.get("input") if kind != "resume" else body.get("resume")), body.get("checkpoint_id"),
             dumps(ib), dumps(ia), 1 if body.get("step") else 0, "pending", time.time(),
             asst.get("assistant_id"), asst.get("version"), body.get("multitask_strategy"), body.get("_cron_id"),
             body.get("_experiment_id")))
        await self.db.exec("UPDATE threads SET updated=? WHERE thread_id=?", (time.time(), tid))
        self.bus.emit(run_id, tid, "run.status", data={"status": "pending", "kind": kind})
        self.bus.emit(run_id, tid, "thread.status", data={"status": "busy"})
        rc.task = asyncio.create_task(self._execute(rc, project, ib, ia))
        return rc

    # ---------------------------------------------------------------- double-texting [F2]
    async def _enqueue(self, thread: dict, body: dict) -> RunCtx:
        tid = thread["thread_id"]
        run_id = str(uuid.uuid4())
        now = time.time()
        kind = self._kind(body)
        await self.db.exec("INSERT INTO run_queue(run_id, thread_id, payload, created) VALUES (?,?,?,?)",
                           (run_id, tid, dumps(body), now))
        await self.db.exec(
            "INSERT INTO runs(run_id, thread_id, project, kind, input, checkpoint_id, status, t0, assistant_id, "
            "assistant_version, multitask_strategy, cron_id, experiment_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, tid, thread["project"], kind,
             dumps(body.get("input") if kind != "resume" else body.get("resume")), body.get("checkpoint_id"),
             "queued", now, (body.get("_assistant") or {}).get("assistant_id"),
             (body.get("_assistant") or {}).get("version"), "enqueue", body.get("_cron_id"), body.get("_experiment_id")))
        pos = (await self.db.one("SELECT COUNT(*) n FROM run_queue WHERE thread_id=?", (tid,)))["n"]
        self.bus.emit(run_id, tid, "run.status", data={"status": "queued", "kind": kind})
        self.bus.emit(run_id, tid, "run.queued", data={"position": pos})
        rc = RunCtx(run_id=run_id, thread_id=tid, project=thread["project"], kind=kind, body=body, status="queued")
        rc.queued, rc.position = True, pos
        return rc

    async def _next_queued(self, tid):
        async with self._lock(tid):
            if tid in self.active:
                return
            row = await self.db.one("SELECT * FROM run_queue WHERE thread_id=? ORDER BY created LIMIT 1", (tid,))
            if not row or self.closing:
                return
            thread = await self.db.one("SELECT * FROM threads WHERE thread_id=?", (tid,))
            try:
                if not thread:
                    raise ValueError("el hilo ya no existe")
                await self._launch(thread, json.loads(row["payload"]), run_id=row["run_id"])
                await self.db.exec("DELETE FROM run_queue WHERE run_id=?", (row["run_id"],))
            except Exception as e:
                await self.db.exec("DELETE FROM run_queue WHERE run_id=?", (row["run_id"],))
                await self.db.exec("UPDATE runs SET status='error', error=?, t1=? WHERE run_id=?",
                                   (f"No se pudo lanzar desde la cola: {e}", time.time(), row["run_id"]))
                self.bus.emit(row["run_id"], tid, "run.status", data={"status": "error", "error": str(e)})
                asyncio.get_running_loop().create_task(self._next_queued(tid))

    async def cancel_queued(self, run_id) -> bool:
        row = await self.db.one("SELECT thread_id FROM run_queue WHERE run_id=?", (run_id,))
        if not row:
            return False
        await self.db.exec("DELETE FROM run_queue WHERE run_id=?", (run_id,))
        await self.db.exec("UPDATE runs SET status='stopped', error=?, t1=? WHERE run_id=?",
                           ("Quitada de la cola", time.time(), run_id))
        self.bus.emit(run_id, row["thread_id"], "run.status", data={"status": "stopped", "error": "Quitada de la cola"})
        return True

    async def _replace(self, old: RunCtx, strategy: str, body: dict):
        """interrupt: detener la actual. rollback: además, lanzar la nueva desde donde empezó la actual."""
        old.stop_motivo = strategy
        inicio = None
        if strategy == "rollback":
            r = await self.db.one("SELECT checkpoint_id, checkpoint_id_inicio FROM runs WHERE run_id=?", (old.run_id,))
            inicio = (old.body.get("checkpoint_id") or (r or {}).get("checkpoint_id")
                      or (r or {}).get("checkpoint_id_inicio"))
        await self.stop(old.run_id)
        if old.task and not old.task.done():
            try:
                await asyncio.wait_for(asyncio.shield(old.task), 20)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        if strategy != "rollback":
            return
        await self.bus.flush()
        tid = old.thread_id
        evs = await self.db.all("SELECT data FROM events WHERE run_id=? AND type='checkpoint' AND ns=''",
                                (old.run_id,))
        cps = [json.loads(e["data"]).get("checkpoint_id") for e in evs]
        cps = [c for c in cps if c and c != inicio]
        if inicio:
            body["checkpoint_id"] = inicio
            t = await self.db.one("SELECT metadata FROM threads WHERE thread_id=?", (tid,))
            meta = json.loads((t or {}).get("metadata") or "{}")
            meta["rolled_back"] = list(dict.fromkeys((meta.get("rolled_back") or []) + cps))
            await self.db.exec("UPDATE threads SET metadata=? WHERE thread_id=?", (dumps(meta), tid))
        else:
            await self._move_checkpoints(tid, f"rb-{old.run_id}", old.project)

    async def _move_checkpoints(self, src: str, dst: str, project: str):
        """Mueve todos los checkpoints de un hilo a un hilo oculto (rollback sobre un hilo que estaba vacío)."""
        async with self.cp.lock:
            con = self.cp.conn
            await con.execute("BEGIN")
            try:
                await con.execute("UPDATE checkpoints SET thread_id=? WHERE thread_id=?", (dst, src))
                await con.execute("UPDATE writes SET thread_id=? WHERE thread_id=?", (dst, src))
                await con.commit()
            except Exception:
                await con.rollback()
                raise
        now = time.time()
        await self.db.exec("INSERT OR IGNORE INTO threads(thread_id, project, title, metadata, created, updated) "
                           "VALUES (?,?,?,?,?,?)", (dst, project, f"Rollback de {src[:8]}",
                                                    dumps({"oculto": True, "rolled_back_de": src}), now, now))

    def _breakpoints(self, project, body):
        if body.get("step"):
            return "*", body.get("interrupt_after") or []
        ib = body.get("interrupt_before")
        ia = body.get("interrupt_after")
        if ib is None:
            ib = [n for n, d in project.get("nodes", {}).items() if (d.get("breakpoint") or {}).get("before")]
        if ia is None:
            ia = [n for n, d in project.get("nodes", {}).items() if (d.get("breakpoint") or {}).get("after")]
        return ib, ia

    async def pause(self, run_id):
        rc = self.by_run.get(run_id)
        if not rc or rc.status not in ("running", "pending"):
            return False
        rc.control.request_drain("pausa")
        await self._set_status(rc, "pausing", esperando=sorted(set(rc.running_nodes.values())))
        return True

    async def stop(self, run_id):
        rc = self.by_run.get(run_id)
        if not rc or rc.status not in ACTIVE:
            return False
        rc.stopping = True
        if rc.task:
            rc.task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(rc.task), 10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        return True

    async def _set_status(self, rc: RunCtx, status, **extra):
        rc.status = status
        sets, args = ["status=?"], [status]
        for k in ("motivo", "error", "traceback"):
            if k in extra:
                sets.append(f"{k}=?")
                args.append(extra[k])
        if status not in ACTIVE:
            sets.append("t1=?")
            args.append(time.time())
        await self.db.exec(f"UPDATE runs SET {', '.join(sets)} WHERE run_id=?", (*args, rc.run_id))
        self.bus.emit(rc.run_id, rc.thread_id, "run.status", data={"status": status, **extra})

    # ---------------------------------------------------------------- tarea de la ejecución
    async def _execute(self, rc: RunCtx, project: dict, ib, ia):
        body = rc.body
        tid = rc.thread_id
        graph = None
        cache = None
        try:
            inp_keys = list((body.get("input") or {}).keys()) if isinstance(body.get("input"), dict) else []
            extra = set(inp_keys) | set(await self.history_keys(tid))
            asst = body.get("_assistant") or {"context": {}, "node_overrides": {}}
            if any((n or {}).get("cache") is not None for n in (project.get("nodes") or {}).values()):
                cache = EstudioCache(path=str(cache_path(self.home, project["slug"])),
                                     bases=(BASE, self.home))
            built = build(project, assistant=asst, checkpointer=self.cp, env=self.env(), extra_keys=sorted(extra),
                          cache=cache, store=self.store)
            graph = built.graph
            prev = await self.db.one("SELECT module_shas FROM runs WHERE thread_id=? AND run_id<>? AND module_shas IS NOT "
                                     "NULL ORDER BY t0 DESC LIMIT 1", (tid, rc.run_id))
            cfg_t = {"configurable": {"thread_id": tid}}
            st0 = await graph.aget_state(cfg_t)
            inicio = (st0.config or {}).get("configurable", {}).get("checkpoint_id") if st0 else None
            await self.db.exec("UPDATE runs SET module_shas=?, checkpoint_id_inicio=? WHERE run_id=?",
                               (dumps(built.module_shas), inicio, rc.run_id))
            if prev and rc.kind != "input":
                old = json.loads(prev["module_shas"] or "{}")
                cambiados = [f for f, s in built.module_shas.items() if old.get(f) and old[f] != s]
                if cambiados:
                    self.bus.emit(rc.run_id, tid, "node.log", data={
                        "line": f"Aviso: código cambiado desde el checkpoint: {', '.join(cambiados)}"})
            config = {"configurable": {"thread_id": tid, "run_id_estudio": rc.run_id},
                      "recursion_limit": int(body.get("recursion_limit") or self.settings["recursion_limit"]),
                      "max_concurrency": self.settings.get("max_concurrency")}
            if body.get("checkpoint_id"):
                config["configurable"]["checkpoint_id"] = body["checkpoint_id"]
                config["configurable"]["checkpoint_ns"] = ""
            if rc.kind == "resume":
                inp = Command(resume=body["resume"])
            elif rc.kind == "input":
                inp = body["input"]
            else:
                inp = None
            await self._set_status(rc, "running")
            edges = project.get("edges") or []
            async for ns, mode, ch in graph.astream(
                    inp, config, context=asst.get("context") or {},
                    stream_mode=["updates", "custom", "checkpoints", "messages"], subgraphs=True,
                    interrupt_before=ib or None, interrupt_after=ia or None,
                    durability=self.settings.get("durability", "async"), control=rc.control, version="v1"):
                nss = "|".join(ns) if ns else ""
                if mode == "custom" and isinstance(ch, dict) and ch.get("__studio__"):
                    await self._on_custom(rc, ch, nss, project)
                elif mode == "custom":
                    self.bus.emit(rc.run_id, tid, "node.partial", node=None, data={"patch": ch}, ns=nss)
                elif mode == "checkpoints":
                    self._on_checkpoint(rc, ch, nss, edges)
                elif mode == "updates" and isinstance(ch, dict):
                    self._on_updates(rc, ch, nss, project)
                elif mode == "messages":
                    self._on_message(rc, ch, ns)
            st = await graph.aget_state(cfg_t)
            if st.interrupts:
                for task in st.tasks:
                    for it in task.interrupts:
                        self.bus.emit(rc.run_id, tid, "interrupt", node=task.name, task_id=task.id,
                                      data={"id": it.id, "value": it.value,
                                            "response_schema": getattr(it, "response_schema", None),
                                            "node": task.name})
                await self._set_status(rc, "interrupted", motivo="humano")
            elif st.next:
                await self._set_status(rc, "interrupted", motivo="breakpoint")
            else:
                if not st.next:
                    self._edges_to_end(rc, edges)
                await self._set_status(rc, "success")
        except GraphDrained:
            await self._set_status(rc, "drained")
        except asyncio.CancelledError:
            if rc.stop_motivo:
                await self._set_status(rc, "stopped", motivo=rc.stop_motivo, error={
                    "interrupt": "Interrumpida por una ejecución nueva",
                    "rollback": "Deshecha (rollback) por una ejecución nueva"}[rc.stop_motivo])
            else:
                await self._set_status(rc, "stopped", error="Detenida por el usuario" if rc.stopping else "Cancelada")
        except ValidationError as e:
            await self._set_status(rc, "error", error="Proyecto no válido: " + "; ".join(x["msg"] for x in e.errores),
                                   traceback="")
        except Exception as e:
            tb = traceback.format_exc()
            msg = f"{type(e).__name__}: {e}"
            if isinstance(e, GraphRecursionError):
                msg = f"GraphRecursionError: se alcanzó el límite de recursión ({config['recursion_limit']})"
            await self._set_status(rc, "error", error=msg, traceback=tb[-8000:])
        finally:
            if cache is not None:
                cache.close()
            for pg in list(rc.pgids):
                _killpg(pg, signal.SIGKILL)
            self.active.pop(tid, None)
            self.bus.flush_agg()
            self.bus.emit(rc.run_id, tid, "thread.status", data={"status": await self.thread_status(tid)})
            await self.bus.flush()
            asyncio.get_running_loop().call_later(600, self.by_run.pop, rc.run_id, None)
            if not rc.stop_motivo and not self.closing:
                asyncio.get_running_loop().create_task(self._next_queued(tid))

    async def _on_custom(self, rc: RunCtx, ch: dict, ns: str, project: dict):
        t, node, task_id, data = ch["type"], ch.get("node"), ch.get("task_id"), ch.get("data") or {}
        if t == "node.start" and "esperando_gpu" not in data:
            rc.running_nodes[task_id] = node
            if rc.kind == "input" and not ns:
                for e in project.get("edges") or []:
                    if e["src"] == "__start__" and e.get("dst") == node and e.get("id") not in rc.edges:
                        rc.edges.append(e.get("id"))
                        self.bus.emit(rc.run_id, rc.thread_id, "edge", data={"edge_id": e.get("id")})
        elif t == "node.end":
            rc.running_nodes.pop(task_id, None)
            if data.get("status") == "success":
                rc.finished.add(node)
            for s in data.pop("salidas", None) or []:
                kind = COMO_KIND.get(s.get("como"), "text")
                v = s.get("valor")
                if v is None or v == "":
                    continue
                path, texto = (None, v if isinstance(v, str) else dumps(v)) if kind == "text" else (str(v), None)
                await self.db.exec("INSERT INTO outputs(run_id, thread_id, project, node, kind, clave, path, texto, ts) "
                                   "VALUES (?,?,?,?,?,?,?,?,?)",
                                   (rc.run_id, rc.thread_id, rc.project, node, kind, s.get("clave"), path, texto,
                                    time.time()))
                data.setdefault("salidas_registradas", []).append({"clave": s.get("clave"), "kind": kind})
        self.bus.emit(rc.run_id, rc.thread_id, t, node=node, task_id=task_id, data=data, ns=ns)

    def _on_message(self, rc: RunCtx, ch, ns):
        """Stream 'messages' [F2]: fragmentos de LLM de LangChain → token {text, msg_id}."""
        try:
            chunk, meta = ch
        except (TypeError, ValueError):
            return
        if not isinstance(chunk, AIMessageChunk):
            return
        text = chunk.content if isinstance(chunk.content, str) else "".join(
            p.get("text", "") for p in chunk.content if isinstance(p, dict))
        if not text:
            return
        inner = (meta or {}).get("langgraph_node")
        node = ns[0].split(":")[0] if ns else inner
        self.bus.emit(rc.run_id, rc.thread_id, "token", node=node, ns="|".join(ns) if ns else "",
                      data={"text": text, "msg_id": chunk.id, "inner": inner})

    def _on_updates(self, rc: RunCtx, ch: dict, ns: str, project: dict):
        """Aciertos de caché (el wrapper no se ejecuta) y manejadores de error (§6.3, spike 2a)."""
        nodes = project.get("nodes") or {}
        cached = (ch.get("__metadata__") or {}).get("cached")
        for k, v in ch.items():
            if k == "__metadata__":
                continue
            if k.startswith("__error_handler__"):
                n = k[len("__error_handler__"):]
                err = (v or {}).get("error") if isinstance(v, dict) else None
                self.bus.emit(rc.run_id, rc.thread_id, "node.end", node=n, ns=ns, data={
                    "status": "error", "handler": True, "error_to": (nodes.get(n) or {}).get("error_to"),
                    "error": (err or {}).get("msg"), "duration": 0})
                rc.finished.add(n)
                continue
            if cached and not ns and (nodes.get(k) or {}).get("cache") is not None:
                self.bus.emit(rc.run_id, rc.thread_id, "node.start", node=k, data={"attempt": 1, "cached": True})
                self.bus.emit(rc.run_id, rc.thread_id, "node.end", node=k, data={
                    "status": "cached", "output": v if isinstance(v, dict) else None, "duration": 0})
                rc.finished.add(k)

    def _on_checkpoint(self, rc: RunCtx, ch: dict, ns: str, edges: list):
        conf = (ch.get("config") or {}).get("configurable", {})
        pconf = (ch.get("parent_config") or {}).get("configurable", {}) if ch.get("parent_config") else {}
        meta = ch.get("metadata") or {}
        nxt = list(ch.get("next") or [])
        self.bus.emit(rc.run_id, rc.thread_id, "checkpoint", ns=ns, data={
            "checkpoint_id": conf.get("checkpoint_id"), "parent_checkpoint_id": pconf.get("checkpoint_id"),
            "step": meta.get("step"), "next": nxt, "source": meta.get("source")})
        if ns:
            return
        if rc.finished and nxt:
            for e in edges:
                srcs = e["src"] if isinstance(e["src"], list) else [e["src"]]
                if e.get("dst") in nxt and set(srcs) & rc.finished:
                    self.bus.emit(rc.run_id, rc.thread_id, "edge", data={"edge_id": e.get("id")})
            rc.finished = set()

    def _edges_to_end(self, rc, edges):
        for e in edges:
            srcs = e["src"] if isinstance(e["src"], list) else [e["src"]]
            if e.get("dst") == "__end__" and set(srcs) & rc.finished:
                self.bus.emit(rc.run_id, rc.thread_id, "edge", data={"edge_id": e.get("id")})

    # ---------------------------------------------------------------- vista del hilo
    async def view(self, thread_id) -> dict:
        run = await self.db.one("SELECT * FROM runs WHERE thread_id=? ORDER BY t0 DESC LIMIT 1", (thread_id,))
        nodos, aristas = {}, []
        if run:
            await self.bus.flush()
            evs = await self.db.all("SELECT type, node, data FROM events WHERE run_id=? AND ns='' AND type IN "
                                    "('node.start','node.end','edge','interrupt') ORDER BY id", (run["run_id"],))
            for ev in evs:
                d = json.loads(ev["data"] or "{}")
                if ev["type"] == "edge":
                    aristas.append(d.get("edge_id"))
                    continue
                n = nodos.setdefault(ev["node"], {"status": "pending", "progress": 0, "attempts": 0, "count": 0,
                                                  "error": None, "cached": False})
                if ev["type"] == "node.start":
                    if "esperando_gpu" in d:
                        n["status"] = "waiting_gpu" if d["esperando_gpu"] else "running"
                    else:
                        n["status"] = "running"
                        n["attempts"] = d.get("attempt", 1)
                        if d.get("attempt", 1) == 1:
                            n["count"] += 1
                elif ev["type"] == "node.end":
                    if d.get("handler"):
                        n["handled_by"] = d.get("error_to")
                        continue
                    n["status"] = d.get("status")
                    n["error"] = d.get("error")
                    n["cached"] = d.get("status") == "cached"
                    n["progress"] = 1 if d.get("status") == "success" else n["progress"]
                elif ev["type"] == "interrupt":
                    n["status"] = "interrupted"
            if run["status"] in ("stopped", "drained", "error"):
                for n in nodos.values():
                    if n["status"] in ("running", "waiting_gpu"):
                        n["status"] = "stopped"
        return {"run": run, "nodos": nodos, "aristas_recorridas": aristas}


def _killpg(pg, sig):
    try:
        os.killpg(pg, sig)
    except (ProcessLookupError, PermissionError):
        pass
