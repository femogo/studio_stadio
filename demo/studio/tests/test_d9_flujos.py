"""D9 (DISENO.md §13.7): flujos como herramientas del «Agente con herramientas» — criterio G2 (sin LLM)."""
import json
import os
import subprocess
import textwrap

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from compiler import Env, build, validate
from conftest import PY_ESTUDIO, esperar_run
from modules import Registry
from test_f2_api import cadena, hilo, proyecto, py_node
from test_f3_export import exportar

# Modelo falso con bind_tools: primero pide flujo__hijo(texto="hola"); con el ToolMessage, responde su contenido.
FALSO = textwrap.dedent('''
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class ModeloFalso(BaseChatModel):
        herramienta: str = "flujo__hijo"
        texto: str = "hola"

        @property
        def _llm_type(self):
            return "falso"

        def bind_tools(self, tools, **kw):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kw):
            ult = messages[-1]
            if isinstance(ult, ToolMessage):
                msg = AIMessage(content="resultado: " + str(ult.content))
            else:
                msg = AIMessage(content="", tool_calls=[{"name": self.herramienta, "args": {"texto": self.texto},
                                                         "id": "llamada1"}])
            return ChatResult(generations=[ChatGeneration(message=msg)])

    HERRAMIENTA = ["flujo__hijo"]

    def falso(**kw):
        return ModeloFalso(herramienta=HERRAMIENTA[0])
''')
_ns = {}
exec(FALSO, _ns)


def hijo_proj(slug="hijo", nodes=None, edges=None):
    return {"slug": slug, "name": "Cuenta letras", "desc": "Cuenta las letras de un texto",
            "nodes": nodes or {"cuenta": py_node("return {'longitud': len(state['texto'])}", "texto", "longitud")},
            "edges": edges or cadena("cuenta"),
            "state_schema": {"texto": {"reducer": "replace", "input": True},
                             "longitud": {"reducer": "replace", "output": True}}}


def padre_proj(flujos="hijo", slug="padre"):
    return {"slug": slug, "name": "Padre",
            "nodes": {"agente": {"module": "agente_react.py", "x": 0, "y": 0,
                                 "config": {"flujos": flujos, "herramientas": "calculadora"}}},
            "edges": cadena("agente"),
            "state_schema": {"messages": {"reducer": "messages", "input": True, "output": True}}}


@pytest.fixture
def env(home, monkeypatch):
    import langchain_ollama
    monkeypatch.setattr(langchain_ollama, "ChatOllama", _ns["falso"])
    proyectos = {}
    e = Env(home=home, settings={"default_timeout": 60}, registry=Registry(home / "modules", home / "data/modsnap"))
    e.load_project = lambda s: proyectos[s]
    e.proyectos = proyectos
    return e


async def correr(env, padre):
    saver = InMemorySaver()
    b = build(padre, env=env, checkpointer=saver)
    cfg = {"configurable": {"thread_id": "t"}}
    custom = []
    async for ns, mode, ch in b.graph.astream({"messages": [{"role": "user", "content": "x"}]}, cfg,
                                              stream_mode=["custom"], subgraphs=True):
        custom.append((ns, ch))
    st = await b.graph.aget_state(cfg)
    return st, custom, saver


async def test_g2_flujo_como_herramienta(env):
    env.proyectos["hijo"] = hijo_proj()
    st, custom, saver = await correr(env, padre_proj())
    msgs = st.values["messages"]
    tool = [m for m in msgs if m.type == "tool"]
    assert len(tool) == 1 and tool[0].name == "flujo__hijo"
    assert json.loads(tool[0].content) == {"longitud": 4}
    assert msgs[-1].content == 'resultado: {"longitud": 4}'
    # eventos del nodo del hijo con ns ≠ ""
    del_hijo = [(ns, ch) for ns, ch in custom if isinstance(ch, dict) and ch.get("node") == "cuenta"]
    assert del_hijo and all(ns for ns, _ in del_hijo)
    assert {ch["type"] for _, ch in del_hijo} >= {"node.start", "node.end"}
    # el hilo no guarda checkpoints del hijo (compilado con checkpointer=False)
    nss = {c.config["configurable"].get("checkpoint_ns", "") for c in saver.list(None)}
    assert not any("herramientas:" in n for n in nss), nss


async def test_g2_slug_con_guiones_y_sin_input(env):
    h = hijo_proj("cuenta-mucho")
    h["state_schema"]["texto"].pop("input")      # sin claves input: true → argumento único 'texto'
    env.proyectos["cuenta-mucho"] = h
    _ns["HERRAMIENTA"][0] = "flujo__cuenta_mucho"
    try:
        st, _, _ = await correr(env, padre_proj("cuenta-mucho"))
    finally:
        _ns["HERRAMIENTA"][0] = "flujo__hijo"
    tool = [m for m in st.values["messages"] if m.type == "tool"]
    assert tool[0].name == "flujo__cuenta_mucho" and json.loads(tool[0].content) == {"longitud": 4}


async def test_g2_revision_humana_no_admitida(env):
    env.proyectos["hijo"] = hijo_proj(nodes={"rev": {"module": "revision_humana.py", "x": 0, "y": 0}},
                                      edges=cadena("rev"))
    st, _, _ = await correr(env, padre_proj())
    tool = [m for m in st.values["messages"] if m.type == "tool"]
    assert "revisión humana" in tool[0].content
    assert st.next == () and not st.interrupts


def test_g2_validacion(env):
    reg = env.registry
    env.proyectos["con-msgs"] = hijo_proj("con-msgs")
    env.proyectos["con-msgs"]["state_schema"]["messages"] = {"reducer": "messages"}
    for flujos, texto in [("noexiste", "no existe"), ("padre", "Ciclo"), ("con-msgs", "messages")]:
        v = validate(padre_proj(flujos), reg, env.settings, loader=env.load_project)
        assert any(texto in e["msg"] and e["node"] == "agente" for e in v["errores"]), (flujos, v["errores"])
    # ciclo indirecto: padre → hijo (subflujo) → padre
    env.proyectos["padre"] = padre_proj("hijo")
    env.proyectos["hijo"] = hijo_proj(nodes={"vuelta": {"module": "subgrafo.py", "x": 0, "y": 0,
                                                        "config": {"proyecto": "padre"}}}, edges=cadena("vuelta"))
    v = validate(env.proyectos["padre"], reg, env.settings, loader=env.load_project)
    assert any("Ciclo" in e["msg"] for e in v["errores"]), v["errores"]


# ------------------------------------------------------------------------------------------------ API + exportación
def test_g2_api_validacion_y_export(api, tmp_path):
    h = proyecto(api, "Hijo D9", {"cuenta": py_node("return {'longitud': len(state['texto'])}", "texto", "longitud")},
                 cadena("cuenta"), desc="Cuenta las letras de un texto",
                 state_schema={"texto": {"reducer": "replace", "input": True},
                               "longitud": {"reducer": "replace", "output": True}})
    nodes = {"agente": {"module": "agente_react.py", "x": 0, "y": 0, "config": {"flujos": "no-existe"}}}
    p = proyecto(api, "Padre D9", nodes, cadena("agente"),
                 state_schema={"messages": {"reducer": "messages", "input": True, "output": True}})
    errs = api.get(f"/api/projects/{p['slug']}/validate").json()["errores"]
    assert any("no existe" in e["msg"] for e in errs)
    r = api.post(f"/api/threads/{hilo(api, p['slug'])}/runs", json={"input": {"messages": []}})
    run = esperar_run(api, r.json()["run_id"])          # la ejecución se acepta y falla al compilar
    assert run["status"] == "error" and "no existe" in json.dumps(run, ensure_ascii=False), run
    p["nodes"]["agente"]["config"] = {"flujos": h["slug"], "herramientas": "calculadora"}
    p = api.put(f"/api/projects/{p['slug']}", json=p).json()
    assert api.get(f"/api/projects/{p['slug']}/validate").json()["errores"] == []
    # exportación .py: incluye el flujo hijo y da el mismo resultado en un proceso nuevo (modelo falso)
    carpeta = exportar(api, p["slug"], tmp_path / "x")
    assert (carpeta / "projects" / f"{h['slug']}.json").exists()
    script = tmp_path / "ejecutar_d9.py"
    script.write_text(FALSO + textwrap.dedent(f'''
        import asyncio, json, sys
        sys.path.insert(0, sys.argv[1])
        import langchain_ollama
        langchain_ollama.ChatOllama = falso
        HERRAMIENTA[0] = {"flujo__" + h["slug"].replace("-", "_")!r}
        from langgraph.checkpoint.memory import InMemorySaver
        import graph as G
        app = G.graph.builder.compile(checkpointer=InMemorySaver())
        async def main():
            r = await app.ainvoke({{"messages": [{{"role": "user", "content": "x"}}]}},
                                  {{"configurable": {{"thread_id": "1"}}}})
            return r["messages"][-1].content
        print("@@OUT " + json.dumps(asyncio.run(main())))
    '''))
    env = {k: v for k, v in os.environ.items() if k != "STUDIO_HOME"}
    out = subprocess.run([PY_ESTUDIO, str(script), str(carpeta)], cwd=str(tmp_path), env=env, capture_output=True,
                         text=True, timeout=180)
    linea = [ln for ln in out.stdout.splitlines() if ln.startswith("@@OUT ")]
    assert linea, out.stdout + out.stderr
    assert json.loads(linea[-1][6:]) == 'resultado: {"longitud": 4}'
