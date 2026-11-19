"""Paso 1c: compiler.py — estado, aristas, validación y wrappers (sin GPU)."""
import asyncio
import json
import os
import time
import subprocess

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError, NodeTimeoutError

from compiler import Env, NodoError, NodoProcesoError, ValidationError, build, cond_keys, resolve_cfg, validate
from modules import Registry

MODS = {
    "suma.py": 'META={"name":"Suma","inputs":["n"],"outputs":["n"]}\nCONFIG=[{"key":"k","type":"number","default":1}]\n'
               'def run(state, cfg, ctx):\n    ctx.log("suma", cfg["k"])\n    return {"n": (state.get("n") or 0) + cfg["k"]}\n',
    "marca.py": 'META={"name":"Marca","outputs":["log"]}\nCONFIG=[{"key":"t","type":"text","default":"x"}]\n'
                'async def run(state, cfg, ctx):\n    ctx.progress(1)\n    return {"log": [cfg["t"]]}\n',
    "malo.py": 'META={"outputs":["a"]}\nCONFIG=[{"key":"modo","default":"clave"}]\ndef run(state, cfg, ctx):\n'
               '    if cfg["modo"] == "clave": return {"zz": 1}\n    return {"a": object()}\n',
    "lento_local.py": 'import asyncio\nMETA={"outputs":["a"]}\nasync def run(state, cfg, ctx):\n'
                      '    await asyncio.sleep(cfg.get("s", 1))\n    return {"a": 1}\n',
}


@pytest.fixture
def env(home):
    for f, src in MODS.items():
        (home / "modules" / f).write_text(src)
    return Env(home=home, settings={"default_timeout": 60, "video": {"voz": "ef_dora"}},
               registry=Registry(home / "modules", home / "data/modsnap"))


def proj(nodes, edges, **kw):
    return {"slug": "p", "nodes": {k: ({"module": v} if isinstance(v, str) else v) for k, v in nodes.items()},
            "edges": [dict(e, id=e.get("id", f"e{i}")) for i, e in enumerate(edges)], **kw}


async def correr(b, inp, tid="t", **kw):
    cfg = {"configurable": {"thread_id": tid}, "recursion_limit": kw.pop("recursion_limit", 50)}
    custom = []
    async for mode, ch in b.graph.astream(inp, cfg, stream_mode=["updates", "custom"], **kw):
        if mode == "custom":
            custom.append(ch)
    return (await b.graph.aget_state(cfg)), custom


def test_resolve_cfg():
    fields = [{"key": "voz", "type": "select", "default": "", "setting": "video.voz"},
              {"key": "n", "type": "number", "default": "3"}, {"key": "tam", "type": "select", "options": [256, 512],
                                                               "default": 512},
              {"key": "f", "type": "number", "default": 0.5}, {"key": "c", "type": "checkbox", "default": "true"}]
    r = resolve_cfg(fields, {"tam": "256"}, None, {"video": {"voz": "em_alex"}})
    assert r == {"voz": "em_alex", "n": 3, "tam": 256, "f": 0.5, "c": True}


def test_cond_keys():
    assert cond_keys("s.get('a') and s['b'] or state['c'] > len(s.get('d') or '')") == {"a", "b", "c", "d"}


async def test_lineal_condicional_y_canales(env):
    p = proj({"entrada": {"module": "entrada.py", "config": {"valores": {"n": 0, "modo": "x"}}},
              "uno": "suma.py", "dos": {"module": "suma.py", "config": {"k": 10}}, "m": "marca.py"},
             [{"src": "__start__", "dst": "entrada"}, {"src": "entrada", "dst": "uno"},
              {"src": "uno", "dst": "dos", "cond": "s.get('modo') == 'y'"}, {"src": "uno", "dst": "m", "cond": "else"},
              {"src": "dos", "dst": "__end__"}, {"src": "m", "dst": "__end__"}],
             state_schema={"log": {"reducer": "append"}})
    b = build(p, env=env, checkpointer=InMemorySaver())
    assert b.canales["log"] == "append" and b.canales["n"] == "replace" and "modo" in b.canales
    st, custom = await correr(b, {"modo": "x"})
    assert st.values["n"] == 1 and st.values["log"] == ["x"] and st.values["modo"] == "x"   # gana la entrada
    tipos = {(c["type"], c["node"]) for c in custom}
    assert ("node.start", "uno") in tipos and ("node.log", "uno") in tipos and ("node.end", "m") in tipos
    st, _ = await correr(b, {"modo": "y"}, tid="t2")
    assert st.values["n"] == 11


async def test_fanout_join_y_ciclo_con_recursion(env):
    p = proj({"a": "marca.py", "b": {"module": "marca.py", "config": {"t": "b"}},
              "c": {"module": "marca.py", "config": {"t": "c"}}, "j": {"module": "marca.py", "config": {"t": "j"}}},
             [{"src": "__start__", "dst": "a"}, {"src": "a", "dst": "b", "cond": "True"},
              {"src": "a", "dst": "c", "cond": "True"}, {"src": ["b", "c"], "dst": "j"}, {"src": "j", "dst": "__end__"}],
             state_schema={"log": {"reducer": "append"}})
    b = build(p, env=env, checkpointer=InMemorySaver())
    st, _ = await correr(b, {})
    assert st.values["log"][0] == "x" and sorted(st.values["log"][1:3]) == ["b", "c"] and st.values["log"][3] == "j"
    # ciclo: suma hasta n >= 3; con recursion_limit=2 → GraphRecursionError
    p = proj({"s": "suma.py"}, [{"src": "__start__", "dst": "s"}, {"src": "s", "dst": "s", "cond": "s.get('n') < 3"},
                                {"src": "s", "dst": "__end__", "cond": "else"}])
    b = build(p, env=env, checkpointer=InMemorySaver())
    st, _ = await correr(b, {"n": 0})
    assert st.values["n"] == 3
    with pytest.raises(GraphRecursionError):
        await correr(b, {"n": 0}, tid="r", recursion_limit=2)


async def test_sin_condicion_verdadera_y_sin_else_es_error(env):
    p = proj({"s": "suma.py", "t": "suma.py"}, [{"src": "__start__", "dst": "s"},
                                                {"src": "s", "dst": "t", "cond": "s.get('n') > 100"}])
    b = build(p, env=env, checkpointer=InMemorySaver())
    with pytest.raises(NodoError, match="Ninguna condición"):
        await correr(b, {"n": 0})


def test_entrada_implicita(env):
    p = proj({"e": {"module": "entrada.py", "config": {"valores": {"x": 1}}}, "s": "suma.py"},
             [{"src": "e", "dst": "s"}])
    assert validate(p, env.registry)["entradas"] == ["e"]
    p = proj({"a": "suma.py", "b": "suma.py"}, [{"src": "a", "dst": "b"}])
    assert validate(p, env.registry)["entradas"] == ["a"]


@pytest.mark.parametrize("nodes,edges,msg", [
    ({"a": "suma.py"}, [{"src": "__start__", "dst": "a"}, {"src": "a", "dst": "__end__", "cond": "else"},
                        {"src": "a", "dst": "a", "cond": "else"}], "aristas 'else'"),
    ({"a": "suma.py"}, [{"src": "__start__", "dst": "a"}, {"src": ["a", "fantasma"], "dst": "__end__"}],
     "inexistente: 'fantasma'"),
    ({"a": "suma.py"}, [{"src": "__start__", "dst": "a"}, {"src": "a", "dst": "__end__", "cond": "s.get('x' =="}],
     "error de sintaxis"),
    ({"a": "suma.py", "b": "suma.py"}, [{"src": "__start__", "dst": "a"}, {"src": "__start__", "dst": "b"},
                                        {"src": ["a", "b"], "dst": "__end__", "cond": "True"}], "join"),
    ({"a": "noexiste.py"}, [{"src": "__start__", "dst": "a"}], "no existe"),
    ({"n": "suma.py"}, [{"src": "__start__", "dst": "n"}], "coincide con una clave"),
    ({"Mal": "suma.py"}, [{"src": "__start__", "dst": "Mal"}], "no válido"),
])
def test_validacion_errores(env, nodes, edges, msg):
    with pytest.raises(ValidationError) as e:
        build(proj(nodes, edges), env=env)
    assert any(msg in x["msg"] for x in e.value.errores), e.value.errores


def test_modulo_local_con_import_torch_no_se_puede_usar(env):
    (env.home / "modules/gpu_local.py").write_text('import torch\nMETA={}\ndef run(s,c,x): pass\n')
    with pytest.raises(ValidationError, match="torch"):
        build(proj({"a": "gpu_local.py"}, [{"src": "__start__", "dst": "a"}]), env=env)


def test_avisos(env):
    p = proj({"a": "suma.py", "b": "suma.py", "c": "suma.py", "z": "suma.py"},
             [{"src": "__start__", "dst": "a"}, {"src": "a", "dst": "b", "cond": "True"}, {"src": "a", "dst": "c"},
              {"src": ["b", "c"], "dst": "__end__"}])
    av = [x["msg"] for x in validate(p, env.registry)["avisos"]]
    assert any("'z' no es alcanzable" in m for m in av) and any("join" in m for m in av)


@pytest.mark.parametrize("modo,msg", [("clave", "`zz` no está declarada"), ("obj", "serializable")])
async def test_salida_invalida(env, modo, msg):
    b = build(proj({"m": {"module": "malo.py", "config": {"modo": modo}}}, [{"src": "__start__", "dst": "m"}]),
              env=env, checkpointer=InMemorySaver())
    with pytest.raises(NodoError, match=msg):
        await correr(b, {})


async def test_proceso_resultado_y_artefactos(env):
    p = proj({"l": {"module": "prueba_lenta.py", "config": {"segundos": 0.3, "clave_salida": "r"}}},
             [{"src": "__start__", "dst": "l"}])
    b = build(p, env=env, checkpointer=InMemorySaver())
    st, custom = await correr(b, {})
    assert st.values["r"] == "l ok"
    prog = [c for c in custom if c["type"] == "node.progress"]
    assert prog and prog[-1]["data"]["value"] == 1.0
    tid = custom[0]["task_id"]
    assert (env.home / f"projects/p/threads/t/l/{tid}/_in.json").exists()
    assert (env.home / f"projects/p/threads/t/l/{tid}/_done.json").exists()


async def test_proceso_error_con_reintentos(env):
    p = proj({"l": {"module": "prueba_lenta.py", "config": {"segundos": 0.1, "fallar": True},
                    "retry": {"max_attempts": 2, "initial_interval": 0.01, "jitter": False}}},
             [{"src": "__start__", "dst": "l"}])
    b = build(p, env=env, checkpointer=InMemorySaver())
    custom = []
    with pytest.raises(NodoProcesoError, match="Fallo pedido"):
        async for mode, ch in b.graph.astream({}, {"configurable": {"thread_id": "e"}}, stream_mode=["custom"]):
            custom.append(ch)
    starts = [c["data"]["attempt"] for c in custom if c["type"] == "node.start"]
    assert starts == [1, 2]
    assert [c["data"]["status"] for c in custom if c["type"] == "node.end"] == ["error", "error"]


async def test_timeout_run_e_idle(env):
    p = proj({"l": {"module": "lento_local.py", "config": {"s": 2}, "timeout": {"run": 0.3, "idle": None}}},
             [{"src": "__start__", "dst": "l"}])
    b = build(p, env=env, checkpointer=InMemorySaver())
    with pytest.raises(NodeTimeoutError) as e:
        await correr(b, {})
    assert e.value.kind == "run"
    p["nodes"]["l"]["timeout"] = {"run": None, "idle": 0.3}
    b = build(p, env=env, checkpointer=InMemorySaver())
    with pytest.raises(NodeTimeoutError) as e:
        await correr(b, {}, tid="i")
    assert e.value.kind == "idle"
    # prueba_lenta emite @@P cada 0,1 s → el heartbeat evita el idle timeout
    p = proj({"l": {"module": "prueba_lenta.py", "config": {"segundos": 1}, "timeout": {"run": 10, "idle": 0.5}}},
             [{"src": "__start__", "dst": "l"}])
    b = build(p, env=env, checkpointer=InMemorySaver())
    st, _ = await correr(b, {}, tid="h")
    assert st.values["prueba"] == "l ok"


async def test_cancelar_mata_el_subproceso(env):
    pg = []
    env.on_pgid = lambda run, pid, vivo: pg.append((pid, vivo))
    p = proj({"l": {"module": "prueba_lenta.py", "config": {"segundos": 30}}}, [{"src": "__start__", "dst": "l"}])
    b = build(p, env=env, checkpointer=InMemorySaver())
    t = asyncio.create_task(correr(b, {}))
    await asyncio.sleep(1.5)
    assert pg and pg[0][1]
    t0 = time.time()
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert time.time() - t0 < 3
    pid = pg[0][0]
    with pytest.raises(ProcessLookupError):
        os.killpg(pid, 0)


async def test_gpu_exclusiva_espera(env):
    await env.gpu.acquire("exclusive")
    (env.home / "modules/g.py").write_text('META={"gpu":"shared","outputs":["a"]}\ndef run(s,c,x): return {"a": 1}\n')
    b = build(proj({"g": "g.py"}, [{"src": "__start__", "dst": "g"}]), env=env, checkpointer=InMemorySaver())
    t = asyncio.create_task(correr(b, {}))
    await asyncio.sleep(1.3)
    assert not t.done()
    await env.gpu.release("exclusive")
    st, custom = await asyncio.wait_for(t, 5)
    assert st.values["a"] == 1
    assert any(c["data"].get("esperando_gpu") for c in custom if c["type"] == "node.start")


def test_mermaid(env):
    b = build(proj({"a": "suma.py", "b": "suma.py"}, [{"src": "__start__", "dst": "a"},
                                                      {"src": "a", "dst": "b", "cond": "True"},
                                                      {"src": "a", "dst": "__end__", "cond": "else"}]), env=env)
    m = b.graph.get_graph().draw_mermaid()
    assert "a -.-> b" in m or "a -.->" in m
