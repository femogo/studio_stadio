"""Fase 3: criterios que necesitan Ollama de verdad (marcador llm, excluido por defecto)."""
import pytest
from test_f2_api import cadena, hilo, lanzar, proyecto, py_node
from test_f3_api import esperar_exp

pytestmark = pytest.mark.llm


def test_c1_juez_llm(api):
    nodes = {"eco": py_node("return {'respuesta': state.get('texto')}", "texto", "respuesta")}
    p = proyecto(api, "Eco juez", nodes, cadena("eco"),
                 state_schema={"texto": {"input": True}, "respuesta": {"output": True}})
    did = api.post("/api/datasets", json={"name": "DS saludos"}).json()["dataset_id"]
    api.post(f"/api/datasets/{did}/examples", json=[{"input": {"texto": "hola"}}, {"input": {"texto": "7"}}])
    juez = {"name": "saludo", "type": "llm",
            "prompt": "Respuesta de un asistente: {output}\n¿La respuesta es un saludo (hola, buenos días…)? "
                      "Puntúa 1 si es un saludo y 0 si no lo es."}
    x = esperar_exp(api, api.post("/api/experiments", json={"dataset_id": did, "project": p["slug"],
                                                             "evaluators": [juez]}).json()["experiment_id"], 300)
    assert x["status"] == "success", x
    sc = {r["input"]["texto"]: r["scores"]["saludo"]["score"] for r in x["results"]}
    assert sc["hola"] is not None and sc["hola"] >= 0.5, x["results"]
    assert sc["7"] is not None and sc["7"] < 0.5, x["results"]


def test_c3_playground_llm(api):
    p = api.post("/api/projects", json={"name": "Playground EC", "template": "escritor_critico"}).json()
    r = api.post(f"/api/projects/{p['slug']}/nodes/escritor/playground", json={
        "state": {"tema": "la biblioteca de Alejandría", "texto": "", "critica": "ninguna"},
        "variantes": [{"temperatura": 0, "num_predict": 60},
                      {"prompt": "Escribe una frase sobre {tema}.", "num_predict": 40}]}, timeout=300)
    assert r.status_code == 200, r.text
    a, b = r.json()["resultados"]
    assert "la biblioteca de Alejandría" in a["prompt"] and b["prompt"] == "Escribe una frase sobre la biblioteca de Alejandría."
    for x in (a, b):
        assert x["error"] is None and x["tokens"] > 0 and x["salida"]["texto"].strip()
    assert api.get(f"/api/threads?project={p['slug']}&ocultos=1").json() == []
    nodes = {"rt": {"module": "router_llm.py", "x": 0, "y": 0,
                    "config": {"texto": "{texto}", "opciones": {"cocina": "recetas y comida", "deporte": "deportes"}}},
             "cocina": py_node("return {}"), "deporte": py_node("return {}")}
    edges = [{"id": "e0", "src": "__start__", "dst": "rt"}, {"id": "e1", "src": "rt", "dst": "cocina"},
             {"id": "e2", "src": "rt", "dst": "deporte"}, {"id": "e3", "src": "cocina", "dst": "__end__"},
             {"id": "e4", "src": "deporte", "dst": "__end__"}]
    pr = proyecto(api, "Playground router", nodes, edges, state_schema={"texto": {"input": True}})
    r = api.post(f"/api/projects/{pr['slug']}/nodes/rt/playground",
                 json={"state": {"texto": "¿Cómo se hace un gazpacho?"}}, timeout=300).json()["resultados"][0]
    assert r["salida"] == "cocina", r


def test_c6_agente_con_mcp(api, tmp_path):
    from test_f3_api import configurar_mcp, quitar_mcp
    configurar_mcp(api, tmp_path)
    try:
        nodes = {"ag": {"module": "agente_react.py", "x": 0, "y": 0,
                        "config": {"herramientas": "recordar", "mcp": "prueba", "temperatura": 0,
                                   "sistema": "Eres un asistente. Para sumar usa SIEMPRE la herramienta prueba__sumar."}}}
        p = proyecto(api, "Agente MCP", nodes, cadena("ag"), state_schema={"messages": {"reducer": "messages"}})
        tid = hilo(api, p["slug"])
        from conftest import esperar_run
        from test_f2_api import lanzar
        run = esperar_run(api, lanzar(api, tid, input={"messages": [
            {"role": "user", "content": "¿Cuánto es 2+3? Usa la herramienta para calcularlo."}]})["run_id"], timeout=300)
        assert run["status"] == "success", run
        msgs = api.get(f"/api/threads/{tid}/messages").json()
        llamadas = [tc["name"] for m in msgs for tc in (m.get("tool_calls") or [])]
        assert "prueba__sumar" in llamadas, msgs
        assert any(m["role"] == "tool" and "5" in str(m["content"]) for m in msgs)
        assert "5" in str(msgs[-1]["content"])
    finally:
        quitar_mcp(api)
