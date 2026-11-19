#!/usr/bin/env python
"""Estudio de agentes v2 — servidor FastAPI (DISENO.md §2, §6).

Arranque: python server.py [--host 0.0.0.0] [--port 8790]     (1 worker, sin --reload)
Todas las rutas de datos salen de STUDIO_HOME (por defecto, la carpeta de este archivo).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore

STUDIO = Path(__file__).resolve().parent
BASE = STUDIO.parent
sys.path.insert(0, str(STUDIO))

import f3  # noqa: E402
from assistants import AsistenteError, Assistants, contexto_efectivo, validar as validar_asistente  # noqa: E402
from compiler import ValidationError, build, cache_clear, cache_info, flat, validate  # noqa: E402
from db import DB, SETTINGS_SCHEMA, ConflictoVersion, Projects, Settings, export_clean  # noqa: E402
from engine import STRATEGIES, Engine, ThreadBusy, dumps  # noqa: E402
from modules import ConflictError, ModuloError, Registry  # noqa: E402

HOME = Path(os.environ.get("STUDIO_HOME") or STUDIO).resolve()
STATIC = STUDIO / "static"
MEDIA_EXT = {".mp4", ".webm", ".wav", ".mp3", ".png", ".jpg", ".jpeg", ".webp", ".txt"}
THREAD_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ApiError(Exception):
    def __init__(self, status, detail, code, **extra):
        super().__init__(detail)
        self.status, self.detail, self.code, self.extra = status, detail, code, extra


def err(status, detail, code, **extra):
    raise ApiError(status, detail, code, **extra)


# ------------------------------------------------------------------------------------------ estado global
class S:
    settings: Settings
    db: DB
    cp: AsyncSqliteSaver
    engine: Engine
    registry: Registry
    projects: Projects
    assistants: Assistants
    store: AsyncSqliteStore


def _prepare_home():
    for d in ("modules", "templates"):
        if not (HOME / d).exists() and (STUDIO / d).exists() and HOME != STUDIO:
            shutil.copytree(STUDIO / d, HOME / d, ignore=shutil.ignore_patterns(".historial", "__pycache__"))
    for d in ("data", "projects", "modules", "templates"):
        (HOME / d).mkdir(parents=True, exist_ok=True)


class EmbedError(RuntimeError):
    pass


async def _embed(textos: list[str]) -> list[list[float]]:
    """Embeddings para el Store [F2]: POST {ollama_url}/api/embed con settings.embed_model."""
    url = (S.settings.get("ollama_url") or "http://localhost:11434").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5)) as c:
            r = await c.post(url + "/api/embed", json={"model": S.settings.get("embed_model") or "nomic-embed-text",
                                                       "input": list(textos)})
            r.raise_for_status()
            return r.json()["embeddings"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise EmbedError(f"Ollama no pudo calcular los embeddings: {type(e).__name__}: {e}") from e


@asynccontextmanager
async def lifespan(app):
    _prepare_home()
    S.settings = Settings(HOME / "settings.json")
    print(f"Token del estudio: {S.settings['token']}", flush=True)
    S.db = DB(HOME / "data/studio.sqlite")
    await S.db.open()
    S.registry = Registry(HOME / "modules", HOME / "data/modsnap")
    S.projects = Projects(HOME / "projects", HOME / "templates")
    S.assistants = Assistants(S.db)
    async with AsyncSqliteSaver.from_conn_string(str(HOME / "data/checkpoints.sqlite")) as cp, \
            AsyncSqliteStore.from_conn_string(str(HOME / "data/store.sqlite"),
                                              index={"dims": 768, "embed": _embed, "fields": ["$"]}) as store:
        await cp.setup()
        await store.setup()
        S.cp = cp
        S.store = store
        S.engine = Engine(HOME, S.settings, S.db, cp, S.registry, S.projects, store=store)
        await S.engine.start()
        await S.db.exec("UPDATE experiments SET status='stopped', error='servidor reiniciado', t1=? WHERE status IN "
                        "('pending','running')", (time.time(),))
        await S.db.exec("UPDATE experiment_results SET status='stopped' WHERE status IN ('pending','running')")
        _CRON_TASK["loop"] = asyncio.create_task(_cron_loop())
        try:
            yield
        finally:
            _CRON_TASK.pop("loop").cancel()
            for tk in list(_EXP_TASKS.values()):
                tk.cancel()
            await S.engine.shutdown()
    await S.db.close()


app = FastAPI(title="Estudio de agentes", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.exception_handler(ApiError)
async def _api_error(request, e: ApiError):
    return JSONResponse({"detail": e.detail, "code": e.code, **e.extra}, status_code=e.status)


@app.exception_handler(Exception)
async def _any_error(request, e: Exception):
    return JSONResponse({"detail": f"{type(e).__name__}: {e}", "code": "internal"}, status_code=500)


@app.middleware("http")
async def auth(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        tok = request.headers.get("x-token") or request.cookies.get("studio_token")
        if not tok or tok != S.settings["token"]:
            return JSONResponse({"detail": "Falta el token o no es válido", "code": "unauthorized"}, status_code=401)
    resp = await call_next(request)
    if request.url.path in ("/", "/index.html") or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


async def body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:
        err(400, "El cuerpo debe ser JSON", "validation")
    return b if b is not None else {}


# ------------------------------------------------------------------------------------------ GUI
@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ------------------------------------------------------------------------------------------ módulos
def _usos() -> dict:
    out = {}
    for pr in S.projects.list():
        for n in (pr.get("nodes") or {}).values():
            out.setdefault(n.get("module"), set()).add(pr["slug"])
    return {k: sorted(v) for k, v in out.items()}


def _mod(file):
    try:
        info = S.registry.get(file)
    except ModuloError as e:
        err(400, str(e), "validation")
    if info is None:
        err(404, f"No existe el módulo {file}", "not_found")
    return info


@app.get("/api/modules")
async def modules_list():
    usos = _usos()
    return [{**m.as_dict(), "usos": usos.get(f, [])} for f, m in S.registry.all().items()]


@app.get("/api/modules/{file}/source")
async def module_source(file: str):
    m = _mod(file)
    return {"source": m.source, "sha": m.sha}


@app.put("/api/modules/{file}/source")
async def module_save(file: str, request: Request):
    b = await body(request)
    _mod(file)
    try:
        m = S.registry.save(file, b.get("source", ""), b.get("sha"))
    except ConflictError as e:
        err(409, str(e), "version_conflict", current=S.registry.get(file).sha)
    except ModuloError as e:
        err(422, str(e), "syntax", line=e.line)
    return {"meta": m.meta, "config": m.config, "sha": m.sha, "usos": _usos().get(file, [])}


@app.post("/api/modules", status_code=201)
async def module_create(request: Request):
    b = await body(request)
    if not (b.get("name") or "").strip():
        err(400, "Falta el nombre", "validation")
    if b.get("from"):
        _mod(b["from"])
    return {"file": S.registry.create(b["name"].strip(), b.get("from"))}


@app.delete("/api/modules/{file}", status_code=204)
async def module_delete(file: str):
    _mod(file)
    usos = _usos().get(file, [])
    if usos:
        err(409, f"El módulo se usa en {len(usos)} proyecto(s)", "module_in_use", usos=usos)
    S.registry.delete(file)
    return Response(status_code=204)


@app.get("/api/modules/{file}/history")
async def module_history(file: str):
    S.registry.path(file)
    return S.registry.history(file)


@app.post("/api/modules/{file}/restore")
async def module_restore(file: str, request: Request):
    b = await body(request)
    try:
        m = S.registry.restore(file, str(b.get("ts", "")))
    except FileNotFoundError:
        err(404, "No existe esa versión", "not_found")
    except ModuloError as e:
        err(422, str(e), "syntax", line=e.line)
    return {"meta": m.meta, "config": m.config, "sha": m.sha}


# ------------------------------------------------------------------------------------------ plantillas y proyectos
@app.get("/api/templates")
async def templates():
    return S.projects.templates_list()


def _proj(slug) -> dict:
    try:
        return S.projects.get(slug)
    except KeyError:
        err(404, f"No existe el proyecto {slug}", "not_found")


@app.get("/api/projects")
async def projects_list():
    th = {r["project"]: r["n"] for r in await S.db.all("SELECT project, COUNT(*) n FROM threads GROUP BY project")}
    last = {r["project"]: r["status"] for r in await S.db.all(
        "SELECT project, status FROM runs r WHERE t0 = (SELECT MAX(t0) FROM runs r2 WHERE r2.project = r.project)")}
    return [{"slug": p["slug"], "name": p.get("name"), "desc": p.get("desc", ""), "template": p.get("template"),
             "updated": p.get("updated"), "version": p.get("version"), "n_nodes": len(p.get("nodes") or {}),
             "n_threads": th.get(p["slug"], 0), "ultimo_status": last.get(p["slug"])} for p in S.projects.list()]


@app.post("/api/projects", status_code=201)
async def project_create(request: Request):
    b = await body(request)
    if not (b.get("name") or "").strip():
        err(400, "Falta el nombre", "validation")
    try:
        tpl = S.projects.template(b.get("template") or "vacio")
    except KeyError:
        err(400, f"No existe la plantilla {b.get('template')}", "validation")
    return S.projects.create_from(tpl, name=b["name"].strip(), desc=b.get("desc"))


@app.get("/api/projects/{slug}")
async def project_get(slug: str):
    return _proj(slug)


@app.put("/api/projects/{slug}")
async def project_put(slug: str, request: Request):
    b = await body(request)
    _proj(slug)
    if not isinstance(b.get("nodes"), dict) or not isinstance(b.get("edges"), list):
        err(400, "El proyecto debe tener nodes (objeto) y edges (lista)", "validation")
    try:
        return S.projects.put(slug, b)
    except ConflictoVersion as e:
        err(409, "El proyecto cambió desde que lo cargaste", "version_conflict", current=e.current)


@app.delete("/api/projects/{slug}", status_code=204)
async def project_delete(slug: str):
    _proj(slug)
    ths = await S.db.all("SELECT thread_id FROM threads WHERE project=?", (slug,))
    if any(S.engine.busy(t["thread_id"]) for t in ths):
        err(409, "Hay una ejecución activa en este proyecto", "thread_busy")
    for t in ths:
        await _delete_thread(t["thread_id"], slug)
    await S.assistants.delete_project(slug)
    cache_clear(HOME, slug)
    S.projects.delete(slug)
    return Response(status_code=204)


@app.get("/api/projects/{slug}/cache")
async def project_cache(slug: str):
    _proj(slug)
    return cache_info(HOME, slug)


@app.delete("/api/projects/{slug}/cache", status_code=204)
async def project_cache_clear(slug: str, node: str | None = None):
    _proj(slug)
    cache_clear(HOME, slug, node)
    return Response(status_code=204)


@app.post("/api/projects/{slug}/duplicate", status_code=201)
async def project_duplicate(slug: str, request: Request):
    b = await body(request)
    p = _proj(slug)
    return S.projects.create_from(p, name=(b.get("name") or f"{p.get('name')} (copia)").strip())


@app.post("/api/projects/import", status_code=201)
async def project_import(request: Request):
    b = await body(request)
    if not isinstance(b.get("nodes"), dict) or not isinstance(b.get("edges"), list):
        err(400, "JSON de proyecto no válido (faltan nodes/edges)", "validation")
    return S.projects.create_from(b, name=b.get("name"))


@app.get("/api/projects/{slug}/export")
async def project_export(slug: str, format: str = "json", check: int = 0):
    pr = _proj(slug)
    if format == "funcional" and check:            # [F3] para la GUI: ¿se puede? (siempre 200)
        from funcional_rt import motivos_no_funcional
        m = motivos_no_funcional(pr, S.registry)
        return {"ok": not m, "motivos": m}
    if format == "json":
        p = export_clean(pr)
        return Response(json.dumps(p, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{slug}.json"'})
    import exportar
    from funcional_rt import motivos_no_funcional
    v = validate(pr, S.registry, S.settings.values, loader=S.projects.get)
    if v["errores"]:
        err(400, "El proyecto no es válido: " + "; ".join(x["msg"] for x in v["errores"]), "validation")
    if format == "py":
        try:
            data = exportar.zip_proyecto(pr, S.registry, S.settings.values, S.projects.get, HOME)
        except KeyError as e:
            err(400, f"Falta el subproyecto {e}", "validation")
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="export_{slug}.zip"'})
    if format == "funcional":
        motivos = motivos_no_funcional(pr, S.registry)
        if motivos:
            err(400, "No se puede exportar a la API funcional: " + "; ".join(motivos), "unsupported", motivos=motivos)
        return Response(exportar.funcional_py(pr), media_type="text/x-python",
                        headers={"Content-Disposition": f'attachment; filename="funcional_{slug}.py"'})
    err(400, "format debe ser json, py o funcional", "validation")


@app.get("/api/projects/{slug}/validate")
async def project_validate(slug: str):
    v = validate(_proj(slug), S.registry, S.settings.values, loader=S.projects.get)
    return {"errores": v["errores"], "avisos": v["avisos"], "canales": v["canales"], "entradas": v["entradas"],
            "io": {k: {"inputs": a, "outputs": b} for k, (a, b) in v["io"].items()},
            "subgrafos": {k: {"proyecto": x["slug"], "nodos": list((x["project"].get("nodes") or {}))}
                          for k, x in v["subs"].items()}}


@app.get("/api/projects/{slug}/mermaid")
async def project_mermaid(slug: str, xray: int = 0):
    try:
        g = build(_proj(slug), env=S.engine.env(), load_modules=False).graph
    except ValidationError as e:
        err(400, "; ".join(x["msg"] for x in e.errores), "validation", errores=e.errores)
    return {"mermaid": g.get_graph(xray=bool(xray)).draw_mermaid()}


# ------------------------------------------------------------------------------------------ hilos
async def _thread(tid) -> dict:
    if not THREAD_RE.match(tid or ""):
        err(404, "Hilo no válido", "not_found")
    t = await S.db.one("SELECT * FROM threads WHERE thread_id=?", (tid,))
    if not t:
        err(404, f"No existe el hilo {tid}", "not_found")
    t["metadata"] = json.loads(t.get("metadata") or "{}")
    return t


async def _thread_out(t) -> dict:
    last = await S.db.one("SELECT run_id, status, motivo, error, t0, t1 FROM runs WHERE thread_id=? ORDER BY t0 DESC "
                          "LIMIT 1", (t["thread_id"],))
    salida = await S.db.one("SELECT id, kind, path, texto FROM outputs WHERE thread_id=? ORDER BY CASE kind WHEN "
                            "'video' THEN 0 ELSE 1 END, id DESC LIMIT 1", (t["thread_id"],))
    return {**t, "status": await S.engine.thread_status(t["thread_id"]), "ultimo_run": last, "salida": salida}


@app.get("/api/threads")
async def threads_list(project: str | None = None, ocultos: int = 0):
    if project:
        rows = await S.db.all("SELECT * FROM threads WHERE project=? ORDER BY updated DESC", (project,))
    else:
        rows = await S.db.all("SELECT * FROM threads ORDER BY updated DESC LIMIT 500")
    out = []
    for t in rows:
        t["metadata"] = json.loads(t.get("metadata") or "{}")
        if t["metadata"].get("oculto") and not ocultos:
            continue
        out.append(await _thread_out(t))
    return out


async def _new_thread(project, title=None) -> str:
    tid = str(uuid.uuid4())
    now = time.time()
    n = (await S.db.one("SELECT COUNT(*) n FROM threads WHERE project=?", (project,)))["n"]
    await S.db.exec("INSERT INTO threads(thread_id, project, title, metadata, created, updated) VALUES (?,?,?,?,?,?)",
                    (tid, project, title or f"Hilo {n + 1}", "{}", now, now))
    await _purge(project)
    return tid


async def _purge(project):
    keep = int(S.settings.get("hilos_conservar") or 100)
    rows = await S.db.all("SELECT thread_id FROM threads WHERE project=? ORDER BY updated DESC", (project,))
    for r in rows[keep:]:
        if not S.engine.busy(r["thread_id"]):
            await _delete_thread(r["thread_id"], project)


@app.post("/api/threads", status_code=201)
async def thread_create(request: Request):
    b = await body(request)
    _proj(b.get("project"))
    return {"thread_id": await _new_thread(b["project"], b.get("title"))}


@app.get("/api/threads/{tid}")
async def thread_get(tid: str):
    return await _thread_out(await _thread(tid))


@app.patch("/api/threads/{tid}")
async def thread_patch(tid: str, request: Request):
    b = await body(request)
    t = await _thread(tid)
    title = b.get("title", t["title"])
    meta = {**t["metadata"], **(b.get("metadata") or {})}
    await S.db.exec("UPDATE threads SET title=?, metadata=?, updated=? WHERE thread_id=?",
                    (title, json.dumps(meta, ensure_ascii=False), time.time(), tid))
    return await _thread_out(await _thread(tid))


async def _delete_thread(tid, project):
    await S.cp.adelete_thread(tid)
    await S.db.exec("DELETE FROM events WHERE thread_id=?", (tid,))
    await S.db.exec("DELETE FROM outputs WHERE thread_id=?", (tid,))
    await S.db.exec("DELETE FROM threads WHERE thread_id=?", (tid,))
    shutil.rmtree(HOME / "projects" / project / "threads" / tid, ignore_errors=True)


@app.delete("/api/threads/{tid}", status_code=204)
async def thread_delete(tid: str):
    t = await _thread(tid)
    if S.engine.busy(tid):
        err(409, "El hilo tiene una ejecución activa", "thread_busy")
    await _delete_thread(tid, t["project"])
    return Response(status_code=204)


@app.post("/api/threads/{tid}/copy", status_code=201)
async def thread_copy(tid: str):
    t = await _thread(tid)
    if S.engine.busy(tid):
        err(409, "El hilo tiene una ejecución activa", "thread_busy")
    new = str(uuid.uuid4())
    async with S.cp.lock:
        con = S.cp.conn
        await con.execute("BEGIN")
        try:
            await con.execute("INSERT INTO checkpoints SELECT ?, checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
                              "type, checkpoint, metadata FROM checkpoints WHERE thread_id=?", (new, tid))
            await con.execute("INSERT INTO writes SELECT ?, checkpoint_ns, checkpoint_id, task_id, task_path, idx, "
                              "channel, type, value FROM writes WHERE thread_id=?", (new, tid))
            await con.commit()
        except Exception:
            await con.rollback()
            raise
    now = time.time()
    meta = {**t["metadata"], "copia_de": tid}
    await S.db.exec("INSERT INTO threads(thread_id, project, title, metadata, created, updated) VALUES (?,?,?,?,?,?)",
                    (new, t["project"], f"{t['title']} (copia)", json.dumps(meta), now, now))
    src = HOME / "projects" / t["project"] / "threads" / tid
    if src.exists():
        await asyncio.to_thread(shutil.copytree, src, HOME / "projects" / t["project"] / "threads" / new)
    return {"thread_id": new}


async def _graph_for_thread(t):
    p = _proj(t["project"])
    keys = await S.engine.history_keys(t["thread_id"])
    try:
        return S.engine.graph_for(p, keys), p
    except ValidationError as e:
        err(400, "El proyecto no es válido: " + "; ".join(x["msg"] for x in e.errores), "validation",
            errores=e.errores)


def _cfg(tid, cid=None):
    c = {"configurable": {"thread_id": tid}}
    if cid:
        c["configurable"]["checkpoint_id"] = cid
        c["configurable"]["checkpoint_ns"] = ""
    return c


def _snap_out(s, version=None):
    conf = (s.config or {}).get("configurable", {})
    pconf = (s.parent_config or {}).get("configurable", {}) if s.parent_config else {}
    interrupts = []
    tasks = []
    for tk in s.tasks or ():
        its = [{"id": i.id, "value": i.value, "response_schema": getattr(i, "response_schema", None), "node": tk.name}
               for i in tk.interrupts]
        interrupts += its
        tasks.append({"id": tk.id, "name": tk.name, "error": str(tk.error) if tk.error else None,
                      "interrupts": its, "result": tk.result})
    return {"values": flat(s.values), "next": list(s.next or ()), "tasks": tasks, "interrupts": interrupts,
            "checkpoint_id": conf.get("checkpoint_id"), "parent_checkpoint_id": pconf.get("checkpoint_id"),
            "metadata": s.metadata or {}, "created_at": s.created_at, "version_proyecto": version}


@app.get("/api/threads/{tid}/state")
async def thread_state(tid: str, checkpoint_id: str | None = None):
    t = await _thread(tid)
    g, p = await _graph_for_thread(t)
    s = await g.aget_state(_cfg(tid, checkpoint_id))
    run = await S.db.one("SELECT project_version FROM runs WHERE thread_id=? ORDER BY t0 DESC LIMIT 1", (tid,))
    out = _snap_out(s, run["project_version"] if run else None)
    out["version_actual"] = p.get("version")
    return out


async def _history(g, tid, limit=200):
    snaps = [s async for s in g.aget_state_history(_cfg(tid), limit=limit)]
    by_id = {(s.config or {}).get("configurable", {}).get("checkpoint_id"): s for s in snaps}
    out = []
    for s in snaps:
        o = _snap_out(s)
        par = by_id.get(o["parent_checkpoint_id"])
        src = (s.metadata or {}).get("source")
        if src == "loop" and par is not None:
            writes = list(par.next or ())
        elif src == "input":
            writes = ["__input__"]
        elif src in ("update", "fork"):
            writes = ["__" + src + "__"]
        else:
            writes = []
        out.append({"checkpoint_id": o["checkpoint_id"], "parent_checkpoint_id": o["parent_checkpoint_id"],
                    "step": (s.metadata or {}).get("step"), "next": o["next"], "writes": writes,
                    "created_at": s.created_at, "source": src, "interrupts": len(o["interrupts"])})
    return out


@app.get("/api/threads/{tid}/history")
async def thread_history(tid: str, limit: int = 200, rolled_back: int = 0):
    t = await _thread(tid)
    g, _ = await _graph_for_thread(t)
    rb = set(t["metadata"].get("rolled_back") or [])
    out = await _history(g, tid, limit)
    for h in out:
        h["rolled_back"] = h["checkpoint_id"] in rb
    return out if rolled_back else [h for h in out if not h["rolled_back"]]


@app.post("/api/threads/{tid}/nodes/{node}/rerun", status_code=202)
async def node_rerun(tid: str, node: str, request: Request):
    """Re-ejecutar un nodo [F2]: replay desde el checkpoint más reciente de la rama actual cuyo next lo contiene."""
    b = await body(request)
    t = await _thread(tid)
    if S.engine.busy(tid):
        err(409, "El hilo ya tiene una ejecución activa", "thread_busy")
    g, p = await _graph_for_thread(t)
    if node not in (p.get("nodes") or {}):
        err(404, f"No existe el nodo {node}", "not_found")
    snaps = {(s.config or {}).get("configurable", {}).get("checkpoint_id"): s
             async for s in g.aget_state_history(_cfg(tid), limit=2000)}
    head = await g.aget_state(_cfg(tid))
    cur = (head.config or {}).get("configurable", {}).get("checkpoint_id") if head else None
    found = None
    while cur and cur in snaps:
        sn = snaps[cur]
        if node in (sn.next or ()):
            found = sn
            break
        cur = (sn.parent_config or {}).get("configurable", {}).get("checkpoint_id") if sn.parent_config else None
    if not found:
        err(409, f"El nodo {node} no aparece en el next de ningún checkpoint de la rama actual", "not_rerunnable")
    cid = found.config["configurable"]["checkpoint_id"]
    run = {"checkpoint_id": cid, "interrupt_before": b.get("interrupt_before"),
           "interrupt_after": b.get("interrupt_after"),
           "kind_hint": "fork" if (found.metadata or {}).get("source") in ("update", "fork") else "replay"}
    if b.get("assistant_id"):
        run["_assistant"] = await _assistant_for_run(t["project"], b["assistant_id"], b.get("assistant_version"))
    else:
        run["_assistant"] = {"context": contexto_efectivo(p, {}, S.settings.values), "node_overrides": {}}
    try:
        rc = await S.engine.create_run(t, run)
    except ThreadBusy:
        err(409, "El hilo ya tiene una ejecución activa", "thread_busy")
    return {"run_id": rc.run_id, "status": "pending", "checkpoint_id": cid}


def _msg_out(m) -> dict:
    if isinstance(m, dict):
        return {"id": m.get("id"), "role": m.get("role") or m.get("type"), "content": m.get("content")}
    role = {"human": "user", "ai": "assistant", "tool": "tool", "system": "system"}.get(getattr(m, "type", ""), "user")
    out = {"id": getattr(m, "id", None), "role": role, "content": m.content}
    if getattr(m, "tool_calls", None):
        out["tool_calls"] = [{"id": c.get("id"), "name": c.get("name"), "args": c.get("args")} for c in m.tool_calls]
    if getattr(m, "tool_call_id", None):
        out["tool_call_id"] = m.tool_call_id
    if getattr(m, "name", None):
        out["name"] = m.name
    return out


@app.get("/api/threads/{tid}/messages")
async def thread_messages(tid: str, checkpoint_id: str | None = None):
    t = await _thread(tid)
    g, p = await _graph_for_thread(t)
    v = validate(p, S.registry, S.settings.values, loader=S.projects.get)
    if v["canales"].get("messages") != "messages":
        err(404, "El proyecto no tiene una clave 'messages' con reducer messages", "not_found")
    s = await g.aget_state(_cfg(tid, checkpoint_id))
    return [_msg_out(m) for m in ((s.values or {}).get("messages") or [])]


@app.get("/api/tools")
async def tools_list():
    import herramientas
    return herramientas.discover(HOME / "modules" / "tools")


@app.post("/api/threads/{tid}/state")
async def thread_update_state(tid: str, request: Request):
    b = await body(request)
    t = await _thread(tid)
    if S.engine.busy(tid):
        err(409, "El hilo tiene una ejecución activa", "thread_busy")
    p = _proj(t["project"])
    vals = b.get("values")
    keys = set(await S.engine.history_keys(tid)) | set((vals or {}).keys())
    try:
        g = S.engine.graph_for(p, sorted(keys))
    except ValidationError as e:
        err(400, "El proyecto no es válido: " + "; ".join(x["msg"] for x in e.errores), "validation")
    cfg = _cfg(tid, b.get("checkpoint_id"))
    as_node = b.get("as_node")
    if as_node is None and vals is not None:
        as_node = await _writer_of(g, tid, b.get("checkpoint_id"), p)
    try:
        if as_node == "__copy__":
            nc = await g.aupdate_state(cfg, None, as_node="__copy__")
        else:
            nc = await g.aupdate_state(cfg, vals or {}, as_node=as_node)
    except Exception as e:
        err(400, f"No se pudo actualizar el estado: {type(e).__name__}: {e}", "validation")
    return {"checkpoint_id": nc["configurable"]["checkpoint_id"]}


async def _writer_of(g, tid, cid, project):
    """Nodo que escribió el checkpoint (los que estaban en next del padre); varios → el primero en orden de edges."""
    s = await g.aget_state(_cfg(tid, cid))
    if not s.parent_config:
        return None
    par = await g.aget_state(s.parent_config)
    cand = [n for n in (par.next or ()) if n in (project.get("nodes") or {})]
    if not cand:
        return None
    if len(cand) == 1:
        return cand[0]
    orden = []
    for e in project.get("edges") or []:
        for n in (e["src"] if isinstance(e["src"], list) else [e["src"]]) + [e["dst"]]:
            if n in cand and n not in orden:
                orden.append(n)
    return (orden or cand)[0]


@app.get("/api/threads/{tid}/view")
async def thread_view(tid: str):
    await _thread(tid)
    return await S.engine.view(tid)


@app.get("/api/threads/{tid}/events")
async def thread_events(tid: str, limit: int = 2000, run: str | None = None, types: str | None = None):
    """Eventos guardados del hilo (los más recientes), para reconstruir logs y la pestaña Eventos al recargar."""
    await _thread(tid)
    await S.engine.bus.flush()
    sql, args = "SELECT * FROM events WHERE thread_id=?", [tid]
    if run:
        sql += " AND run_id=?"
        args.append(run)
    if types:
        tl = [t for t in types.split(",") if t]
        sql += f" AND type IN ({','.join('?' * len(tl))})"
        args += tl
    rows = await S.db.all(sql + " ORDER BY id DESC LIMIT ?", (*args, min(limit, 10000)))
    return [{**r, "data": json.loads(r["data"] or "{}")} for r in reversed(rows)]


@app.post("/api/threads/{tid}/runs", status_code=202)
async def run_create(tid: str, request: Request):
    return await _lanzar(tid, await body(request))


async def _lanzar(tid: str, b: dict) -> dict:
    """Crea una ejecución (§6.1/§6.3). La usan la API, los crons y los experimentos [F3]."""
    t = await _thread(tid)
    if b.get("input") is not None and b.get("resume") is not None:
        err(400, "Como mucho uno de input / resume", "validation")
    strategy = b.get("multitask_strategy") or S.settings.get("multitask_strategy") or "reject"
    if strategy not in STRATEGIES:
        err(400, f"multitask_strategy debe ser una de: {', '.join(STRATEGIES)}", "validation")
    busy = S.engine.busy(tid)
    if busy and strategy == "reject":
        err(409, "El hilo ya tiene una ejecución activa", "thread_busy")
    if b.get("resume") is not None and not busy:
        st = await thread_state(tid)
        if not st["interrupts"]:
            err(409, "El hilo no está esperando una respuesta", "not_interrupted")
    if b.get("assistant_id"):
        b["_assistant"] = await _assistant_for_run(t["project"], b["assistant_id"], b.get("assistant_version"))
    else:
        b["_assistant"] = {"context": contexto_efectivo(_proj(t["project"]), {}, S.settings.values),
                           "node_overrides": {}}
    if b.get("checkpoint_id"):
        g, _ = await _graph_for_thread(t)
        s = await g.aget_state(_cfg(tid, b["checkpoint_id"]))
        if not s or not (s.config or {}).get("configurable", {}).get("checkpoint_id"):
            err(404, "No existe ese checkpoint", "not_found")
        b["kind_hint"] = "fork" if (s.metadata or {}).get("source") in ("update", "fork") else "replay"
    try:
        rc = await S.engine.create_run(t, b)
    except ThreadBusy:
        err(409, "El hilo ya tiene una ejecución activa", "thread_busy")
    except ValueError as e:
        err(400, str(e), "validation")
    out = {"run_id": rc.run_id, "status": "pending"}
    if rc.queued:
        out.update(queued=True, position=rc.position)
    if rc.replaces:
        out["replaces"] = rc.replaces
    return out


# ------------------------------------------------------------------------------------------ asistentes [F2]
async def _assistant(aid: str) -> dict:
    a = await S.assistants.get(aid)
    if not a:
        err(404, f"No existe el asistente {aid}", "not_found")
    return a


async def _assistant_for_run(project: str, aid: str, version=None) -> dict:
    a = await S.assistants.get(aid, version)
    if not a:
        err(404, f"No existe el asistente {aid}" + (f" (versión {version})" if version is not None else ""),
            "not_found")
    if a["project"] != project:
        err(400, "El asistente es de otro proyecto", "validation")
    pr = _proj(project)
    return {"assistant_id": aid, "version": a["version"], "node_overrides": a["node_overrides"],
            "context": contexto_efectivo(pr, a["context"], S.settings.values)}


def _validar_asistente(pr, context, overrides):
    try:
        return validar_asistente(pr, S.registry, context, overrides, S.settings.values)
    except AsistenteError as e:
        err(400, str(e), "validation")


@app.get("/api/projects/{slug}/assistants")
async def assistants_list(slug: str):
    _proj(slug)
    return await S.assistants.list(slug)


@app.post("/api/projects/{slug}/assistants", status_code=201)
async def assistant_create(slug: str, request: Request):
    b = await body(request)
    pr = _proj(slug)
    if not (b.get("name") or "").strip():
        err(400, "Falta el nombre", "validation")
    ctx, ov = _validar_asistente(pr, b.get("context"), b.get("node_overrides"))
    return await S.assistants.create(slug, b["name"].strip(), ctx, ov)


@app.get("/api/assistants")
async def assistants_all():
    """Todos los asistentes («agentes» en la GUI), de todos los proyectos, el más reciente primero."""
    nombres = {p["slug"]: p.get("name") for p in S.projects.list()}
    return [{**a, "project_name": nombres.get(a["project"])} for a in await S.assistants.list()
            if a["project"] in nombres]


@app.get("/api/assistants/{aid}")
async def assistant_get(aid: str):
    return await _assistant(aid)


@app.patch("/api/assistants/{aid}")
async def assistant_patch(aid: str, request: Request):
    b = await body(request)
    a = await _assistant(aid)
    ctx = ov = None
    if "context" in b or "node_overrides" in b:
        ctx, ov = _validar_asistente(_proj(a["project"]), b.get("context", a["context"]),
                                     b.get("node_overrides", a["node_overrides"]))
    name = b.get("name")
    if name is not None and not str(name).strip():
        err(400, "El nombre no puede estar vacío", "validation")
    return await S.assistants.patch(aid, name=name.strip() if name else None, context=ctx, node_overrides=ov)


@app.get("/api/assistants/{aid}/versions")
async def assistant_versions(aid: str):
    await _assistant(aid)
    return await S.assistants.versions(aid)


@app.post("/api/assistants/{aid}/activate/{version}")
async def assistant_activate(aid: str, version: int):
    await _assistant(aid)
    a = await S.assistants.activate(aid, version)
    if not a:
        err(404, f"No existe la versión {version}", "not_found")
    return a


@app.delete("/api/assistants/{aid}", status_code=204)
async def assistant_delete(aid: str):
    await _assistant(aid)
    await S.assistants.delete(aid)
    return Response(status_code=204)


# ------------------------------------------------------------------------------------------ memoria (Store) [F2]
def _ns(ns: str | None, obligatorio=True) -> tuple:
    segs = tuple((ns or "").split("/")) if ns else ()
    if (obligatorio and not segs) or any(not x for x in segs):
        err(400, "Namespace no válido: segmentos separados por '/', sin vacíos", "validation")
    return segs


def _item(it) -> dict:
    def ts(d):
        return d.timestamp() if hasattr(d, "timestamp") else d
    out = {"namespace": list(it.namespace), "key": it.key, "value": it.value, "created_at": ts(it.created_at),
           "updated_at": ts(it.updated_at)}
    if getattr(it, "score", None) is not None:
        out["score"] = it.score
    return out


def _es_embed(e: BaseException) -> bool:
    while e is not None:
        if isinstance(e, (EmbedError, httpx.HTTPError)):
            return True
        e = e.__cause__ or e.__context__
    return False


@app.get("/api/store/namespaces")
async def store_namespaces(prefix: str | None = None, max_depth: int | None = None, limit: int = 100):
    nss = await S.store.alist_namespaces(prefix=_ns(prefix, False) or None, max_depth=max_depth, limit=limit)
    return [list(n) for n in nss]


@app.get("/api/store/items")
async def store_items(ns: str, key: str | None = None, query: str | None = None, limit: int = 20, offset: int = 0):
    segs = _ns(ns)
    if key is not None:
        it = await S.store.aget(segs, key)
        if not it:
            err(404, f"No existe {ns}/{key}", "not_found")
        return _item(it)
    try:
        res = await S.store.asearch(segs, query=query or None, limit=limit, offset=offset)
    except Exception as e:
        if _es_embed(e):
            err(502, str(e), "ollama")
        raise
    out = [_item(r) for r in res]
    if not query:
        out.sort(key=lambda x: x["updated_at"] or 0, reverse=True)
    return out


@app.put("/api/store/items")
async def store_put(request: Request):
    b = await body(request)
    segs = b.get("namespace")
    if not isinstance(segs, list) or not segs or not all(isinstance(x, str) and x and "/" not in x for x in segs):
        err(400, "namespace debe ser una lista de cadenas no vacías y sin '/'", "validation")
    if not isinstance(b.get("key"), str) or not b["key"]:
        err(400, "Falta key", "validation")
    if not isinstance(b.get("value"), dict):
        err(400, "value debe ser un objeto", "validation")
    try:
        await S.store.aput(tuple(segs), b["key"], b["value"], index=None if b.get("index", True) else False)
    except Exception as e:
        if _es_embed(e):
            err(502, str(e), "ollama")
        raise
    return _item(await S.store.aget(tuple(segs), b["key"]))


@app.delete("/api/store/items", status_code=204)
async def store_delete(ns: str, key: str):
    await S.store.adelete(_ns(ns), key)
    return Response(status_code=204)


# ------------------------------------------------------------------------------------------ ejecuciones
def _run_out(r):
    if not r:
        return r
    for k in ("input", "module_shas", "interrupt_before", "interrupt_after", "pgids"):
        try:
            r[k] = json.loads(r[k]) if r.get(k) else None
        except ValueError:
            pass
    return r


@app.get("/api/runs")
async def runs_list(thread: str | None = None, project: str | None = None, status: str | None = None,
                    since: float | None = None, limit: int = 50):
    sql, args = "SELECT * FROM runs WHERE 1=1", []
    if thread:
        sql += " AND thread_id=?"
        args.append(thread)
    if project:
        sql += " AND project=?"
        args.append(project)
    if status:
        sts = [x for x in status.split(",") if x]
        sql += f" AND status IN ({','.join('?' * len(sts))})"
        args += sts
    if since:
        sql += " AND t0>=?"
        args.append(since)
    rows = await S.db.all(sql + " ORDER BY t0 DESC LIMIT ?", (*args, min(limit, 1000)))
    return [_run_out(r) for r in rows]


@app.get("/api/runs/{run_id}/trace")
async def run_trace(run_id: str):
    """Trazas [F2]: un span por intento de cada nodo, calculado de la tabla events (§6.3)."""
    r = await S.db.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
    if not r:
        err(404, "No existe la ejecución", "not_found")
    await S.engine.bus.flush()
    evs = await S.db.all("SELECT type, node, task_id, ns, data, ts FROM events WHERE run_id=? AND type IN "
                         "('node.start','node.end','token') ORDER BY id", (run_id,))
    spans, abiertos, tokens = [], {}, 0
    for ev in evs:
        d = json.loads(ev["data"] or "{}")
        if ev["type"] == "token":
            tokens += 1
            continue
        clave = (ev["ns"] or "", ev["task_id"] or f"cache:{ev['node']}", ev["node"])
        if ev["type"] == "node.start":
            if "esperando_gpu" in d:
                sp = abiertos.get(clave)
                if sp is not None:
                    if d["esperando_gpu"]:
                        sp["_espero"] = True
                    elif sp.get("_espero"):
                        sp["t_gpu"] = ev["ts"]
                continue
            sp = {"task_id": ev["task_id"], "node": ev["node"], "label": d.get("label") or ev["node"],
                  "ns": ev["ns"] or "", "attempt": d.get("attempt", 1), "t0": ev["ts"], "t_gpu": None, "t1": None,
                  "status": "running", "error": None, "cached": bool(d.get("cached"))}
            abiertos[clave] = sp
            spans.append(sp)
        elif ev["type"] == "node.end":
            if d.get("handler"):
                continue
            sp = abiertos.pop(clave, None)
            if sp is None:
                continue
            sp["t1"] = ev["ts"]
            sp["status"] = d.get("status")
            sp["error"] = d.get("error")
            sp["cached"] = sp["cached"] or d.get("status") == "cached"
    for sp in spans:
        sp.pop("_espero", None)
        if sp["t1"] is None and r["status"] not in ("pending", "running", "pausing"):
            sp["status"] = "stopped" if r["status"] in ("stopped", "drained") else (sp["status"] or "stopped")
    t_end = r["t1"] or time.time()
    totals = {"duration": (t_end - r["t0"]) if r["t0"] else None, "n_spans": len(spans),
              "errores": sum(1 for x in spans if x["status"] == "error"),
              "reintentos": sum(1 for x in spans if (x["attempt"] or 1) > 1),
              "espera_gpu": round(sum((x["t_gpu"] - x["t0"]) for x in spans if x["t_gpu"]), 3), "tokens": tokens}
    return {"run": _run_out(r), "spans": spans, "totals": totals}


@app.get("/api/runs/active")
async def runs_active():
    return [{"run_id": rc.run_id, "thread_id": rc.thread_id, "project": rc.project, "status": rc.status}
            for rc in S.engine.active.values()]


@app.get("/api/runs/{run_id}")
async def run_get(run_id: str):
    r = await S.db.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
    if not r:
        err(404, "No existe la ejecución", "not_found")
    return _run_out(r)


@app.post("/api/runs/{run_id}/pause", status_code=202)
async def run_pause(run_id: str):
    if not await S.engine.pause(run_id):
        err(409, "La ejecución no está en marcha", "not_running")
    return {"status": "pausing"}


@app.post("/api/runs/{run_id}/stop", status_code=202)
async def run_stop(run_id: str):
    if await S.engine.cancel_queued(run_id):
        return {"status": "stopped"}
    if run_id not in S.engine.by_run or S.engine.by_run[run_id].status not in ("pending", "running", "pausing"):
        err(409, "La ejecución no está en marcha", "not_running")
    asyncio.create_task(S.engine.stop(run_id))
    return {"status": "stopping"}


# ------------------------------------------------------------------------------------------ eventos SSE
@app.get("/api/events")
async def events(request: Request, thread: str | None = None, after: int | None = None):
    last = request.headers.get("last-event-id")
    after = int(last) if last and last.isdigit() else (after or 0)
    q = S.engine.bus.subscribe(thread)

    async def gen():
        sent = after
        try:
            await S.engine.bus.flush()
            if after:
                sql = "SELECT * FROM events WHERE id>? " + ("AND thread_id=? " if thread else "") + "ORDER BY id LIMIT 5000"
                for r in await S.db.all(sql, (after, thread) if thread else (after,)):
                    ev = {**r, "data": json.loads(r["data"] or "{}")}
                    sent = ev["id"]
                    yield _sse(ev)
            yield ": conectado\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if ev.get("id") is not None:
                    if ev["id"] <= sent:
                        continue
                    sent = ev["id"]
                yield _sse(ev)
        finally:
            S.engine.bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(ev) -> str:
    s = ""
    if ev.get("id") is not None:
        s += f"id: {ev['id']}\n"
    return s + f"event: {ev['type']}\ndata: {dumps(ev)}\n\n"


# ------------------------------------------------------------------------------------------ medios
def _safe_media(path: str) -> Path:
    try:
        p = Path(path).resolve()
    except Exception:
        err(400, "Ruta no válida", "validation")
    if not p.is_relative_to(BASE) or p.suffix.lower() not in MEDIA_EXT:
        err(403, "Ruta no permitida", "forbidden")
    if any(x in p.parts for x in ("data",)) and p.is_relative_to(HOME / "data"):
        err(403, "Ruta no permitida", "forbidden")
    if not p.is_file():
        err(404, "No existe el fichero", "not_found")
    return p


@app.get("/api/media")
async def media(path: str):
    return FileResponse(_safe_media(path))


def _retratos_dir() -> Path:
    return Path(S.settings["video"].get("retratos") or BASE)


@app.get("/api/images")
async def images():
    d = _retratos_dir()
    out = []
    if d.is_dir():
        for p in sorted(d.iterdir()):
            if p.suffix.lower() in (".png", ".jpg", ".jpeg") and p.is_file():
                out.append({"path": str(p), "name": p.name, "size": p.stat().st_size})
    return out


@app.post("/api/images")
async def image_upload(file: UploadFile = File(...)):
    name = Path(file.filename or "imagen.png").name
    ext = Path(name).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        err(400, "Solo PNG o JPG", "validation")
    data = await file.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        err(400, "La imagen supera 20 MB", "validation")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem)[:60] or "imagen"
    d = _retratos_dir()
    d.mkdir(parents=True, exist_ok=True)
    dst, n = d / f"{stem}{ext}", 2
    while dst.exists():
        dst = d / f"{stem}_{n}{ext}"
        n += 1
    dst.write_bytes(data)
    return {"path": str(dst)}


@app.get("/api/outputs")
async def outputs(kind: str | None = None, thread: str | None = None, limit: int = 200):
    sql, args = "SELECT o.*, t.title AS thread_title FROM outputs o LEFT JOIN threads t USING(thread_id) WHERE 1=1", []
    if kind:
        sql += " AND o.kind=?"
        args.append(kind)
    if thread:
        sql += " AND o.thread_id=?"
        args.append(thread)
    rows = await S.db.all(sql + " ORDER BY o.id DESC LIMIT ?", (*args, limit))
    for r in rows:
        r["legacy"] = False
        if r["kind"] == "video" and not r.get("texto"):
            tx = await S.db.one("SELECT texto FROM outputs WHERE run_id=? AND kind='text' ORDER BY id DESC LIMIT 1",
                                (r["run_id"],))
            r["texto"] = tx["texto"] if tx else None
    if kind in (None, "video") and not thread:
        for sj in sorted((HOME / "legacy" / "runs").glob("*/state.json"), reverse=True):
            vid = sj.parent / "final.mp4"
            if not vid.exists():
                continue
            try:
                st = json.loads(sj.read_text(encoding="utf-8"))
            except ValueError:
                st = {}
            stv = st.get("state", st) if isinstance(st, dict) else {}
            rows.append({"id": f"v1-{sj.parent.name}", "kind": "video", "path": str(vid), "legacy": True,
                         "texto": stv.get("texto"), "project": "v1", "thread_id": None, "node": "montaje",
                         "ts": vid.stat().st_mtime, "clave": "video_final", "thread_title": sj.parent.name})
    return rows


@app.delete("/api/outputs/{oid}", status_code=204)
async def output_delete(oid: str):
    if oid.startswith("v1-"):  # vídeo heredado de v1: se borra su carpeta de ejecución completa
        nombre = oid[3:]
        raiz = (HOME / "legacy" / "runs").resolve()
        d = (raiz / nombre).resolve()
        if not re.fullmatch(r"[\w.-]+", nombre) or d.parent != raiz or not (d / "final.mp4").is_file():
            err(404, "No existe la salida", "not_found")
        await asyncio.to_thread(shutil.rmtree, d)
        return Response(status_code=204)
    if not oid.isdigit():
        err(404, "No existe la salida", "not_found")
    r = await S.db.one("SELECT * FROM outputs WHERE id=?", (int(oid),))
    if not r:
        err(404, "No existe la salida", "not_found")
    if r.get("path"):
        p = Path(r["path"]).resolve()
        if p.is_relative_to(BASE) and p.is_file() and p.suffix.lower() in MEDIA_EXT:
            p.unlink()
    await S.db.exec("DELETE FROM outputs WHERE id=?", (int(oid),))
    return Response(status_code=204)


# ------------------------------------------------------------------------------------------ ajustes y sistema
@app.get("/api/settings")
async def settings_get():
    return {"schema": SETTINGS_SCHEMA, "values": S.settings.values}


@app.put("/api/settings")
async def settings_put(request: Request):
    b = await body(request)
    vals = b.get("values", b)
    try:
        S.settings.update(vals)
    except (ValueError, TypeError) as e:
        err(400, str(e), "validation")
    return {"schema": SETTINGS_SCHEMA, "values": S.settings.values}


def _gpu():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,temperature.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
        name, used, total, temp = [x.strip() for x in out.splitlines()[0].split(",")]
        return {"name": name, "mem_used": int(used), "mem_total": int(total), "temp": int(temp)}
    except Exception:
        return None


def _ram():
    info = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, v = line.split(":", 1)
        info[k] = int(v.split()[0]) * 1024
    return {"total": info.get("MemTotal"), "disponible": info.get("MemAvailable")}


async def _ollama(path, method="GET", **kw):
    url = S.settings.get("ollama_url", "http://localhost:11434").rstrip("/") + path
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.request(method, url, **kw)
        r.raise_for_status()
        return r.json()


@app.get("/api/system")
async def system():
    import langgraph.version as lgv
    gpu = await asyncio.to_thread(_gpu)
    du = shutil.disk_usage(HOME)
    try:
        ps = await _ollama("/api/ps")
        oll = {"ok": True, "cargados": [m["name"] for m in ps.get("models", [])]}
    except Exception:
        oll = {"ok": False, "cargados": []}
    return {"gpu": gpu, "ram": _ram(), "disco": {"total": du.total, "libre": du.free},
            "versiones": {"python": sys.version.split()[0], "langgraph": getattr(lgv, "__version__", "?")},
            "ollama": oll, "activas": len(S.engine.active), "token_corto": len(S.settings["token"]) < 16,
            "nombre": S.settings.get("nombre")}


@app.get("/api/ollama/models")
async def ollama_models():
    try:
        tags = await _ollama("/api/tags")
    except Exception:
        return []
    return sorted(m["name"] for m in tags.get("models", []))


# ------------------------------------------------------------------------------------------ vídeo rápido
@app.post("/api/quick-video", status_code=201)
async def quick_video(request: Request):
    b = await body(request)
    personaje = (b.get("personaje") or "").strip()
    if not personaje:
        err(400, "Falta el personaje", "validation")
    origen = b.get("origen_texto") or "texto"
    if origen not in ("texto", "tema"):
        err(400, "origen_texto debe ser texto o tema", "validation")
    if origen == "texto" and not (b.get("texto") or "").strip():
        err(400, "Falta el texto", "validation")
    if origen == "tema" and not (b.get("tema") or "").strip():
        err(400, "Falta el tema", "validation")
    tpl = S.projects.template("clip_personaje")
    n = tpl["nodes"]
    v = n["entrada"]["config"]["valores"]
    v.update({"origen_texto": origen, "personaje": personaje, "texto": (b.get("texto") or "").strip(),
              "tema": (b.get("tema") or "").strip(), "nombre": b.get("nombre") or personaje.upper(),
              "subtitulo": b.get("subtitulo") or ""})
    if b.get("prompt_imagen"):
        n["retrato"]["config"].update({"modo": "sdxl", "prompt": b["prompt_imagen"]})
    elif b.get("imagen"):
        n["retrato"]["config"].update({"modo": "existente", "imagen": b["imagen"]})
    if b.get("voz"):
        n["voz"]["config"]["voz"] = b["voz"]
    for k in ("tam", "mejora"):
        if k in b:
            n["lipsync"]["config"][k] = b[k]
    pr = S.projects.create_from(tpl, name=(b.get("nombre_proyecto") or f"Clip · {personaje}").strip())
    tid = await _new_thread(pr["slug"], personaje)
    out = {"slug": pr["slug"], "thread_id": tid}
    if b.get("iniciar"):
        rc = await S.engine.create_run(await _thread(tid), {"input": {}})
        out["run_id"] = rc.run_id
    return out


# ------------------------------------------------------------------------------------------ datasets [F3]
def _ds_out(d):
    return d


def _ej_out(e):
    for k in ("input", "expected", "metadata"):
        e[k] = json.loads(e[k]) if e.get(k) else ({} if k == "metadata" else None)
    return e


async def _dataset(did) -> dict:
    d = await S.db.one("SELECT d.*, (SELECT COUNT(*) FROM examples e WHERE e.dataset_id=d.dataset_id) n_examples, "
                       "(SELECT COUNT(*) FROM experiments x WHERE x.dataset_id=d.dataset_id) n_experiments "
                       "FROM datasets d WHERE dataset_id=?", (did,))
    if not d:
        err(404, f"No existe el dataset {did}", "not_found")
    return d


async def _ds_nombre_libre(name, excepto=None):
    name = (name or "").strip()
    if not name:
        err(400, "El nombre no puede estar vacío", "validation")
    r = await S.db.one("SELECT dataset_id FROM datasets WHERE name=? AND dataset_id IS NOT ?", (name, excepto))
    if r:
        err(400, f"Ya existe un dataset llamado «{name}»", "validation")
    return name


def _ds_project(p):
    if p:
        _proj(p)
    return p or None


@app.get("/api/datasets")
async def datasets_list():
    return await S.db.all("SELECT d.*, (SELECT COUNT(*) FROM examples e WHERE e.dataset_id=d.dataset_id) n_examples, "
                          "(SELECT COUNT(*) FROM experiments x WHERE x.dataset_id=d.dataset_id) n_experiments "
                          "FROM datasets d ORDER BY created DESC")


async def _crear_dataset(name, desc="", project=None) -> str:
    did = "ds-" + uuid.uuid4().hex[:12]
    await S.db.exec("INSERT INTO datasets(dataset_id, name, desc, project, created) VALUES (?,?,?,?,?)",
                    (did, await _ds_nombre_libre(name), desc or "", _ds_project(project), time.time()))
    return did


@app.post("/api/datasets", status_code=201)
async def dataset_create(request: Request):
    b = await body(request)
    return await _dataset(await _crear_dataset(b.get("name"), b.get("desc"), b.get("project")))


@app.get("/api/datasets/{did}")
async def dataset_get(did: str):
    return await _dataset(did)


@app.patch("/api/datasets/{did}")
async def dataset_patch(did: str, request: Request):
    b = await body(request)
    d = await _dataset(did)
    name = await _ds_nombre_libre(b["name"], did) if "name" in b else d["name"]
    desc = b.get("desc", d["desc"])
    project = _ds_project(b["project"]) if "project" in b else d["project"]
    await S.db.exec("UPDATE datasets SET name=?, desc=?, project=? WHERE dataset_id=?", (name, desc, project, did))
    return await _dataset(did)


@app.delete("/api/datasets/{did}", status_code=204)
async def dataset_delete(did: str):
    await _dataset(did)
    if await S.db.one("SELECT 1 FROM experiments WHERE dataset_id=? AND status IN ('pending','running')", (did,)):
        err(409, "Hay un experimento en marcha sobre este dataset", "experiment_running")
    await S.db.exec("DELETE FROM datasets WHERE dataset_id=?", (did,))
    return Response(status_code=204)


@app.get("/api/datasets/{did}/examples")
async def examples_list(did: str, limit: int = 1000, offset: int = 0):
    await _dataset(did)
    rows = await S.db.all("SELECT * FROM examples WHERE dataset_id=? ORDER BY created, rowid LIMIT ? OFFSET ?",
                          (did, min(limit, 10000), max(offset, 0)))
    return [_ej_out(r) for r in rows]


def _ej_valido(e) -> dict:
    if not isinstance(e, dict) or not isinstance(e.get("input"), dict):
        err(400, "Cada ejemplo necesita input (objeto)", "validation")
    if e.get("expected") is not None and not isinstance(e["expected"], dict):
        err(400, "expected debe ser un objeto o null", "validation")
    if e.get("metadata") is not None and not isinstance(e["metadata"], dict):
        err(400, "metadata debe ser un objeto", "validation")
    return e


async def _insert_ejemplos(did, ejemplos, source_run=None) -> list[dict]:
    ids, now = [], time.time()
    rows = []
    for i, e in enumerate(ejemplos):
        eid = "ex-" + uuid.uuid4().hex[:12]
        ids.append(eid)
        rows.append((eid, did, dumps(e["input"]), dumps(e.get("expected")) if e.get("expected") is not None else None,
                     dumps(e.get("metadata") or {}), source_run, now + i * 1e-6))
    await S.db.execmany("INSERT INTO examples(example_id, dataset_id, input, expected, metadata, source_run, created) "
                        "VALUES (?,?,?,?,?,?,?)", rows)
    out = []
    for eid in ids:
        out.append(_ej_out(await S.db.one("SELECT * FROM examples WHERE example_id=?", (eid,))))
    return out


@app.post("/api/datasets/{did}/examples", status_code=201)
async def examples_add(did: str, request: Request):
    b = await body(request)
    await _dataset(did)
    lista = b if isinstance(b, list) else [b]
    if not lista:
        err(400, "Lista vacía", "validation")
    return await _insert_ejemplos(did, [_ej_valido(e) for e in lista])


@app.post("/api/datasets/{did}/examples/from-run", status_code=201)
async def examples_from_run(did: str, request: Request):
    b = await body(request)
    await _dataset(did)
    r = _run_out(await S.db.one("SELECT * FROM runs WHERE run_id=?", (b.get("run_id") or "",)))
    if not r:
        err(404, f"No existe la ejecución {b.get('run_id')}", "not_found")
    if r["kind"] != "input" or not isinstance(r.get("input"), dict):
        err(400, "Esa ejecución no tuvo entrada (solo sirven las ejecuciones «Iniciar»)", "validation")
    expected = b.get("expected")
    if expected is None:
        st = await thread_state(r["thread_id"])
        expected = f3.salida(_proj(r["project"]), st["values"])
    return (await _insert_ejemplos(did, [_ej_valido({"input": r["input"], "expected": expected,
                                                     "metadata": {"project": r["project"]}})], r["run_id"]))[0]


async def _ejemplo(eid) -> dict:
    e = await S.db.one("SELECT * FROM examples WHERE example_id=?", (eid,))
    if not e:
        err(404, f"No existe el ejemplo {eid}", "not_found")
    return _ej_out(e)


@app.patch("/api/examples/{eid}")
async def example_patch(eid: str, request: Request):
    b = await body(request)
    e = await _ejemplo(eid)
    nuevo = _ej_valido({"input": b.get("input", e["input"]), "expected": b.get("expected", e["expected"]),
                        "metadata": b.get("metadata", e["metadata"])})
    await S.db.exec("UPDATE examples SET input=?, expected=?, metadata=? WHERE example_id=?",
                    (dumps(nuevo["input"]), dumps(nuevo["expected"]) if nuevo["expected"] is not None else None,
                     dumps(nuevo["metadata"] or {}), eid))
    return await _ejemplo(eid)


@app.delete("/api/examples/{eid}", status_code=204)
async def example_delete(eid: str):
    await _ejemplo(eid)
    await S.db.exec("DELETE FROM examples WHERE example_id=?", (eid,))
    return Response(status_code=204)


@app.get("/api/datasets/{did}/export")
async def dataset_export(did: str):
    d = await _dataset(did)
    ejs = await examples_list(did, limit=10000)
    fn = re.sub(r"[^A-Za-z0-9_-]+", "_", d["name"]) or "dataset"
    return Response(f3.jsonl_dump(d, ejs), media_type="application/x-ndjson",
                    headers={"Content-Disposition": f'attachment; filename="{fn}.jsonl"'})


@app.post("/api/datasets/import", status_code=201)
async def dataset_import(request: Request, name: str | None = None):
    texto = (await request.body()).decode("utf-8", "replace")
    try:
        cab, ejs = f3.jsonl_load(texto)
    except ValueError as e:
        err(400, str(e), "validation")
    nombre = (name or cab.get("name") or "Importado").strip()
    base, i = nombre, 2
    while await S.db.one("SELECT 1 FROM datasets WHERE name=?", (nombre,)):
        nombre, i = f"{base} ({i})", i + 1
    did = await _crear_dataset(nombre, cab.get("desc") or "")
    if ejs:
        await _insert_ejemplos(did, ejs)
    return await _dataset(did)


# ------------------------------------------------------------------------------------------ playground [F3]
@app.post("/api/projects/{slug}/nodes/{node}/playground")
async def node_playground(slug: str, node: str, request: Request):
    import tempfile

    from compiler import resolve_cfg
    b = await body(request)
    pr = _proj(slug)
    n = (pr.get("nodes") or {}).get(node)
    if not n:
        err(404, f"No existe el nodo {node}", "not_found")
    info = _mod(n.get("module") or "")
    meta = info.meta or {}
    if info.error or not meta.get("llm") or meta.get("exec") != "local" or meta.get("kind") not in ("task", "router"):
        err(400, "El playground solo admite módulos LLM locales de tipo task o router (llm_ollama, router_llm, "
                 "chat_ollama…)", "unsupported")
    vs = b.get("variantes") or [{}]
    if not isinstance(vs, list) or not 1 <= len(vs) <= 4 or not all(isinstance(v, dict) for v in vs):
        err(400, "variantes debe ser una lista de 1 a 4 objetos", "validation")
    state = b.get("state")
    if state is None and b.get("thread_id"):
        t = await _thread(b["thread_id"])
        if t["project"] != slug:
            err(400, "El hilo es de otro proyecto", "validation")
        state = (await thread_state(b["thread_id"], b.get("checkpoint_id")))["values"]
    state = state or {}
    if not isinstance(state, dict):
        err(400, "state debe ser un objeto", "validation")
    ov, context = {}, contexto_efectivo(pr, {}, S.settings.values)
    if b.get("assistant_id"):
        a = await _assistant_for_run(slug, b["assistant_id"], b.get("assistant_version"))
        ov, context = (a["node_overrides"] or {}).get(node) or {}, a["context"]
    claves = {f["key"] for f in info.config}
    for v in vs:
        malas = set(v) - claves
        if malas:
            err(400, f"Campos desconocidos en la variante: {', '.join(sorted(malas))}", "validation")
    cfgs = [resolve_cfg(info.config, n.get("config") or {}, {**ov, **v}, S.settings.values) for v in vs]
    _, mod = S.registry.load(info.file)
    with tempfile.TemporaryDirectory(prefix="playground-", dir=str(HOME / "data")) as d:
        try:
            res = await f3.playground(mod, node, state, cfgs, S.settings.values, context, S.store, d)
        except f3.OllamaCaido as e:
            err(502, f"Ollama no responde: {e}", "ollama")
    return JSONResponse(json.loads(dumps({"resultados": res})))


# ------------------------------------------------------------------------------------------ experimentos [F3]
FIN_RUN = ("success", "error", "stopped", "interrupted", "drained")
_EXP_TASKS: dict[str, asyncio.Task] = {}
_EXP_STOP: set[str] = set()


def _exp_emit(eid, status, hechos=None, n=None):
    S.engine.bus.emit(None, None, "experiment.status",
                      data={"experiment_id": eid, "status": status, "hechos": hechos, "n": n})


async def _experimento(eid) -> dict:
    x = await S.db.one("SELECT * FROM experiments WHERE experiment_id=?", (eid,))
    if not x:
        err(404, f"No existe el experimento {eid}", "not_found")
    x["evaluators"] = json.loads(x.get("evaluators") or "[]")
    res = await S.db.all("SELECT status, scores FROM experiment_results WHERE experiment_id=?", (eid,))
    x["n"] = len(res)
    x["hechos"] = sum(1 for r in res if r["status"] not in ("pending", "running"))
    x["resumen"] = f3.resumen([json.loads(r["scores"] or "{}") for r in res], x["evaluators"])
    return x


async def _resultados(eid) -> list[dict]:
    rows = await S.db.all(
        "SELECT r.*, e.input, e.expected FROM experiment_results r LEFT JOIN examples e ON e.example_id=r.example_id "
        "WHERE r.experiment_id=? ORDER BY e.created, e.rowid", (eid,))
    for r in rows:
        for k in ("input", "expected", "output", "scores"):
            r[k] = json.loads(r[k]) if r.get(k) else None
    return rows


async def _esperar_run(run_id) -> dict:
    while True:
        r = await S.db.one("SELECT status, t0, t1, error FROM runs WHERE run_id=?", (run_id,))
        if r and r["status"] in FIN_RUN and not any(rc.run_id == run_id for rc in S.engine.active.values()):
            return r
        await asyncio.sleep(0.25)


async def _exp_ejemplo(x, ej, asst, sem):
    eid = x["experiment_id"]
    async with sem:
        if eid in _EXP_STOP:
            return
        t0 = time.time()
        await S.db.exec("UPDATE experiment_results SET status='running' WHERE experiment_id=? AND example_id=?",
                        (eid, ej["example_id"]))
        tid = await _new_thread(x["project"], f"{x['name']} · {ej['example_id']}")
        await S.db.exec("UPDATE threads SET metadata=? WHERE thread_id=?",
                        (dumps({"experiment": eid, "example": ej["example_id"], "oculto": True}), tid))
        upd = "UPDATE experiment_results SET {} WHERE experiment_id=? AND example_id=?"
        try:
            out = await _lanzar(tid, {"input": ej["input"], "_experiment_id": eid, "multitask_strategy": "reject",
                                      **({"assistant_id": x["assistant_id"], "assistant_version": x["assistant_version"]}
                                         if x["assistant_id"] else {})})
        except ApiError as e:
            await S.db.exec(upd.format("thread_id=?, status='error', error=?, duration=?"),
                            (tid, e.detail, time.time() - t0, eid, ej["example_id"]))
            return
        await S.db.exec(upd.format("thread_id=?, run_id=?"), (tid, out["run_id"], eid, ej["example_id"]))
        r = await _esperar_run(out["run_id"])
        output, scores = None, None
        try:
            output = f3.salida(_proj(x["project"]), (await thread_state(tid))["values"])
        except ApiError:
            pass
        if r["status"] == "success":
            scores = await f3.puntuar(x["evaluators"], output, ej["expected"], ej["input"], S.settings.values)
        await S.db.exec(upd.format("status=?, output=?, scores=?, duration=?, error=?"),
                        (r["status"], dumps(output), dumps(scores) if scores is not None else None,
                         (r["t1"] or time.time()) - (r["t0"] or t0), r["error"], eid, ej["example_id"]))
        xx = await _experimento(eid)
        _exp_emit(eid, xx["status"], xx["hechos"], xx["n"])


async def _exp_lanzar(eid):
    x = await _experimento(eid)
    try:
        await S.db.exec("UPDATE experiments SET status='running', t0=? WHERE experiment_id=?", (time.time(), eid))
        _exp_emit(eid, "running", 0, x["n"])
        ejs = {e["example_id"]: e for e in await examples_list(x["dataset_id"], limit=10000)}
        res = await S.db.all("SELECT example_id FROM experiment_results WHERE experiment_id=?", (eid,))
        sem = asyncio.Semaphore(max(1, min(4, int(x["concurrency"] or 1))))
        await asyncio.gather(*[_exp_ejemplo(x, ejs[r["example_id"]], None, sem) for r in res
                               if r["example_id"] in ejs])
        st = "stopped" if eid in _EXP_STOP else "success"
        await S.db.exec("UPDATE experiment_results SET status='stopped' WHERE experiment_id=? AND status IN "
                        "('pending','running')", (eid,))
        await S.db.exec("UPDATE experiments SET status=?, t1=? WHERE experiment_id=?", (st, time.time(), eid))
    except asyncio.CancelledError:
        await S.db.exec("UPDATE experiments SET status='stopped', t1=?, error=? WHERE experiment_id=?",
                        (time.time(), "servidor detenido", eid))
        raise
    except Exception as e:  # noqa: BLE001
        await S.db.exec("UPDATE experiments SET status='error', t1=?, error=? WHERE experiment_id=?",
                        (time.time(), f"{type(e).__name__}: {e}", eid))
    finally:
        _EXP_TASKS.pop(eid, None)
        _EXP_STOP.discard(eid)
        xx = await _experimento(eid)
        _exp_emit(eid, xx["status"], xx["hechos"], xx["n"])


@app.post("/api/experiments", status_code=202)
async def experiment_create(request: Request):
    b = await body(request)
    d = await _dataset(b.get("dataset_id") or "")
    pr = _proj(b.get("project") or "")
    try:
        evs = f3.validar_evaluadores(b.get("evaluators"))
    except ValueError as e:
        err(400, str(e), "validation")
    validate_project = validate(pr, S.registry, S.settings.values, loader=S.projects.get)
    if validate_project["errores"]:
        err(400, "El proyecto no es válido: " + "; ".join(x["msg"] for x in validate_project["errores"]),
            "validation")
    ejs = await examples_list(d["dataset_id"], limit=10000)
    if not ejs:
        err(400, "El dataset no tiene ejemplos", "validation")
    aid, av = b.get("assistant_id"), None
    if aid:
        av = (await _assistant_for_run(pr["slug"], aid, b.get("assistant_version")))["version"]
    try:
        conc = max(1, min(4, int(b.get("concurrency") or 1)))
    except (TypeError, ValueError):
        err(400, "concurrency debe ser un entero entre 1 y 4", "validation")
    eid = "exp-" + uuid.uuid4().hex[:12]
    n = 1 + (await S.db.one("SELECT COUNT(*) n FROM experiments WHERE dataset_id=?", (d["dataset_id"],)))["n"]
    name = (b.get("name") or "").strip() or f"{d['name']} #{n}"
    await S.db.exec("INSERT INTO experiments(experiment_id, dataset_id, project, assistant_id, assistant_version, "
                    "evaluators, status, created, name, concurrency) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (eid, d["dataset_id"], pr["slug"], aid, av, dumps(evs), "pending", time.time(), name, conc))
    await S.db.execmany("INSERT INTO experiment_results(experiment_id, example_id, status) VALUES (?,?,?)",
                        [(eid, e["example_id"], "pending") for e in ejs])
    _EXP_TASKS[eid] = asyncio.create_task(_exp_lanzar(eid))
    return {"experiment_id": eid, "status": "pending"}


@app.get("/api/experiments")
async def experiments_list(dataset: str | None = None, project: str | None = None):
    sql, args = "SELECT experiment_id FROM experiments WHERE 1=1", []
    if dataset:
        sql += " AND dataset_id=?"
        args.append(dataset)
    if project:
        sql += " AND project=?"
        args.append(project)
    out = []
    for r in await S.db.all(sql + " ORDER BY created DESC", args):
        x = await _experimento(r["experiment_id"])
        x.pop("evaluators", None)
        out.append(x)
    return out


@app.get("/api/experiments/compare")
async def experiments_compare(ids: str):
    lista = [i for i in ids.split(",") if i]
    if len(lista) < 2:
        err(400, "Indica al menos dos experimentos", "validation")
    xs = [await _experimento(i) for i in lista]
    if len({x["dataset_id"] for x in xs}) != 1:
        err(400, "Los experimentos deben ser del mismo dataset", "validation")
    filas = {}
    for x in xs:
        for r in await _resultados(x["experiment_id"]):
            f = filas.setdefault(r["example_id"], {"example_id": r["example_id"], "input": r["input"],
                                                   "expected": r["expected"], "por_experimento": {}})
            f["por_experimento"][x["experiment_id"]] = {k: r[k] for k in ("output", "scores", "status", "duration")}
    return {"experimentos": xs, "filas": list(filas.values()),
            "resumen": {x["experiment_id"]: x["resumen"] for x in xs}}


@app.get("/api/experiments/{eid}")
async def experiment_get(eid: str):
    x = await _experimento(eid)
    x["results"] = await _resultados(eid)
    return x


@app.post("/api/experiments/{eid}/stop", status_code=202)
async def experiment_stop(eid: str):
    x = await _experimento(eid)
    if x["status"] in ("pending", "running") and eid in _EXP_TASKS:
        _EXP_STOP.add(eid)
        for r in await S.db.all("SELECT run_id FROM runs WHERE experiment_id=? AND status IN "
                                "('pending','running','pausing')", (eid,)):
            await S.engine.stop(r["run_id"])
    return {"experiment_id": eid, "status": "stopping"}


async def _rescore(eid, evs, previo):
    try:
        for r in await _resultados(eid):
            if r["status"] != "success":
                continue
            sc = await f3.puntuar(evs, r["output"], r["expected"], r["input"], S.settings.values)
            await S.db.exec("UPDATE experiment_results SET scores=? WHERE experiment_id=? AND example_id=?",
                            (dumps(sc), eid, r["example_id"]))
    finally:
        await S.db.exec("UPDATE experiments SET status=? WHERE experiment_id=?", (previo, eid))
        _EXP_TASKS.pop(eid, None)
        xx = await _experimento(eid)
        _exp_emit(eid, xx["status"], xx["hechos"], xx["n"])


@app.post("/api/experiments/{eid}/rescore", status_code=202)
async def experiment_rescore(eid: str, request: Request):
    b = await body(request)
    x = await _experimento(eid)
    if x["status"] in ("pending", "running"):
        err(409, "El experimento está en marcha", "experiment_running")
    try:
        evs = f3.validar_evaluadores(b.get("evaluators"))
    except ValueError as e:
        err(400, str(e), "validation")
    await S.db.exec("UPDATE experiments SET evaluators=?, status='running' WHERE experiment_id=?", (dumps(evs), eid))
    _EXP_TASKS[eid] = asyncio.create_task(_rescore(eid, evs, x["status"]))
    return {"experiment_id": eid, "status": "running"}


@app.delete("/api/experiments/{eid}", status_code=204)
async def experiment_delete(eid: str):
    x = await _experimento(eid)
    if x["status"] in ("pending", "running"):
        err(409, "El experimento está en marcha", "experiment_running")
    await S.db.exec("DELETE FROM experiments WHERE experiment_id=?", (eid,))
    return Response(status_code=204)


# ------------------------------------------------------------------------------------------ programaciones [F3]
_CRON_TASK: dict[str, asyncio.Task] = {}


def _cron_out(c):
    if c is None:
        return c
    c["input"] = json.loads(c["input"]) if c.get("input") else None
    c["active"] = bool(c.get("active"))
    return c


async def _cron(cid) -> dict:
    c = await S.db.one("SELECT * FROM crons WHERE cron_id=?", (cid,))
    if not c:
        err(404, f"No existe la programación {cid}", "not_found")
    return _cron_out(c)


async def _cron_validar(b: dict, prev: dict | None = None) -> dict:
    c = dict(prev or {})
    for k in ("project", "name", "cron", "input", "thread_mode", "thread_id", "assistant_id", "multitask_strategy",
              "active"):
        if k in b:
            c[k] = b[k]
    pr = _proj(c.get("project") or "")
    expr = str(c.get("cron") or "").strip()
    if not f3.cron_valido(expr):
        err(400, f"Expresión cron no válida: «{expr}»", "cron")
    c["cron"] = expr
    c["thread_mode"] = c.get("thread_mode") or "nuevo"
    if c["thread_mode"] not in ("nuevo", "fijo"):
        err(400, "thread_mode debe ser nuevo o fijo", "validation")
    if c["thread_mode"] == "fijo":
        t = await _thread(c.get("thread_id") or "")
        if t["project"] != pr["slug"]:
            err(400, "El hilo es de otro proyecto", "validation")
    else:
        c["thread_id"] = None
    c["multitask_strategy"] = c.get("multitask_strategy") or "enqueue"
    if c["multitask_strategy"] not in STRATEGIES:
        err(400, f"multitask_strategy debe ser una de: {', '.join(STRATEGIES)}", "validation")
    if c.get("input") is not None and not isinstance(c["input"], dict):
        err(400, "input debe ser un objeto", "validation")
    if c.get("assistant_id"):
        await _assistant_for_run(pr["slug"], c["assistant_id"])
    else:
        c["assistant_id"] = None
    c["name"] = (c.get("name") or "").strip() or pr["name"]
    c["active"] = 1 if c.get("active", True) else 0
    return c


async def _cron_disparar(c: dict) -> dict:
    pr = _proj(c["project"])
    if c["thread_mode"] == "fijo":
        tid = c["thread_id"]
    else:
        tid = await _new_thread(pr["slug"], f"{c['name']} · {time.strftime('%Y-%m-%d %H:%M:%S')}")
        await S.db.exec("UPDATE threads SET metadata=? WHERE thread_id=?", (dumps({"cron": c["cron_id"]}), tid))
    b = {"input": c.get("input") or {}, "_cron_id": c["cron_id"], "multitask_strategy": c["multitask_strategy"]}
    if c.get("assistant_id"):
        b["assistant_id"] = c["assistant_id"]
    out = await _lanzar(tid, b)
    S.engine.bus.emit(out["run_id"], tid, "cron.fired",
                      data={"cron_id": c["cron_id"], "run_id": out["run_id"], "thread_id": tid})
    return {**out, "thread_id": tid}


async def _cron_tick(c: dict, now: float):
    error = None
    try:
        await _cron_disparar(c)
    except ApiError as e:
        error = ("disparo saltado: el hilo está ocupado" if e.code == "thread_busy" else e.detail)
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    await S.db.exec("UPDATE crons SET last_run=?, next_run=?, last_error=? WHERE cron_id=?",
                    (now, f3.siguiente(c["cron"], now), error, c["cron_id"]))


async def _cron_loop():
    while True:
        try:
            now = time.time()
            for c in await S.db.all("SELECT * FROM crons WHERE active=1 AND next_run<=?", (now,)):
                await _cron_tick(_cron_out(c), now)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"planificador: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(0.5)


@app.get("/api/crons")
async def crons_list(project: str | None = None):
    if project:
        rows = await S.db.all("SELECT * FROM crons WHERE project=? ORDER BY created DESC", (project,))
    else:
        rows = await S.db.all("SELECT * FROM crons ORDER BY created DESC")
    return [_cron_out(r) for r in rows]


@app.get("/api/crons/preview")
async def cron_preview(cron: str, n: int = 5):
    """Siempre 200 (la GUI la llama mientras se escribe): {cron, valido, proximas, error}."""
    if not f3.cron_valido(cron):
        return {"cron": cron, "valido": False, "proximas": [], "error": f"Expresión cron no válida: «{cron}»"}
    return {"cron": cron, "valido": True, "proximas": f3.proximas(cron, max(1, min(n, 20))), "error": None}


@app.post("/api/crons", status_code=201)
async def cron_create(request: Request):
    c = await _cron_validar(await body(request))
    cid = "cron-" + uuid.uuid4().hex[:12]
    now = time.time()
    await S.db.exec("INSERT INTO crons(cron_id, project, assistant_id, thread_mode, thread_id, input, cron, active, "
                    "next_run, name, multitask_strategy, created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, c["project"], c["assistant_id"], c["thread_mode"], c["thread_id"],
                     dumps(c["input"]) if c.get("input") is not None else None, c["cron"], c["active"],
                     f3.siguiente(c["cron"], now), c["name"], c["multitask_strategy"], now))
    return await _cron(cid)


@app.get("/api/crons/{cid}")
async def cron_get(cid: str):
    return await _cron(cid)


@app.patch("/api/crons/{cid}")
async def cron_patch(cid: str, request: Request):
    prev = await _cron(cid)
    c = await _cron_validar(await body(request), prev)
    await S.db.exec("UPDATE crons SET project=?, assistant_id=?, thread_mode=?, thread_id=?, input=?, cron=?, "
                    "active=?, next_run=?, name=?, multitask_strategy=? WHERE cron_id=?",
                    (c["project"], c["assistant_id"], c["thread_mode"], c["thread_id"],
                     dumps(c["input"]) if c.get("input") is not None else None, c["cron"], c["active"],
                     f3.siguiente(c["cron"]), c["name"], c["multitask_strategy"], cid))
    return await _cron(cid)


@app.delete("/api/crons/{cid}", status_code=204)
async def cron_delete(cid: str):
    await _cron(cid)
    await S.db.exec("DELETE FROM crons WHERE cron_id=?", (cid,))
    return Response(status_code=204)


@app.post("/api/crons/{cid}/run", status_code=202)
async def cron_run_now(cid: str):
    c = await _cron(cid)
    out = await _cron_disparar(c)
    await S.db.exec("UPDATE crons SET last_run=? WHERE cron_id=?", (time.time(), cid))
    return out


@app.get("/api/crons/{cid}/runs")
async def cron_runs(cid: str, limit: int = 50):
    await _cron(cid)
    rows = await S.db.all("SELECT * FROM runs WHERE cron_id=? ORDER BY t0 DESC LIMIT ?", (cid, min(limit, 1000)))
    return [_run_out(r) for r in rows]


# ------------------------------------------------------------------------------------------ MCP [F3]
@app.get("/api/mcp/servers")
async def mcp_servers():
    import mcp_cliente
    return await mcp_cliente.listar(S.settings.get("mcp_servidores") or {})


@app.post("/api/mcp/call")
async def mcp_call(request: Request):
    import mcp_cliente
    b = await body(request)
    srvs = S.settings.get("mcp_servidores") or {}
    if b.get("servidor") not in srvs:
        err(404, f"No existe el servidor MCP {b.get('servidor')!r}", "not_found")
    if b.get("args") is not None and not isinstance(b["args"], dict):
        err(400, "args debe ser un objeto", "validation")
    try:
        r = await mcp_cliente.llamar(srvs, b["servidor"], b.get("herramienta") or "", b.get("args") or {})
    except KeyError as e:
        err(404, f"No existe la herramienta {e}", "not_found")
    except mcp_cliente.McpError as e:
        err(502, str(e), "mcp")
    return JSONResponse(json.loads(dumps({"resultado": r})))


def main():
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    a = ap.parse_args()
    _prepare_home()
    st = Settings(HOME / "settings.json")
    host, port = a.host or st["host"], a.port or st["port"]
    print(f"Estudio de agentes en http://{host}:{port}  (STUDIO_HOME={HOME})", flush=True)
    uvicorn.run(app, host=host, port=port, workers=1, log_level="warning", timeout_graceful_shutdown=8)


if __name__ == "__main__":
    main()
