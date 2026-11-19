"""Proyecto JSON → StateGraph de LangGraph (DISENO.md §3.3, §3.4, §4). No depende de FastAPI."""
from __future__ import annotations

import ast
import asyncio
import contextvars
import hashlib
import json
import operator
import os
import signal
import sqlite3
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Callable, TypedDict

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.cache.sqlite import SqliteCache
from langgraph.errors import GraphBubbleUp, GraphInterrupt, NodeTimeoutError
from langgraph.errors import NodeError as LGNodeError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime
from langgraph.types import CachePolicy, Command, Overwrite, RetryPolicy, Send, TimeoutPolicy, interrupt

from modules import ModuloError, Registry, keys_from_value
from runner import CtxBase

STUDIO = Path(__file__).resolve().parent
BASE = STUDIO.parent
NODE_RE = __import__("re").compile(r"^[a-z][a-z0-9_]{0,39}$")
REDUCERS = ("replace", "append", "merge", "sum", "max", "messages")
SAFE_BUILTINS = {n: getattr(__import__("builtins"), n) for n in
                 ("len", "min", "max", "abs", "int", "float", "str", "bool", "round", "any", "all", "sorted", "sum",
                  "isinstance", "list", "dict")}
SAFE_BUILTINS.update({"True": True, "False": False, "None": None})
_SYNC_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mod-local")


class ValidationError(Exception):
    def __init__(self, errores: list, avisos: list | None = None):
        super().__init__("; ".join(e["msg"] for e in errores))
        self.errores = errores
        self.avisos = avisos or []


class NodoProcesoError(Exception):
    """El subproceso de un nodo terminó con error."""


class NodoError(Exception):
    """Error de validación de la salida de un nodo (no se reintenta)."""


# ----------------------------------------------------------------------------------- reducers
def _append(a, b):
    a = a if isinstance(a, list) else ([] if a is None else [a])
    b = b if isinstance(b, list) else ([] if b is None else [b])
    return a + b


def _merge(a, b):
    return {**(a or {}), **(b or {})}


def _sum(a, b):
    return (a or 0) + (b or 0)


def _max(a, b):
    return b if a is None else a if b is None else max(a, b)


REDUCER_FN = {"append": _append, "merge": _merge, "sum": _sum, "max": _max, "messages": add_messages}


# ----------------------------------------------------------------------------------- entorno de ejecución
class GpuGate:
    """Semáforo lector/escritor asyncio: varios 'shared' a la vez o un 'exclusive' solo (§5.6)."""

    def __init__(self):
        self._cond = asyncio.Condition()
        self.shared = 0
        self.exclusive = False
        self.waiting_excl = 0
        self.proc_shared = 0     # [F3] 'shared' en subproceso (cargan su propio modelo en VRAM)

    def libre(self, mode, proceso=False, limite=None) -> bool:
        if mode == "exclusive":
            return not self.exclusive and self.shared == 0
        if proceso and limite and self.proc_shared >= limite:
            return False
        return not self.exclusive and self.waiting_excl == 0

    async def acquire(self, mode, on_wait: Callable[[], None] | None = None, proceso=False, limite=None):
        async with self._cond:
            if mode == "exclusive":
                self.waiting_excl += 1
            try:
                while not self.libre(mode, proceso, limite):
                    if on_wait:
                        on_wait()
                    try:
                        await asyncio.wait_for(self._cond.wait(), 1.0)
                    except asyncio.TimeoutError:
                        pass
            finally:
                if mode == "exclusive":
                    self.waiting_excl -= 1
            if mode == "exclusive":
                self.exclusive = True
            else:
                self.shared += 1
                if proceso:
                    self.proc_shared += 1

    async def release(self, mode, proceso=False):
        async with self._cond:
            if mode == "exclusive":
                self.exclusive = False
            else:
                self.shared -= 1
                if proceso:
                    self.proc_shared -= 1
            self._cond.notify_all()


@dataclass
class Env:
    """Lo que los wrappers necesitan del motor. El motor lo crea; build() usa uno por defecto si falta."""
    home: Path
    settings: dict
    registry: Registry
    gpu: GpuGate = field(default_factory=GpuGate)
    on_pgid: Callable[[str | None, int, bool], None] | None = None  # (run_id, pgid, vivo)
    before_exclusive: Callable[[], Any] | None = None               # async: descargar Ollama (D6)
    load_project: Callable[[str], dict] | None = None               # [F2] subgrafos: slug → proyecto

    @classmethod
    def default(cls, home: Path | None = None, settings: dict | None = None):
        home = Path(home or os.environ.get("STUDIO_HOME") or STUDIO)
        return cls(home=home, settings=settings or {}, registry=Registry(home / "modules", home / "data/modsnap"))


# ----------------------------------------------------------------------------------- utilidades
def flat(state) -> dict:
    return {k: v for k, v in (state or {}).items() if v is not None}


def get_path(d: dict, path: str):
    cur = d
    for p in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _num(v):
    if isinstance(v, bool) or v is None or v == "":
        return v
    try:
        x = float(v)
    except (TypeError, ValueError):
        return v
    if isinstance(v, int) or (x.is_integer() and not (isinstance(v, str) and "." in v) and not isinstance(v, float)):
        return int(x)
    return x


def resolve_cfg(fields: list, node_config: dict, overrides: dict | None, settings: dict) -> dict:
    out = dict(node_config or {})
    for f in fields:
        k = f["key"]
        v = f.get("default")
        if k in (node_config or {}):
            v = node_config[k]
        if overrides and k in overrides:
            v = overrides[k]
        if (v is None or v == "") and f.get("setting"):
            sv = get_path(settings, f["setting"])
            if sv not in (None, ""):
                v = sv
        t = f.get("type", "text")
        if t == "number":
            v = _num(v)
        elif t == "select" and f.get("options") and all(isinstance(o, (int, float)) for o in f["options"]):
            v = _num(v)
        elif t == "checkbox":
            v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "sí", "si", "on")
        elif t == "json" and isinstance(v, str):
            try:
                v = json.loads(v) if v.strip() else None
            except ValueError:
                pass
        out[k] = v
    return out


def cond_keys(expr: str) -> set[str]:
    """Claves usadas en una condición: s['x'], s.get('x'), state[...]."""
    out = set()
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return out
    for n in ast.walk(tree):
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id in ("s", "state"):
            if isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
                out.add(n.slice.value)
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
                and isinstance(n.func.value, ast.Name) and n.func.value.id in ("s", "state") and n.args
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
            out.add(n.args[0].value)
    return out


def eval_cond(code, state: dict):
    f = flat(state)
    return eval(code, {"__builtins__": SAFE_BUILTINS}, {"s": f, "state": f})


def module_io(info, node_config: dict, settings: dict, overrides: dict | None = None) -> tuple[list, list]:
    """Claves de entrada y salida declaradas de un nodo (META + campos *_from_config)."""
    meta = info.meta
    cfg = resolve_cfg(info.config, node_config, overrides, settings)
    ins = list(meta.get("inputs") or [])
    outs = list(meta.get("outputs") or [])
    for k in meta.get("inputs_from_config") or []:
        ins += keys_from_value(cfg.get(k))
    for k in meta.get("outputs_from_config") or []:
        outs += keys_from_value(cfg.get(k))
    return list(dict.fromkeys(ins)), list(dict.fromkeys(outs))


def _src_list(e) -> list:
    return e["src"] if isinstance(e["src"], list) else [e["src"]]


# ----------------------------------------------------------------------------------- validación
def es_enrutador(meta: dict) -> bool:
    """[F2] Nodos cuyas aristas salientes son destinos de Command/Send (no aristas fijas)."""
    return bool(meta.get("command")) or meta.get("kind") == "router"


def validate(project: dict, registry: Registry, settings: dict | None = None, extra_keys=(),
             node_overrides: dict | None = None, loader: Callable[[str], dict] | None = None,
             _stack: tuple = ()) -> dict:
    """→ {errores, avisos, canales: {clave: reducer}, entradas: [...], io: {nodo: (ins, outs)}}"""
    settings = settings or {}
    errores, avisos = [], []
    nodes = project.get("nodes") or {}
    edges = project.get("edges") or []
    schema = project.get("state_schema") or {}
    canales: dict[str, str] = {}
    io, infos, subs, flujos = {}, {}, {}, {}

    for k, d in schema.items():
        r = (d or {}).get("reducer", "replace")
        if r not in REDUCERS:
            errores.append({"msg": f"Reducer desconocido '{r}' en la clave '{k}'"})
            r = "replace"
        canales[k] = r

    for nid, n in nodes.items():
        if not NODE_RE.match(nid) or nid.startswith("__"):
            errores.append({"msg": f"Id de nodo no válido: '{nid}'", "node": nid})
        try:
            info = registry.get(n.get("module", ""))
        except ModuloError as e:
            info = None
            errores.append({"msg": str(e), "node": nid})
        if info is None:
            errores.append({"msg": f"El nodo '{nid}' usa un módulo que no existe: {n.get('module')}", "node": nid})
            continue
        if info.error:
            errores.append({"msg": f"El módulo {info.file} tiene un error: {info.error}", "node": nid})
            continue
        infos[nid] = info
        if info.meta.get("kind") == "subgraph":
            sub = _validate_sub(project, nid, n, info, registry, settings, node_overrides, loader, _stack, errores)
            if sub is None:
                continue
            subs[nid] = sub
            io[nid] = (sub["ins"], sub["outs"])
        else:
            io[nid] = module_io(info, n.get("config") or {}, settings, (node_overrides or {}).get(nid))
            cfg_r = resolve_cfg(info.config, n.get("config") or {}, (node_overrides or {}).get(nid), settings)
            if info.meta.get("kind") == "agent" and keys_from_value(cfg_r.get("flujos")):
                flujos[nid] = _validate_flujos(project, nid, cfg_r, registry, settings, loader, _stack, errores)
            for f in info.config:
                if f.get("required") and cfg_r.get(f["key"]) in (None, "", 0, [], {}):
                    errores.append({"msg": f"'{nid}': el campo '{f.get('label') or f['key']}' es obligatorio",
                                    "node": nid})
        for k in io[nid][0] + io[nid][1]:
            canales.setdefault(k, "messages" if k == "messages" else "replace")

    for nid, sub in subs.items():
        for k in sub["internas"]:
            otros = k in schema or any(k in (io[o][0] + io[o][1]) for o in io if o != nid)
            if otros:
                errores.append({"msg": f"La clave '{k}' es {sub['canales'][k]} en el subproyecto "
                                       f"'{sub['slug']}' y no se puede compartir con el padre", "node": nid})
    for nid, info in infos.items():
        if es_enrutador(info.meta):
            for e in edges:
                if nid in _src_list(e) and e.get("cond"):
                    errores.append({"msg": f"'{nid}' enruta con Command/Send: sus aristas no llevan condición "
                                           f"({e.get('id')})", "edge": e.get("id")})

    for nid, n in nodes.items():
        et = n.get("error_to")
        if et:
            if et == nid:
                errores.append({"msg": f"error_to de '{nid}' no puede ser el propio nodo", "node": nid})
            elif et not in nodes:
                errores.append({"msg": f"error_to de '{nid}' apunta a un nodo inexistente: '{et}'", "node": nid})
            canales.setdefault("error", "replace")
        c = n.get("cache")
        if c is not None:
            if not isinstance(c, dict) or (c.get("ttl") is not None and not isinstance(c.get("ttl"), (int, float))):
                errores.append({"msg": f"cache de '{nid}' debe ser null o {{\"ttl\": segundos|null}}", "node": nid})
        if not isinstance(n.get("defer", False), bool):
            errores.append({"msg": f"defer de '{nid}' debe ser true o false", "node": nid})

    for e in edges:
        if e.get("cond") and e["cond"] != "else":
            for k in cond_keys(e["cond"]):
                canales.setdefault(k, "replace")
    for k in extra_keys:
        canales.setdefault(k, "replace")

    for nid in nodes:
        if nid in canales:
            errores.append({"msg": f"El id de nodo '{nid}' coincide con una clave del estado", "node": nid})

    validos = set(nodes) | {"__start__", "__end__"}
    elses: dict[str, int] = {}
    for e in edges:
        srcs = _src_list(e)
        for s in srcs:
            if s not in validos or s == "__end__":
                errores.append({"msg": f"La arista {e.get('id')} sale de un nodo inexistente: '{s}'", "edge": e.get("id")})
        if e.get("dst") not in validos or e.get("dst") == "__start__":
            errores.append({"msg": f"La arista {e.get('id')} va a un nodo inexistente: '{e.get('dst')}'",
                            "edge": e.get("id")})
        c = e.get("cond")
        if isinstance(e["src"], list) and len(e["src"]) > 1 and c:
            errores.append({"msg": f"La arista {e.get('id')} es un join y no puede tener condición", "edge": e.get("id")})
        if c == "else":
            elses[srcs[0]] = elses.get(srcs[0], 0) + 1
        elif c:
            try:
                compile(c, "<cond>", "eval")
            except SyntaxError as ex:
                errores.append({"msg": f"Condición de la arista {e.get('id')} con error de sintaxis: {ex.msg}",
                                "edge": e.get("id")})
    for s, n in elses.items():
        if n > 1:
            errores.append({"msg": f"El nodo '{s}' tiene {n} aristas 'else' (solo se permite una)", "node": s})

    entradas = _entradas(nodes, edges, infos)
    if not entradas:
        errores.append({"msg": "No hay entrada: añade una arista desde __start__ o un nodo de tipo entrada"})

    # avisos: alcanzabilidad, joins tras condiciones, entradas no producidas
    succ: dict[str, set] = {}
    for e in edges:
        for s in _src_list(e):
            succ.setdefault(s, set()).add(e.get("dst"))
    for nid, n in nodes.items():
        if n.get("error_to"):
            succ.setdefault(nid, set()).add(n["error_to"])
    vistos, pila = set(), list(entradas)
    while pila:
        x = pila.pop()
        if x in vistos:
            continue
        vistos.add(x)
        pila += list(succ.get(x, ()))
    for nid in nodes:
        if nid not in vistos:
            avisos.append({"msg": f"El nodo '{nid}' no es alcanzable desde el inicio", "node": nid})
    cond_dst = {e.get("dst") for e in edges if e.get("cond")}
    for e in edges:
        if isinstance(e["src"], list) and len(e["src"]) > 1 and cond_dst & set(e["src"]):
            avisos.append({"msg": f"El join {e.get('id')} tiene una fuente detrás de una condición: puede no "
                                  f"dispararse nunca", "edge": e.get("id")})
    producidas = set(schema) | set(extra_keys)
    for nid, (ins, outs) in io.items():
        producidas |= set(outs)
    for nid, (ins, outs) in io.items():
        for k in ins:
            if k not in producidas:
                avisos.append({"msg": f"'{nid}' lee '{k}', que ningún nodo produce", "node": nid})
    return {"errores": errores, "avisos": avisos, "canales": canales, "entradas": entradas, "io": io, "infos": infos,
            "subs": subs, "flujos": flujos}


def _validate_sub(project, nid, n, info, registry, settings, node_overrides, loader, _stack, errores):
    """Nodo kind subgraph: valida el subproyecto y calcula las claves que comparte con el padre."""
    cfg = resolve_cfg(info.config, n.get("config") or {}, (node_overrides or {}).get(nid), settings)
    slug = (cfg.get("proyecto") or "").strip()
    yo = project.get("slug") or "_"
    if not slug:
        errores.append({"msg": f"El subgrafo '{nid}' no tiene proyecto", "node": nid})
        return None
    if loader is None:
        errores.append({"msg": f"No se pueden cargar subproyectos aquí ('{nid}')", "node": nid})
        return None
    if slug in _stack or slug == yo:
        errores.append({"msg": f"Ciclo de subproyectos: {' → '.join([*_stack, yo, slug])}", "node": nid})
        return None
    try:
        child = loader(slug)
    except (KeyError, FileNotFoundError):
        errores.append({"msg": f"El subgrafo '{nid}' usa un proyecto que no existe: {slug}", "node": nid})
        return None
    cv = validate(child, registry, settings, (), None, loader, (*_stack, yo))
    if cv["errores"]:
        errores.append({"msg": f"Subproyecto '{slug}': " + "; ".join(x["msg"] for x in cv["errores"]), "node": nid})
        return None
    internas = [k for k, r in cv["canales"].items() if r in ("append", "sum")]
    schema = child.get("state_schema") or {}
    compartidas = [k for k in cv["canales"] if k not in internas]
    marcadas = [k for k in compartidas if (schema.get(k) or {}).get("output")]
    return {"slug": slug, "project": child, "canales": cv["canales"], "internas": internas,
            "ins": compartidas, "outs": marcadas or compartidas}


def _validate_flujos(project, nid, cfg, registry, settings, loader, _stack, errores) -> list:
    """[D9] Agente con herramientas: cada slug de 'flujos' será la herramienta flujo__<slug>. → [{slug, project, ins,
    outs, tool}] de los válidos; los inválidos añaden un error."""
    yo = project.get("slug") or "_"
    out = []
    for slug in keys_from_value(cfg.get("flujos")):
        if loader is None:
            errores.append({"msg": f"No se pueden cargar flujos aquí ('{nid}')", "node": nid})
            break
        if slug in _stack or slug == yo:
            errores.append({"msg": f"Ciclo de flujos: {' → '.join([*_stack, yo, slug])} (herramienta de '{nid}')",
                            "node": nid})
            continue
        try:
            child = loader(slug)
        except (KeyError, FileNotFoundError):
            errores.append({"msg": f"'{nid}' usa como herramienta un flujo que no existe: {slug}", "node": nid})
            continue
        cv = validate(child, registry, settings, (), None, loader, (*_stack, yo))
        if cv["errores"]:
            errores.append({"msg": f"Flujo '{slug}' (herramienta de '{nid}'): "
                            + "; ".join(x["msg"] for x in cv["errores"]), "node": nid})
            continue
        if "messages" in cv["canales"]:
            errores.append({"msg": f"El flujo '{slug}' usa 'messages' y no puede ser herramienta de '{nid}'",
                            "node": nid})
            continue
        schema = child.get("state_schema") or {}
        ins = [k for k, d in schema.items() if (d or {}).get("input")] or ["texto"]
        outs = [k for k, d in schema.items() if (d or {}).get("output")]
        out.append({"slug": slug, "project": child, "ins": ins, "outs": outs, "tool": tool_name_flujo(slug)})
    return out


def tool_name_flujo(slug: str) -> str:
    """[D9] Nombre de la herramienta de un flujo: flujo__<slug con guiones bajos>."""
    return "flujo__" + slug.replace("-", "_")


def _entradas(nodes, edges, infos) -> list:
    desde = [e["dst"] for e in edges if "__start__" in _src_list(e)]
    if desde:
        return desde
    ins = [n for n, i in infos.items() if i.meta.get("kind") == "input"]
    if ins:
        return ins
    con_entrada = {e["dst"] for e in edges}
    return [n for n in nodes if n not in con_entrada]


# ----------------------------------------------------------------------------------- ctx local
class LocalCtx(CtxBase):
    def __init__(self, emit, heartbeat, *a, runtime=None, **kw):
        super().__init__(*a, **kw)
        self._emit_fn = emit
        self.heartbeat = heartbeat
        self.runtime = runtime
        self.writer = runtime.stream_writer if runtime else None
        self.store = runtime.store if runtime else None

    def log(self, *a):
        txt = " ".join(str(x) for x in a)
        for line in txt.splitlines() or [""]:
            self._emit_fn("node.log", {"line": line})

    def progress(self, x):
        try:
            self._emit_fn("node.progress", {"value": max(0.0, min(1.0, float(x)))})
        except (TypeError, ValueError):
            pass

    def update(self, d):
        self._emit_fn("node.partial", {"patch": d})

    def token(self, texto):
        self._emit_fn("token", {"text": str(texto)})

    def interrupt(self, value, schema=None):
        return interrupt(value, response_schema=schema)


# ----------------------------------------------------------------------------------- wrapper
@dataclass
class NodeSpec:
    id: str
    label: str
    file: str
    info: Any
    module: Any            # módulo cargado (local) o None
    snap: Path | None      # instantánea (proceso)
    node: dict
    inputs: list
    allowed: set
    canales: dict
    run_timeout: float | None
    destinos: tuple = ()   # [F2] nodos enrutadores: destinos válidos de Command/Send


def _json_ok(v) -> bool:
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


def _check_output(spec: NodeSpec, out):
    if out is None:
        return None
    if isinstance(out, Command):
        if isinstance(out.update, dict):
            return Command(graph=out.graph, update=_check_output(spec, out.update), resume=out.resume, goto=out.goto)
        return out
    if not isinstance(out, dict):
        raise NodoError(f"run() de '{spec.id}' debe devolver un dict o None, devolvió {type(out).__name__}")
    res = {}
    for k, v in out.items():
        if k not in spec.allowed:
            raise NodoError(f"La clave `{k}` no está declarada: añádela a outputs del módulo o al esquema de estado "
                            f"del proyecto")
        if isinstance(v, dict) and set(v) == {"__overwrite__"}:
            if spec.canales.get(k, "replace") == "replace":
                raise NodoError(f"__overwrite__ solo vale en claves con reducer distinto de 'replace' ('{k}')")
            if not _json_ok(v["__overwrite__"]):
                raise NodoError(f"El valor de '{k}' no es serializable a JSON")
            res[k] = Overwrite(v["__overwrite__"])
            continue
        if spec.canales.get(k) == "messages" and _mensajes_ok(v):
            res[k] = v
            continue
        if not _json_ok(v):
            raise NodoError(f"El valor de '{k}' no es serializable a JSON ({type(v).__name__})")
        res[k] = v
    return res


def _mensajes_ok(v) -> bool:
    vs = v if isinstance(v, list) else [v]
    return all(isinstance(x, (BaseMessage, dict, str)) for x in vs)


def jenc(o):
    """default= de json.dumps: mensajes de LangChain como dict; el resto como texto."""
    if isinstance(o, BaseMessage):
        return o.model_dump()
    return str(o)


def _short(v, limit=20000):
    try:
        s = json.dumps(v, ensure_ascii=False, default=jenc)
    except Exception:
        return str(v)[:limit]
    if len(s) <= limit:
        return v
    return {"__truncado__": s[:limit]}


def make_wrapper(spec: NodeSpec, env: Env, slug: str, assistant: dict):
    meta = spec.info.meta
    gpu = meta.get("gpu") or False
    proc_gpu = gpu == "shared" and meta.get("exec") == "proceso"

    async def wrapper(state, runtime: Runtime, config: RunnableConfig):
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        ei = runtime.execution_info
        task_id = ei.task_id if ei else "t"
        attempt = ei.node_attempt if ei else 1
        thread_id = (ei.thread_id if ei else None) or "_sin_hilo"
        run_id = (config.get("configurable") or {}).get("run_id_estudio")
        writer = runtime.stream_writer
        hb = runtime.heartbeat

        def send(type_, data):
            payload = {"__studio__": True, "type": type_, "node": spec.id, "task_id": task_id, "data": data}
            if threading.get_ident() == loop_thread:
                writer(payload)
            else:
                loop.call_soon_threadsafe(writer, payload)

        def emit(type_, data):
            send(type_, data)
            if type_ in ("node.progress", "node.log", "token", "node.partial"):
                if threading.get_ident() == loop_thread:
                    hb()
                else:
                    loop.call_soon_threadsafe(hb)

        settings = {k: v for k, v in env.settings.items() if k != "token"}
        cfg = resolve_cfg(spec.info.config, spec.node.get("config") or {},
                          (assistant.get("node_overrides") or {}).get(spec.id), env.settings)
        st = flat(state)
        run_dir = env.home / "projects" / slug / "threads" / thread_id / spec.id / task_id
        run_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        send("node.start", {"attempt": attempt, "label": spec.label})
        acquired = False
        try:
            if gpu:
                if gpu == "exclusive" and env.before_exclusive:
                    try:
                        await env.before_exclusive()
                    except Exception:
                        pass

                def esperando():
                    send("node.start", {"attempt": attempt, "esperando_gpu": True})
                    hb()
                await env.gpu.acquire(gpu, esperando, proceso=proc_gpu,
                                      limite=int(env.settings.get("gpu_procesos_compartidos") or 0) or None)
                acquired = True
                send("node.start", {"attempt": attempt, "esperando_gpu": False})
                hb()
            t_run = time.monotonic()
            try:
                async with asyncio.timeout(spec.run_timeout):
                    if meta.get("exec") == "proceso":
                        precarga = await _store_reads(runtime.store, meta, st, assistant.get("context") or {},
                                                      int(env.settings.get("store_items_por_ns") or 200))
                        out = await _run_proceso(spec, env, st, cfg, settings, run_dir, thread_id, run_id,
                                                 assistant.get("context") or {}, emit, hb, precarga)
                        out = await _store_puts(runtime.store, spec, out)
                    else:
                        ctx = LocalCtx(emit, hb, run_dir, spec.id, settings, thread_id=thread_id, run_id=run_id,
                                       context=assistant.get("context") or {}, runtime=runtime)
                        fn = spec.module.run
                        if asyncio.iscoroutinefunction(fn):
                            out = await fn(st, cfg, ctx)
                        else:
                            cv = contextvars.copy_context()
                            out = await loop.run_in_executor(_SYNC_POOL, cv.run, fn, st, cfg, ctx)
                            if asyncio.iscoroutine(out):
                                out = await out
            except TimeoutError:
                raise NodeTimeoutError(spec.id, time.monotonic() - t_run, kind="run",
                                       run_timeout=spec.run_timeout) from None
            out = _check_output(spec, _route(spec, out))
            data = {"status": "success", "output": _short(_serializable(out)), "duration": time.time() - t0}
            if meta.get("kind") == "output":
                data["salidas"] = [{"clave": m.get("clave"), "como": m.get("como", "texto"), "valor": st.get(m.get("clave"))}
                                   for m in (cfg.get("mostrar") or []) if isinstance(m, dict)]
            send("node.end", data)
            return out
        except GraphInterrupt:
            send("node.end", {"status": "interrupted", "duration": time.time() - t0})
            raise
        except GraphBubbleUp:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            send("node.end", {"status": "error", "error": f"{type(e).__name__}: {e}",
                              "traceback": traceback.format_exc()[-6000:], "duration": time.time() - t0})
            raise
        finally:
            if acquired:
                await asyncio.shield(env.gpu.release(gpu, proceso=proc_gpu))

    wrapper.__name__ = f"nodo_{spec.id}"
    return wrapper


def _route(spec: NodeSpec, out):
    """[F2] router → Command(goto); lista de Send → Command(goto=[Send…]); valida los destinos."""
    meta = spec.info.meta
    if meta.get("kind") == "router":
        if isinstance(out, str) or (isinstance(out, list) and out and all(isinstance(x, str) for x in out)):
            out = Command(goto=out)
        elif not isinstance(out, Command):
            raise NodoError(f"El router '{spec.id}' debe devolver un id de nodo o una lista de ids "
                            f"(válidos: {', '.join(spec.destinos) or 'ninguno: conecta aristas de salida'})")
    if isinstance(out, list) and out and all(isinstance(x, Send) for x in out):
        out = Command(goto=out)
    if isinstance(out, Command) and out.goto:
        if not spec.destinos:
            raise NodoError(f"'{spec.id}' devolvió Command(goto) pero su módulo no declara META['command'] = True")
        gos = out.goto if isinstance(out.goto, (list, tuple)) else [out.goto]
        for gt in gos:
            dst = gt.node if isinstance(gt, Send) else gt
            if dst == END:
                dst = "__end__"
            if dst not in spec.destinos:
                raise NodoError(f"'{spec.id}' quiere ir a '{dst}', que no está entre sus aristas de salida "
                                f"(válidos: {', '.join(spec.destinos)})")
        if any(isinstance(gt, str) and gt == "__end__" for gt in gos):
            out = Command(update=out.update, goto=[END if gt == "__end__" else gt for gt in gos], graph=out.graph)
    elif isinstance(out, list):
        raise NodoError(f"run() de '{spec.id}' devolvió una lista: solo se admite una lista de Send")
    return out


def make_sub_wrapper(spec: NodeSpec, child, ins: list, outs: list):
    """[F2] Nodo kind subgraph: invoca el subgrafo compilado (checkpointer heredado) con eventos del nodo padre."""
    allowed = set(outs)

    async def wrapper(state, runtime: Runtime, config: RunnableConfig):
        ei = runtime.execution_info
        task_id = ei.task_id if ei else "t"
        writer = runtime.stream_writer

        def send(type_, data):
            writer({"__studio__": True, "type": type_, "node": spec.id, "task_id": task_id, "data": data})

        t0 = time.time()
        send("node.start", {"attempt": ei.node_attempt if ei else 1, "label": spec.label, "subgrafo": True})
        st = flat(state)
        try:
            res = await child.ainvoke({k: st[k] for k in ins if k in st}, config)
        except GraphInterrupt:
            send("node.end", {"status": "interrupted", "duration": time.time() - t0})
            raise
        except GraphBubbleUp:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            send("node.end", {"status": "error", "error": f"{type(e).__name__}: {e}",
                              "traceback": traceback.format_exc()[-6000:], "duration": time.time() - t0})
            raise
        out = {k: v for k, v in flat(res or {}).items() if k in allowed}
        send("node.end", {"status": "success", "output": _short(out), "duration": time.time() - t0})
        return out

    wrapper.__name__ = f"subgrafo_{spec.id}"
    return wrapper


def _serializable(out):
    if isinstance(out, Command):
        return {"__command__": {"goto": str(out.goto), "update": _serializable(out.update)}}
    if isinstance(out, dict):
        return {k: (v.value if isinstance(v, Overwrite) else v) for k, v in out.items()}
    return out


def _ns_fill(ns, valores: dict) -> tuple:
    out = []
    for seg in ns:
        seg = str(seg)
        for k, v in valores.items():
            seg = seg.replace("{" + k + "}", "" if v is None else str(v))
        out.append(seg)
    return tuple(out)


async def _store_reads(store, meta, st, context, limite) -> list:
    """[F2] Precarga para exec proceso: items de los namespaces de META['store_reads']."""
    reads = meta.get("store_reads") or []
    if not reads:
        return []
    if store is None:
        raise NodoError("El módulo declara store_reads pero el grafo no tiene Store")
    out = []
    vals = {**(context or {}), **st}
    for ns in reads:
        ns = _ns_fill(ns, vals)
        for it in await store.asearch(ns, limit=limite):
            out.append({"namespace": list(it.namespace), "key": it.key, "value": it.value})
    return out


async def _store_puts(store, spec, out):
    """[F2] Aplica las escrituras '__store_put__' devueltas por un subproceso (no son claves del estado)."""
    if not isinstance(out, dict) or "__store_put__" not in out:
        return out
    puts = out.pop("__store_put__") or []
    if puts and store is None:
        raise NodoError(f"'{spec.id}' escribió en el Store pero el grafo no tiene Store")
    for put in puts:
        ns, key, value = put[:3]
        index = put[3] if len(put) > 3 else True
        if value is None:
            await store.adelete(tuple(ns), key)
        else:
            await store.aput(tuple(ns), key, value, index=None if index else False)
    return out


async def _run_proceso(spec: NodeSpec, env: Env, st, cfg, settings, run_dir: Path, thread_id, run_id, context, emit,
                       hb, precarga=None):
    meta = spec.info.meta
    in_state = {k: st[k] for k in spec.inputs if k in st}
    (run_dir / "_in.json").write_text(json.dumps(
        {"state": in_state, "cfg": cfg, "settings": settings, "node": spec.id, "thread_id": thread_id,
         "run_id": run_id, "context": context, "store": precarga or []}, ensure_ascii=False, default=jenc),
        encoding="utf-8")
    py = settings.get("python_pesado") if meta.get("interprete") == "pesado" else settings.get("python_estudio")
    py = py or (str(BASE / ".venv/bin/python") if meta.get("interprete") == "pesado"
                else str(STUDIO / ".venv-studio/bin/python"))
    penv = dict(os.environ, STUDIO_HOME=str(env.home), PYTHONUNBUFFERED="1")
    proc = await asyncio.create_subprocess_exec(
        py, "-u", str(STUDIO / "runner.py"), str(spec.snap), str(run_dir), spec.id, cwd=str(BASE),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True,
        limit=32 * 1024 * 1024, env=penv)
    if env.on_pgid:
        env.on_pgid(run_id, proc.pid, True)
    ultimas: list[str] = []
    result = None
    try:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            hb()
            if line.startswith("@@P "):
                try:
                    emit("node.progress", {"value": float(line[4:])})
                except ValueError:
                    pass
            elif line.startswith("@@S "):
                try:
                    emit("node.partial", {"patch": json.loads(line[4:])})
                except ValueError:
                    pass
            elif line.startswith("@@R "):
                result = json.loads(line[4:])
            else:
                ultimas.append(line)
                del ultimas[:-50]
                emit("node.log", {"line": line})
        rc = await proc.wait()
    except asyncio.CancelledError:
        _killpg(proc, signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), 1.5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        if proc.returncode is None:
            _killpg(proc, signal.SIGKILL)
        raise
    finally:
        if proc.returncode is None:
            _killpg(proc, signal.SIGKILL)
        if env.on_pgid:
            env.on_pgid(run_id, proc.pid, False)
    if rc != 0:
        raise NodoProcesoError(f"El proceso de '{spec.id}' terminó con código {rc}:\n" + "\n".join(ultimas))
    if result is None:
        raise NodoProcesoError(f"El proceso de '{spec.id}' terminó sin resultado (@@R)")
    (run_dir / "_done.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def _killpg(proc, sig):
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


# ----------------------------------------------------------------------------------- políticas
def _policies(node: dict, meta: dict, settings: dict):
    d = meta.get("defaults") or {}
    r = node.get("retry") or d.get("retry") or {"max_attempts": 1}
    t = node.get("timeout") if node.get("timeout") is not None else d.get("timeout")
    if t is None:
        t = {"run": settings.get("default_timeout", 900), "idle": None}
    retry = RetryPolicy(
        max_attempts=max(1, int(r.get("max_attempts") or 1)),
        initial_interval=float(r.get("initial_interval", 1.0)),
        backoff_factor=float(r.get("backoff_factor", 2.0)),
        max_interval=float(r.get("max_interval", 60)),
        jitter=bool(r.get("jitter", True)),
        retry_on=(NodoProcesoError, TimeoutError, ConnectionError, NodeTimeoutError))
    run_t = t.get("run")
    idle_t = t.get("idle")
    timeout = TimeoutPolicy(idle_timeout=float(idle_t), refresh_on="heartbeat") if idle_t else None
    return retry, timeout, (float(run_t) if run_t else None)


# ----------------------------------------------------------------------------------- caché [F2]
class EstudioCache(SqliteCache):
    """SqliteCache que da por fallo un acierto cuya salida apunta a ficheros bajo BASE que ya no existen."""

    def __init__(self, *, path: str, bases=(BASE,)):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        super().__init__(path=path)
        self._bases = tuple(str(b).rstrip("/") + "/" for b in bases)

    def _faltan(self, v) -> bool:
        if isinstance(v, str):
            return v.startswith(self._bases) and len(v) < 4096 and not Path(v).exists()
        if isinstance(v, dict):
            return any(self._faltan(x) for x in v.values())
        if isinstance(v, (list, tuple, deque, set)):
            return any(self._faltan(x) for x in v)
        return False

    def get(self, keys):
        vals = super().get(keys)
        return {k: v for k, v in vals.items() if not self._faltan(v)}

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


def cache_path(home: Path, slug: str) -> Path:
    return Path(home) / "data" / "cache" / f"{slug}.sqlite"


def cache_info(home: Path, slug: str) -> dict:
    p = cache_path(home, slug)
    if not p.exists():
        return {"entradas": 0, "bytes": 0}
    con = sqlite3.connect(p)
    try:
        n = con.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    except sqlite3.Error:
        n = 0
    finally:
        con.close()
    size = sum(f.stat().st_size for f in p.parent.glob(p.name + "*"))
    return {"entradas": n, "bytes": size}


def cache_clear(home: Path, slug: str, node: str | None = None):
    p = cache_path(home, slug)
    if not p.exists():
        return
    if node is None:
        for f in p.parent.glob(p.name + "*"):
            f.unlink(missing_ok=True)
        return
    con = sqlite3.connect(p)
    try:
        con.execute("DELETE FROM cache WHERE ns = ? OR ns LIKE ?", (node, f"%,{node}"))
        con.commit()
    except sqlite3.Error:
        pass
    finally:
        con.close()


def _cache_key(slug, spec_id, sha, cfg, inputs):
    def key(state):
        st = flat(state) if isinstance(state, dict) else {}
        raw = json.dumps([slug, spec_id, sha, cfg, {k: st.get(k) for k in inputs}], sort_keys=True,
                         ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()
    return key


def _error_handler(error_to: str):
    async def handler(state, error: LGNodeError):
        return Command(goto=error_to, update={"error": {"node": error.node, "msg": f"{type(error.error).__name__}: "
                                                                                   f"{error.error}"}})
    handler.__name__ = f"error_a_{error_to}"
    handler.__annotations__ = {"error": LGNodeError}   # compiler.py usa anotaciones diferidas: LangGraph inyecta por tipo
    return handler


# ----------------------------------------------------------------------------------- build
@dataclass
class Built:
    graph: Any
    canales: dict
    avisos: list
    module_shas: dict
    specs: dict


def build(project: dict, *, assistant: dict | None = None, checkpointer=None, store=None, cache=None,
          env: Env | None = None, extra_keys=(), load_modules: bool = True, _stack: tuple = ()) -> Built:
    env = env or Env.default()
    assistant = assistant or {"context": {}, "node_overrides": {}}
    v = validate(project, env.registry, env.settings, extra_keys, assistant.get("node_overrides"), env.load_project,
                 _stack)
    if v["errores"]:
        raise ValidationError(v["errores"], v["avisos"])
    canales = v["canales"]
    fields = {}
    for k, r in canales.items():
        fields[k] = Annotated[Any, REDUCER_FN[r]] if r in REDUCER_FN else Any
    Estado = TypedDict("Estado", fields, total=False)
    g = StateGraph(Estado)
    nodes, edges = project.get("nodes") or {}, project.get("edges") or []
    specs, shas = {}, {}
    declared_schema = set(project.get("state_schema") or {})
    for nid, n in nodes.items():
        info = v["infos"][nid]
        mod, snap = None, None
        if load_modules:
            if info.meta.get("exec") == "local":
                info, mod = env.registry.load(info.file)
            else:
                info, snap = env.registry.snapshot(info.file)
        shas[info.file] = info.sha
        ins, outs = v["io"][nid]
        retry, timeout, run_t = _policies(n, info.meta, env.settings)
        destinos = tuple(dict.fromkeys(e["dst"] for e in edges if nid in _src_list(e) and not e.get("cond")))
        spec = NodeSpec(id=nid, label=n.get("label") or nid, file=info.file, info=info, module=mod, snap=snap, node=n,
                        inputs=ins, allowed=set(outs) | declared_schema, canales=canales, run_timeout=run_t,
                        destinos=destinos if es_enrutador(info.meta) else ())
        specs[nid] = spec
        extra = {}
        dests = list(spec.destinos)
        if n.get("cache") is not None:
            cfg_eff = resolve_cfg(info.config, n.get("config") or {},
                                  (assistant.get("node_overrides") or {}).get(nid), env.settings)
            extra["cache_policy"] = CachePolicy(
                key_func=_cache_key(project.get("slug") or "_", nid, info.sha, cfg_eff, ins),
                ttl=int(n["cache"]["ttl"]) if n["cache"].get("ttl") else None)
        if n.get("defer"):
            extra["defer"] = True
        if n.get("error_to"):
            extra["error_handler"] = _error_handler(n["error_to"])
            dests.append(n["error_to"])
        if dests:
            extra["destinations"] = tuple(dict.fromkeys(END if d == "__end__" else d for d in dests))
        if info.meta.get("kind") == "agent":
            cfg_a = resolve_cfg(info.config, n.get("config") or {},
                                (assistant.get("node_overrides") or {}).get(nid), env.settings)
            extra_tools = []
            for fl in v["flujos"].get(nid) or []:                # [D9] flujos como herramientas
                child = build(fl["project"], assistant={"context": assistant.get("context") or {},
                                                        "node_overrides": {}},
                              checkpointer=False, store=store, env=env, load_modules=load_modules,
                              _stack=(*_stack, project.get("slug") or "_"))
                for f, sh in child.module_shas.items():
                    shas.setdefault(f, sh)
                extra_tools.append(_tool_flujo(fl, child.graph))
            fn = make_sub_wrapper(spec, _build_agent(cfg_a, env, assistant.get("context") or {}, extra_tools),
                                  ["messages"], ["messages"])
        elif nid in v["subs"]:
            sub = v["subs"][nid]
            child = build(sub["project"], assistant={"context": assistant.get("context") or {}, "node_overrides": {}},
                          checkpointer=None, store=store, env=env, load_modules=load_modules,
                          _stack=(*_stack, project.get("slug") or "_"))
            for f, sh in child.module_shas.items():
                shas.setdefault(f, sh)
            fn = make_sub_wrapper(spec, child.graph, sub["ins"], sub["outs"])
        else:
            fn = make_wrapper(spec, env, project.get("slug") or "_", assistant)
        g.add_node(nid, fn, retry_policy=retry, timeout=timeout,
                   metadata={"module": info.file, "label": spec.label}, **extra)

    def tgt(x):
        return END if x == "__end__" else x

    for nid in v["entradas"] if not any("__start__" in _src_list(e) for e in edges) else []:
        g.add_edge(START, nid)
    conds: dict[str, list] = {}
    for e in edges:
        srcs = _src_list(e)
        if len(srcs) == 1 and srcs[0] in specs and specs[srcs[0]].destinos:
            continue   # destino de Command/Send: no es una arista fija
        if e.get("cond"):
            conds.setdefault(srcs[0], []).append(e)
        elif len(srcs) > 1:
            g.add_edge(srcs, tgt(e["dst"]))
        else:
            g.add_edge(START if srcs[0] == "__start__" else srcs[0], tgt(e["dst"]))
    for src, es in conds.items():
        g.add_conditional_edges(START if src == "__start__" else src, _router(src, es),
                                {e["dst"]: tgt(e["dst"]) for e in es})
    graph = g.compile(checkpointer=checkpointer, store=store, cache=cache, name=project.get("slug") or "proyecto")
    return Built(graph=graph, canales=canales, avisos=v["avisos"], module_shas=shas, specs=specs)


LIMITE_SALIDA_FLUJO = 4000


def _tool_flujo(fl: dict, graph):
    """[D9] Herramienta que ejecuta un flujo (compilado con checkpointer=False: no deja checkpoints en el hilo; sus
    eventos llegan con ns ≠ "" porque se invoca con la config del padre). Errores e interrupts → texto para el LLM."""
    from langchain_core.tools import StructuredTool
    from pydantic import Field, create_model

    p = fl["project"]
    outs = fl["outs"]

    async def ejecutar(config: RunnableConfig, **kw):
        try:
            res = await graph.ainvoke({k: kw.get(k) for k in fl["ins"]}, config)
        except GraphInterrupt:
            return "Error: el flujo pide revisión humana; no se puede usar como herramienta"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return f"Error en el flujo '{fl['slug']}': {type(e).__name__}: {e}"
        st = flat(res or {})
        val = {k: st[k] for k in outs if k in st} if outs else {k: v for k, v in st.items() if k != "messages"}
        txt = json.dumps(val, ensure_ascii=False, default=jenc)
        return txt if len(txt) <= LIMITE_SALIDA_FLUJO else txt[:LIMITE_SALIDA_FLUJO] + "…"

    Args = create_model("Args_" + fl["tool"], **{k: (str, Field(..., description=k)) for k in fl["ins"]})
    return StructuredTool.from_function(coroutine=ejecutar, name=fl["tool"],
                                        description=(p.get("desc") or "").strip() or p.get("name") or fl["slug"],
                                        args_schema=Args)


def _build_agent(cfg: dict, env: Env, context: dict, extra_tools=()):
    """[F2] kind agent: LLM con herramientas ↔ ToolNode, con tools_condition. Subgrafo sin checkpointer propio."""
    from langchain_core.messages import SystemMessage
    from langchain_ollama import ChatOllama
    from langgraph.prebuilt import ToolNode, tools_condition

    import herramientas
    nombres = keys_from_value(cfg.get("herramientas"))
    tools = herramientas.load(env.home / "modules" / "tools", nombres or None)
    mcp = keys_from_value(cfg.get("mcp"))
    if mcp:                                                      # [F3] herramientas de servidores MCP
        import mcp_cliente
        tools = list(tools) + mcp_cliente.herramientas_sync(env.settings.get("mcp_servidores") or {}, mcp)
    if extra_tools:                                              # [D9] flujos como herramientas
        tools = list(tools) + list(extra_tools)
    llm = ChatOllama(model=cfg.get("modelo") or env.settings.get("ollama_model") or "qwen2.5:7b",
                     base_url=cfg.get("url") or env.settings.get("ollama_url") or None,
                     temperature=float(cfg.get("temperatura") or 0), keep_alive=env.settings.get("keep_alive", "5m"))
    if tools:
        llm = llm.bind_tools(tools)
    sistema = cfg.get("sistema") or ""
    for k, val in (context or {}).items():
        sistema = sistema.replace("{" + k + "}", "" if val is None else str(val))

    class EstadoAgente(TypedDict, total=False):
        messages: Annotated[list, add_messages]

    async def llm_node(state):
        msgs = ([SystemMessage(sistema)] if sistema else []) + list(state.get("messages") or [])
        resp = None
        async for ch in llm.astream(msgs):
            resp = ch if resp is None else resp + ch
        return {"messages": [resp]} if resp is not None else {}

    g = StateGraph(EstadoAgente)
    g.add_node("llm", llm_node)
    g.add_edge(START, "llm")
    if tools:
        g.add_node("herramientas", ToolNode(tools))
        g.add_conditional_edges("llm", tools_condition, {"tools": "herramientas", END: END})
        g.add_edge("herramientas", "llm")
    else:
        g.add_edge("llm", END)
    return g.compile(checkpointer=None, name="agente")


def _router(src, es):
    normales = [(e["dst"], e.get("id"), compile(e["cond"], f"<cond {e.get('id')}>", "eval"))
                for e in es if e["cond"] != "else"]
    otro = next((e["dst"] for e in es if e["cond"] == "else"), None)

    def router(state):
        res = []
        for dst, eid, code in normales:
            try:
                ok = eval_cond(code, state)
            except Exception as ex:
                raise NodoError(f"Error al evaluar la condición de la arista {eid}: {type(ex).__name__}: {ex}") from None
            if ok and dst not in res:
                res.append(dst)
        if not res:
            if otro is None:
                raise NodoError(f"Ninguna condición desde '{src}' es verdadera y no hay 'else'")
            res = [otro]
        return res

    router.__name__ = f"ruta_{src}"
    return router
