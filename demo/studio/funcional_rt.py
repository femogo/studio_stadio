"""[F3] El mismo proyecto con la API funcional de LangGraph (@entrypoint/@task) (DISENO.md §6.4).

Un @task por nodo (llama al wrapper del compilador) y un @entrypoint que recorre el grafo por supersteps con las reglas
de aristas de §3.4: condiciones en orden con fan-out y `else`, joins que esperan a todas sus fuentes y ciclos, con el
límite `recursion_limit`. No admite Send/Command, router, subgrafo, agente, caché, defer ni error_to.
"""
from __future__ import annotations

from langgraph.config import get_config
from langgraph.errors import GraphRecursionError
from langgraph.func import entrypoint, task
from langgraph.runtime import get_runtime
from langgraph.types import Overwrite

from compiler import (REDUCER_FN, Env, NodoError, _policies, _router, _src_list, build, es_enrutador, flat,
                      make_wrapper)

NO_ADMITIDOS = {"router", "subgraph", "agent", "tools"}


def motivos_no_funcional(project: dict, registry) -> list[str]:
    """Lista vacía = exportable a la API funcional."""
    out = []
    for nid, n in (project.get("nodes") or {}).items():
        info = registry.get(n.get("module") or "")
        meta = (info.meta if info else {}) or {}
        if meta.get("kind") in NO_ADMITIDOS:
            out.append(f"el nodo '{nid}' es de tipo {meta['kind']}")
        elif meta.get("command") or es_enrutador(meta):
            out.append(f"el nodo '{nid}' enruta con Send/Command")
        if n.get("cache") is not None:
            out.append(f"el nodo '{nid}' usa caché")
        if n.get("defer"):
            out.append(f"el nodo '{nid}' es diferido (defer)")
        if n.get("error_to"):
            out.append(f"el nodo '{nid}' tiene manejador de errores (error_to)")
    return out


def _aplicar(state: dict, upd: dict | None, canales: dict):
    for k, v in (upd or {}).items():
        if isinstance(v, Overwrite):
            state[k] = v.value
            continue
        r = canales.get(k, "replace")
        fn = REDUCER_FN.get(r)
        if fn is None:
            state[k] = v
        elif r == "messages":
            state[k] = fn(state.get(k) or [], v)
        else:
            state[k] = fn(state.get(k), v)


def build_funcional(project: dict, *, env: Env | None = None, assistant: dict | None = None, checkpointer=None,
                    store=None):
    env = env or Env.default()
    assistant = assistant or {"context": {}, "node_overrides": {}}
    motivos = motivos_no_funcional(project, env.registry)
    if motivos:
        raise NodoError("El proyecto no se puede exportar a la API funcional: " + "; ".join(motivos))
    built = build(project, env=env, assistant=assistant)
    specs, canales = built.specs, built.canales
    slug = project.get("slug") or "_"
    edges = project.get("edges") or []

    tareas = {}
    for nid, spec in specs.items():
        w = make_wrapper(spec, env, slug, assistant)
        retry, _, _ = _policies(spec.node, spec.info.meta, env.settings)

        def hacer(w=w):
            async def nodo(state: dict):
                return await w(state, get_runtime(), get_config())
            return nodo
        fn = hacer()
        fn.__name__ = nid
        tareas[nid] = task(name=nid, retry_policy=retry)(fn)

    simples, conds, joins = {}, {}, []
    for e in edges:
        srcs = _src_list(e)
        if e.get("cond"):
            conds.setdefault(srcs[0], []).append(e)
        elif len(srcs) > 1:
            joins.append((set(srcs), e["dst"]))
        else:
            simples.setdefault(srcs[0], []).append(e["dst"])
    routers = {src: _router(src, es) for src, es in conds.items()}
    desde_start = [e["dst"] for e in edges if "__start__" in _src_list(e)]
    if not desde_start:
        con_entrada = {e["dst"] for e in edges}
        desde_start = [n for n, s in specs.items() if s.info.meta.get("kind") == "input"] or \
            [n for n in specs if n not in con_entrada]

    def siguientes(origenes: list[str], state: dict, vistos_join: list[set]) -> list[str]:
        out = []

        def add(d):
            if d not in out:
                out.append(d)
        for src in origenes:
            for d in simples.get(src, []):
                add(d)
            if src in routers:
                for d in routers[src](flat(state)):
                    add(d)
            for i, (fuentes, dst) in enumerate(joins):
                if src in fuentes:
                    vistos_join[i].add(src)
                    if vistos_join[i] >= fuentes:
                        vistos_join[i].clear()
                        add(dst)
        return out

    @entrypoint(checkpointer=checkpointer, store=store)
    async def graph(inp: dict, config=None):
        state: dict = {}
        _aplicar(state, inp or {}, canales)
        limite = int((config or {}).get("recursion_limit") or 25)
        vistos_join = [set() for _ in joins]
        frontera = [d for d in dict.fromkeys(desde_start) if d != "__end__"]
        if "__start__" in routers:
            frontera = [d for d in dict.fromkeys(frontera + routers["__start__"](flat(state))) if d != "__end__"]
        pasos = 0
        while frontera:
            pasos += 1
            if pasos > limite:
                raise GraphRecursionError(f"Se alcanzó el límite de recursión ({limite}) sin llegar a __end__")
            foto = dict(state)
            futuros = [(nid, tareas[nid](foto)) for nid in frontera]          # todas lanzadas: en paralelo
            resultados = [(nid, await f) for nid, f in futuros]
            for _, upd in resultados:
                _aplicar(state, upd, canales)
            frontera = [d for d in siguientes([n for n, _ in resultados], state, vistos_join) if d != "__end__"]
        return flat(state)

    return graph
