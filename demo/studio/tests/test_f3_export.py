"""Fase 3: C4 (exportar .py / langgraph.json) y C5 (API funcional) (DISENO.md §6.4, §13.5)."""
import io
import json
import os
import subprocess
import textwrap
import zipfile
from pathlib import Path

from conftest import PY_ESTUDIO, STUDIO, esperar_run
from test_f2_api import cadena, crear_modulo, estado, hilo, lanzar, proyecto, py_node  # noqa: F401

CRITICO = '''"""Crítico falso (local): puntúa por la longitud del texto."""
META = {"name": "Crítico falso", "category": "Pruebas", "kind": "task", "exec": "local", "gpu": False,
        "inputs": ["texto"], "outputs": ["puntuacion", "critica"]}
CONFIG = []
def run(state, cfg, ctx):
    n = len(state.get("texto") or "")
    return {"puntuacion": n, "critica": f"longitud {n}"}
'''

# Ejecuta graph.py / funcional.py de una exportación en un proceso nuevo y cuenta las ejecuciones por nodo.
EJECUTOR = textwrap.dedent('''
    import asyncio, json, sys
    sys.path.insert(0, sys.argv[1])
    cual, entrada, limite = sys.argv[2], json.loads(sys.argv[3]), int(sys.argv[4])
    resume = json.loads(sys.argv[5]) if len(sys.argv) > 5 else None
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    import graph as G
    if cual == "graph":
        app = G.graph.builder.compile(checkpointer=InMemorySaver())
    else:
        from funcional_rt import build_funcional
        app = build_funcional(G.PROYECTO, env=G.env, assistant=G.ASISTENTE, checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "1"}, "recursion_limit": limite}
    cuenta, interrupts = {}, []

    async def correr(x):
        async for ch in app.astream(x, cfg, stream_mode="updates"):
            for k, v in ch.items():
                if k == "__interrupt__":
                    interrupts.extend(i.value for i in v)
                elif k not in ("graph",):
                    cuenta[k] = cuenta.get(k, 0) + 1

    async def main():
        try:
            await correr(entrada)
            antes = dict(cuenta)
            if resume is not None and interrupts:
                await correr(Command(resume=resume))
            st = (await app.aget_state(cfg)).values
            if cual != "graph":
                st = st if isinstance(st, dict) else {}
            return {"estado": {k: v for k, v in st.items() if v is not None}, "cuenta": cuenta, "antes": antes,
                    "interrupts": interrupts}
        except Exception as e:
            return {"error": type(e).__name__ + ": " + str(e)}
    out = asyncio.run(main())
    out["fastapi"] = "fastapi" in sys.modules or "server" in sys.modules
    import compiler
    out["compiler"] = compiler.__file__
    print("@@OUT " + json.dumps(out, ensure_ascii=False, default=str))
''')


def exportar(api, slug, destino: Path) -> Path:
    r = api.get(f"/api/projects/{slug}/export", params={"format": "py"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    zipfile.ZipFile(io.BytesIO(r.content)).extractall(destino)
    return destino / f"export_{slug}"


def ejecutar(carpeta: Path, cual, entrada, limite=50, resume=None) -> dict:
    script = carpeta.parent / "ejecutor.py"
    script.write_text(EJECUTOR)
    env = {k: v for k, v in os.environ.items() if k != "STUDIO_HOME"}
    args = [PY_ESTUDIO, str(script), str(carpeta), cual, json.dumps(entrada), str(limite)]
    if resume is not None:
        args.append(json.dumps(resume))
    r = subprocess.run(args, cwd=str(carpeta.parent), env=env, capture_output=True, text=True, timeout=180)
    linea = [ln for ln in r.stdout.splitlines() if ln.startswith("@@OUT ")]
    assert linea, r.stdout + r.stderr
    out = json.loads(linea[-1][6:])
    assert not out["fastapi"]
    assert out["compiler"].startswith(str(carpeta)), out["compiler"]
    return out


def proyecto_bucle(api, nombre):
    crit = crear_modulo(api, f"Crítico {nombre}", CRITICO)
    nodes = {
        "entrada": {"module": "entrada.py", "x": 0, "y": 0, "config": {"valores": {"texto": "", "iteracion": 0}}},
        "escritor": py_node('return {"texto": (state.get("texto") or "") + "ab", '
                            '"iteracion": int(state.get("iteracion") or 0) + 1}', "texto,iteracion",
                            "texto,iteracion"),
        "critico": {"module": crit, "x": 0, "y": 0},
        "salida": {"module": "salida.py", "x": 0, "y": 0, "config": {"mostrar": [{"clave": "texto", "como": "texto"}]}},
    }
    edges = [{"id": "e0", "src": "__start__", "dst": "entrada"}, {"id": "e1", "src": "entrada", "dst": "escritor"},
             {"id": "e2", "src": "escritor", "dst": "critico"},
             {"id": "e3", "src": "critico", "dst": "escritor", "cond": "s.get('puntuacion', 0) < 6"},
             {"id": "e4", "src": "critico", "dst": "salida", "cond": "else"},
             {"id": "e5", "src": "salida", "dst": "__end__"}]
    return proyecto(api, nombre, nodes, edges, state_schema={"tema": {"input": True}})


# ------------------------------------------------------------------------------------------------ C4
def test_c4_exportar_py_bucle(api, tmp_path):
    p = proyecto_bucle(api, "Export bucle")
    tid = hilo(api, p["slug"])
    assert esperar_run(api, lanzar(api, tid, input={"tema": "t"})["run_id"])["status"] == "success"
    ref = estado(api, tid)
    assert ref["texto"] == "ababab" and ref["iteracion"] == 3
    carpeta = exportar(api, p["slug"], tmp_path)
    assert not str(carpeta).startswith(str(STUDIO))
    lj = json.loads((carpeta / "langgraph.json").read_text())
    for g in lj["graphs"].values():
        f, var = g.split(":")
        assert (carpeta / f).exists() and f"\n{var} = " in (carpeta / f).read_text()
    assert len(lj["graphs"]) == 2
    assert "token" not in json.loads((carpeta / "settings.json").read_text())
    for f in ("studio_runtime/compiler.py", "studio_runtime/runner.py", "modules/codigo_python.py",
              f"projects/{p['slug']}.json", "requirements.txt", "LEEME.md", "funcional.py"):
        assert (carpeta / f).exists(), f
    out = ejecutar(carpeta, "graph", {"tema": "t"})
    assert "error" not in out, out
    assert out["estado"] == ref
    assert out["cuenta"]["escritor"] == 3 and out["cuenta"]["critico"] == 3
    # el subproceso usó la exportación
    assert list((carpeta / "data/modsnap").glob("*.py"))
    assert list((carpeta / "projects" / p["slug"] / "threads").glob("*/escritor/*/_in.json"))


def test_c4_exportar_py_subgrafo(api, tmp_path):
    hijo = proyecto(api, "Export hijo", {"dob": py_node('return {"res": state["n"] * 2}', "n", "res")},
                    cadena("dob"), state_schema={"n": {"input": True}, "res": {"output": True}})
    nodes = {"sub": {"module": "subgrafo.py", "x": 0, "y": 0, "config": {"proyecto": hijo["slug"]}},
             "grande": py_node('return {"tam": "grande"}', "", "tam"),
             "peque": py_node('return {"tam": "pequeño"}', "", "tam")}
    edges = [{"id": "e0", "src": "__start__", "dst": "sub"},
             {"id": "e1", "src": "sub", "dst": "grande", "cond": "s.get('res', 0) > 10"},
             {"id": "e2", "src": "sub", "dst": "peque", "cond": "else"},
             {"id": "e3", "src": "grande", "dst": "__end__"}, {"id": "e4", "src": "peque", "dst": "__end__"}]
    p = proyecto(api, "Export padre", nodes, edges, state_schema={"n": {"input": True}})
    tid = hilo(api, p["slug"])
    assert esperar_run(api, lanzar(api, tid, input={"n": 7})["run_id"])["status"] == "success"
    ref = estado(api, tid)
    assert ref["tam"] == "grande"
    carpeta = exportar(api, p["slug"], tmp_path)
    assert (carpeta / f"projects/{hijo['slug']}.json").exists()
    assert not (carpeta / "funcional.py").exists()
    assert "subgraph" in (carpeta / "LEEME.md").read_text()
    assert len(json.loads((carpeta / "langgraph.json").read_text())["graphs"]) == 1
    out = ejecutar(carpeta, "graph", {"n": 7})
    assert "error" not in out, out
    assert out["estado"] == ref


# ------------------------------------------------------------------------------------------------ C5
def test_c5_funcional_join_y_bucle(api, tmp_path):
    nodes = {"a": py_node('return {"la": ["a"]}', "", "la"), "a2": py_node('return {"la": ["a2"]}', "", "la"),
             "b": py_node('return {"lb": "b"}', "", "lb"),
             "c": py_node('return {"fin": len(state.get("la") or []) * 10 + len(state.get("lb") or "")}',
                          "la,lb", "fin")}
    edges = [{"id": "e0", "src": "__start__", "dst": "a"}, {"id": "e1", "src": "__start__", "dst": "b"},
             {"id": "e2", "src": "a", "dst": "a2"}, {"id": "e3", "src": ["a2", "b"], "dst": "c"},
             {"id": "e4", "src": "c", "dst": "__end__"}]
    p = proyecto(api, "Funcional join", nodes, edges, state_schema={"la": {"reducer": "append"}})
    r = api.get(f"/api/projects/{p['slug']}/export", params={"format": "funcional"})
    assert r.status_code == 200 and "build_funcional" in r.text
    carpeta = exportar(api, p["slug"], tmp_path / "join")
    g = ejecutar(carpeta, "graph", {})
    f = ejecutar(carpeta, "funcional", {})
    assert "error" not in g and "error" not in f, (g, f)
    assert g["estado"] == f["estado"] and g["estado"]["fin"] == 21
    assert g["cuenta"] == f["cuenta"] == {"a": 1, "a2": 1, "b": 1, "c": 1}
    # bucle con condición y else
    pb = proyecto_bucle(api, "Funcional bucle")
    carpeta = exportar(api, pb["slug"], tmp_path / "bucle")
    g = ejecutar(carpeta, "graph", {"tema": "t"})
    f = ejecutar(carpeta, "funcional", {"tema": "t"})
    assert g["estado"] == f["estado"] and f["estado"]["iteracion"] == 3
    assert g["cuenta"] == f["cuenta"]
    g = ejecutar(carpeta, "graph", {"tema": "t"}, limite=4)
    f = ejecutar(carpeta, "funcional", {"tema": "t"}, limite=4)
    assert g["error"].startswith("GraphRecursionError") and f["error"].startswith("GraphRecursionError")


def test_c5_funcional_interrupt(api, tmp_path):
    marca = tmp_path / "llamadas.txt"
    contar = f'open({str(marca)!r}, "a").write("x"); return {{"texto": "borrador"}}'
    nodes = {"escribe": py_node(contar, "", "texto"),
             "rev": {"module": "revision_humana.py", "x": 0, "y": 0, "config": {"campo": "texto"}},
             "fin": py_node('return {"final": state.get("texto")}', "texto", "final")}
    p = proyecto(api, "Funcional interrupt", nodes, cadena("escribe", "rev", "fin"))
    carpeta = exportar(api, p["slug"], tmp_path / "int")
    f = ejecutar(carpeta, "funcional", {}, resume={"aprobado": True, "valor": "editado"})
    assert "error" not in f, f
    assert f["interrupts"] and f["interrupts"][0]["valor"] == "borrador"
    assert f["estado"]["final"] == "editado" and f["estado"]["aprobado"] is True
    assert marca.read_text() == "x"          # «escribe» no se repite al reanudar
    g = ejecutar(carpeta, "graph", {}, resume={"aprobado": True, "valor": "editado"})
    assert g["estado"] == f["estado"] and marca.read_text() == "xx"


def test_c5_funcional_no_admitido(api, tmp_path):
    nodes = {"mapa": {"module": "mapa_send.py", "x": 0, "y": 0, "config": {"lista": "items", "destino": "uno"}},
             "uno": py_node('return {"r": [state.get("item")]}', "item", "r"),
             "lento": {"module": "prueba_lenta.py", "x": 0, "y": 0, "config": {"segundos": 0.1},
                       "cache": {"ttl": 60}}}
    edges = [{"id": "e0", "src": "__start__", "dst": "mapa"}, {"id": "e1", "src": "mapa", "dst": "uno"},
             {"id": "e2", "src": "uno", "dst": "lento"}, {"id": "e3", "src": "lento", "dst": "__end__"}]
    p = proyecto(api, "Funcional no", nodes, edges, state_schema={"items": {"input": True},
                                                                    "r": {"reducer": "append"}})
    r = api.get(f"/api/projects/{p['slug']}/export", params={"format": "funcional", "check": 1}).json()
    assert r["ok"] is False and len(r["motivos"]) == 2
    r = api.get(f"/api/projects/{p['slug']}/export", params={"format": "funcional"})
    assert r.status_code == 400 and r.json()["code"] == "unsupported"
    assert "Send/Command" in r.json()["detail"] and "caché" in r.json()["detail"]
    carpeta = exportar(api, p["slug"], tmp_path)
    assert not (carpeta / "funcional.py").exists()
    leeme = (carpeta / "LEEME.md").read_text()
    assert "Send/Command" in leeme and "caché" in leeme
