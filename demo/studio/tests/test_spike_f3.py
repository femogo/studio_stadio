"""Paso 3a: comprobación en ejecución de lo que usa F3 (DISENO.md §6.4, §13.5)."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.func import entrypoint, task
from langgraph.types import Command, interrupt

from conftest import PY_ESTUDIO, STUDIO

pytestmark = pytest.mark.spike


# ---------------------------------------------------------------------------------------------- croniter
def test_croniter_segundos_al_final():
    from croniter import croniter

    now = time.time()
    it = croniter("* * * * * */2", now)
    a = it.get_next(float)
    b = it.get_next(float)
    assert 0 < a - now <= 2.0 and b - a == 2.0
    with pytest.raises(Exception):
        croniter("no es cron", now)
    assert croniter.is_valid("*/5 * * * *") and not croniter.is_valid("61 * * * *")
    # cinco campos: siguiente minuto exacto
    n = croniter("* * * * *", datetime.fromtimestamp(now)).get_next(float)
    assert n % 60 == 0 and 0 < n - now <= 60


# ---------------------------------------------------------------------------------------------- API funcional
async def test_funcional_paralelo_interrupt_y_reanudar_sin_repetir():
    calls = {"a": 0, "b": 0, "rev": 0}

    @task
    async def a(x):
        calls["a"] += 1
        await asyncio.sleep(0.1)
        return x + "a"

    @task
    async def b(x):
        calls["b"] += 1
        await asyncio.sleep(0.1)
        return x + "b"

    @task
    async def rev(x):
        calls["rev"] += 1
        r = interrupt({"valor": x})
        return r

    @entrypoint(checkpointer=InMemorySaver())
    async def flujo(inp: dict, config=None):
        t0 = time.time()
        fa, fb = a(inp["x"]), b(inp["x"])
        ra, rb = await fa, await fb
        paralelo = time.time() - t0 < 0.18
        r = await rev(ra + rb)
        return {"r": r, "paralelo": paralelo, "limite": config.get("recursion_limit")}

    cfg = {"configurable": {"thread_id": "t1"}, "recursion_limit": 7}
    ev = [c async for c in flujo.astream({"x": "-"}, cfg, stream_mode="updates")]
    ints = [c["__interrupt__"] for c in ev if "__interrupt__" in c]
    assert ints and ints[0][0].value == {"valor": "-a-b"}
    assert calls == {"a": 1, "b": 1, "rev": 1}
    out = await flujo.ainvoke(Command(resume="ok"), cfg)
    assert out["r"] == "ok" and out["paralelo"] and out["limite"] == 7
    assert calls["a"] == 1 and calls["b"] == 1          # no se repiten al reanudar


async def test_funcional_tarea_llamada_varias_veces_en_bucle():
    n = {"k": 0}

    @task
    async def paso(i):
        n["k"] += 1
        return i + 1

    @entrypoint(checkpointer=InMemorySaver())
    async def bucle(inp: dict):
        i = 0
        while i < inp["hasta"]:
            i = await paso(i)
        return {"i": i}

    assert (await bucle.ainvoke({"hasta": 4}, {"configurable": {"thread_id": "x"}}))["i"] == 4
    assert n["k"] == 4


# ---------------------------------------------------------------------------------------------- MCP
SERVIDOR_MCP = textwrap.dedent('''
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("prueba")

    @mcp.tool()
    def sumar(a: int, b: int) -> int:
        """Suma dos enteros."""
        return a + b

    @mcp.tool()
    def eco(texto: str) -> str:
        """Devuelve el texto."""
        return texto

    if __name__ == "__main__":
        mcp.run()
''')


async def test_mcp_listar_llamar_y_servidor_caido(tmp_path):
    from langchain_mcp_adapters.client import MultiServerMCPClient

    srv = tmp_path / "srv.py"
    srv.write_text(SERVIDOR_MCP)
    cli = MultiServerMCPClient({"prueba": {"transport": "stdio", "command": PY_ESTUDIO, "args": [str(srv)]}})
    t0 = time.time()
    tools = await cli.get_tools(server_name="prueba")
    assert sorted(t.name for t in tools) == ["eco", "sumar"]
    sumar = next(t for t in tools if t.name == "sumar")
    assert "a" in sumar.args_schema["properties"]
    r = await sumar.ainvoke({"a": 2, "b": 3})
    assert "5" in json.dumps(r, default=str)
    assert time.time() - t0 < 15
    malo = MultiServerMCPClient({"malo": {"transport": "stdio", "command": "/no/existe", "args": []}})
    with pytest.raises(BaseException):
        await asyncio.wait_for(malo.get_tools(server_name="malo"), 10)


# ---------------------------------------------------------------------------------------------- build() copiado
def test_build_desde_copia_fuera_de_studio(tmp_path):
    exp = tmp_path / "export_x"
    rt = exp / "studio_runtime"
    rt.mkdir(parents=True)
    for f in ("compiler.py", "modules.py", "runner.py", "herramientas.py"):
        shutil.copy(STUDIO / f, rt / f)
    (exp / "modules").mkdir()
    for f in ("entrada.py", "codigo_python.py", "salida.py"):
        shutil.copy(STUDIO / "modules" / f, exp / "modules" / f)
    proj = {"slug": "x", "name": "x", "state_schema": {"texto": {"reducer": "replace", "input": True}},
            "nodes": {"cod": {"module": "codigo_python.py", "config": {
                "codigo": 'return {"resultado": len(state.get("texto") or "")}', "salidas": "resultado",
                "entradas": "texto"}}},
            "edges": [{"id": "e1", "src": "__start__", "dst": "cod"}, {"id": "e2", "src": "cod", "dst": "__end__"}]}
    (exp / "project.json").write_text(json.dumps(proj))
    (exp / "graph.py").write_text(textwrap.dedent(f'''
        import json, os, sys
        from pathlib import Path
        AQUI = Path(__file__).resolve().parent
        os.environ.setdefault("STUDIO_HOME", str(AQUI))
        sys.path.insert(0, str(AQUI / "studio_runtime"))
        from compiler import Env, build
        env = Env.default(AQUI, {{"python_estudio": {PY_ESTUDIO!r}}})
        graph = build(json.loads((AQUI / "project.json").read_text()), env=env).graph
    '''))
    prueba = textwrap.dedent('''
        import asyncio, sys
        sys.path.insert(0, sys.argv[1])
        import graph as G
        from langgraph.checkpoint.memory import InMemorySaver
        g = G.graph.builder.compile(checkpointer=InMemorySaver())
        out = asyncio.run(g.ainvoke({"texto": "hola"}, {"configurable": {"thread_id": "1"}}))
        assert out["resultado"] == 4, out
        assert "fastapi" not in sys.modules and "server" not in sys.modules
        import compiler
        assert compiler.__file__.startswith(sys.argv[1]), compiler.__file__
        print("OK")
    ''')
    (tmp_path / "p.py").write_text(prueba)
    env = {k: v for k, v in os.environ.items() if k != "STUDIO_HOME"}
    r = subprocess.run([PY_ESTUDIO, str(tmp_path / "p.py"), str(exp)], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout + r.stderr
    # el subproceso usó los módulos de la exportación
    assert list(exp.glob("data/modsnap/*.py"))


# ---------------------------------------------------------------------------------------------- Send → subgrafo
async def test_send_hacia_nodo_subgrafo(home):
    from compiler import Env, build

    hijo = {"slug": "hijo", "name": "hijo",
            "state_schema": {"item": {"reducer": "replace", "input": True}, "res": {"reducer": "replace", "output": True}},
            "nodes": {"dob": {"module": "codigo_python.py", "config": {
                "codigo": 'import time; time.sleep(0.3); return {"res": [state["item"] * 2]}',
                "salidas": "res", "entradas": "item"}}},
            "edges": [{"id": "e1", "src": "__start__", "dst": "dob"}, {"id": "e2", "src": "dob", "dst": "__end__"}]}
    padre = {"slug": "padre", "name": "padre",
             "state_schema": {"items": {"reducer": "replace", "input": True}, "res": {"reducer": "append"},
                              "item": {"reducer": "replace"}},
             "nodes": {"mapa": {"module": "mapa_send.py", "config": {"lista": "items", "destino": "sub"}},
                       "sub": {"module": "subgrafo.py", "config": {"proyecto": "hijo"}}},
             "edges": [{"id": "e1", "src": "__start__", "dst": "mapa"}, {"id": "e2", "src": "mapa", "dst": "sub"},
                       {"id": "e3", "src": "sub", "dst": "__end__"}]}
    env = Env.default(home, {"python_estudio": PY_ESTUDIO})
    env.load_project = lambda s: {"hijo": hijo}[s]
    g = build(padre, env=env, checkpointer=InMemorySaver()).graph
    t0 = time.time()
    out = await g.ainvoke({"items": [1, 2, 3]}, {"configurable": {"thread_id": "s"}})
    assert sorted(out["res"]) == [2, 4, 6], out
    print("duración", time.time() - t0)
