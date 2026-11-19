"""Fase 2: criterios que necesitan Ollama de verdad (marcador llm, excluido por defecto)."""
import pytest
from conftest import esperar_run
from test_f2_api import cadena, estado, hilo, lanzar, proyecto, py_node

pytestmark = pytest.mark.llm


def test_a6_router_llm(api):
    opciones = {"tiempo": "preguntas sobre el clima o el tiempo que hace",
                "cocina": "recetas, comida o cocina",
                "deporte": "fútbol, deportes o partidos"}
    nodes = {"rt": {"module": "router_llm.py", "x": 0, "y": 0,
                    "config": {"texto": "{texto}", "opciones": opciones}},
             **{k: py_node(f"return {{'rama': '{k}'}}", "", "rama") for k in opciones}}
    edges = [{"id": "e0", "src": "__start__", "dst": "rt"}]
    edges += [{"id": f"r{k}", "src": "rt", "dst": k} for k in opciones]
    edges += [{"id": f"f{k}", "src": k, "dst": "__end__"} for k in opciones]
    p = proyecto(api, "Router LLM", nodes, edges, state_schema={"texto": {"input": True}})
    casos = {"¿Va a llover mañana en Madrid?": "tiempo",
             "¿Cómo se hace una tortilla de patatas?": "cocina",
             "¿Quién ganó el partido del Real Madrid?": "deporte"}
    for frase, esperado in casos.items():
        tid = hilo(api, p["slug"])
        run = esperar_run(api, lanzar(api, tid, input={"texto": frase})["run_id"], timeout=180)
        assert run["status"] == "success", run
        assert estado(api, tid)["rama"] == esperado, frase


def test_a3_memoria_entre_hilos(api):
    nodes = {"mem": {"module": "memoria.py", "x": 0, "y": 0,
                     "config": {"accion": "guardar", "namespace": "usuarios/{usuario}", "valor": "{texto}"}}}
    p = proyecto(api, "Memoria guardar", nodes, cadena("mem"),
                 state_schema={"usuario": {"input": True}, "texto": {"input": True}})
    for t in ("Me encanta la paella valenciana, es mi comida favorita", "Mi perro se llama Toby y es un beagle",
              "Trabajo de fontanero en Valencia"):
        tid = hilo(api, p["slug"])
        assert esperar_run(api, lanzar(api, tid, input={"usuario": "ana", "texto": t})["run_id"])["status"] == "success"
    nodes = {"mem": {"module": "memoria.py", "x": 0, "y": 0,
                     "config": {"accion": "buscar", "namespace": "usuarios/{usuario}", "consulta": "{pregunta}",
                                "limite": 3}}}
    p2 = proyecto(api, "Memoria buscar", nodes, cadena("mem"),
                  state_schema={"usuario": {"input": True}, "pregunta": {"input": True}})
    tid = hilo(api, p2["slug"])
    run = esperar_run(api, lanzar(api, tid, input={"usuario": "ana", "pregunta": "¿Qué comida le gusta?"})["run_id"])
    assert run["status"] == "success", run
    rec = estado(api, tid)["recuerdos"]
    assert "paella" in rec[0]["texto"] and rec[0]["score"] >= rec[1]["score"]
    res = api.get("/api/store/items", params={"ns": "usuarios/ana", "query": "¿Cómo se llama su perro?"}).json()
    assert "Toby" in res[0]["value"]["texto"]


def _chat(api, tid, texto, **kw):
    r = lanzar(api, tid, input={"messages": [{"role": "user", "content": texto}]}, **kw)
    run = esperar_run(api, r["run_id"], timeout=300)
    return r["run_id"], run


def test_a7_react_chat(api):
    p = api.post("/api/projects", json={"name": "Chat", "template": "react_chat"}).json()
    tid = hilo(api, p["slug"])
    run_id, run = _chat(api, tid, "¿Cuánto es 37 por 91?")
    assert run["status"] == "success", run
    msgs = api.get(f"/api/threads/{tid}/messages").json()
    assert any(c["name"] == "calculadora" for m in msgs for c in m.get("tool_calls") or [])
    assert any(m["role"] == "tool" for m in msgs)
    assert "3367" in msgs[-1]["content"].replace(".", "").replace(",", "")
    evs = api.get(f"/api/threads/{tid}/events", params={"run": run_id, "types": "token,node.end",
                                                         "limit": 5000}).json()
    fin = next(i for i, e in enumerate(evs) if e["type"] == "node.end" and e["node"] == "agente" and not e["ns"])
    assert len([e for e in evs[:fin] if e["type"] == "token" and e["node"] == "agente"]) >= 5
    # memoria entre hilos, con el usuario del contexto del asistente
    a = api.post(f"/api/projects/{p['slug']}/assistants", json={"name": "Ana", "context": {"usuario": "ana-chat"}}).json()
    t1 = hilo(api, p["slug"])
    _, run = _chat(api, t1, "Hola, me llamo Ana. Recuérdalo, por favor.", assistant_id=a["assistant_id"])
    assert run["status"] == "success", run
    t2 = hilo(api, p["slug"])
    _, run = _chat(api, t2, "¿Cómo me llamo?", assistant_id=a["assistant_id"])
    assert run["status"] == "success", run
    assert "Ana" in api.get(f"/api/threads/{t2}/messages").json()[-1]["content"]
    # confirmación humana dentro del chat
    t3 = hilo(api, p["slug"])
    _, run = _chat(api, t3, "Borra todos mis datos. Pide confirmación antes con la herramienta.")
    assert run["status"] == "interrupted", run
    st = api.get(f"/api/threads/{t3}/state").json()
    assert st["interrupts"] and st["interrupts"][0]["response_schema"]["required"] == ["aprobado"]
    r = lanzar(api, t3, resume={st["interrupts"][0]["id"]: {"aprobado": False, "comentario": "mejor no"}})
    assert esperar_run(api, r["run_id"], timeout=300)["status"] == "success"
    tool_msgs = [m for m in api.get(f"/api/threads/{t3}/messages").json() if m["role"] == "tool"]
    assert any("NO" in m["content"] for m in tool_msgs)


def test_a7_chat_ollama(api):
    nodes = {"chat": {"module": "chat_ollama.py", "x": 0, "y": 0, "config": {"temperatura": 0}}}
    p = proyecto(api, "Chat simple", nodes, cadena("chat"),
                 state_schema={"messages": {"reducer": "messages", "input": True}})
    tid = hilo(api, p["slug"])
    run_id, run = _chat(api, tid, "Di hola en una frase corta.")
    assert run["status"] == "success", run
    toks = api.get(f"/api/threads/{tid}/events", params={"run": run_id, "types": "token"}).json()
    assert len(toks) >= 2 and all(t["node"] == "chat" for t in toks)
    assert api.get(f"/api/threads/{tid}/messages").json()[-1]["role"] == "assistant"
