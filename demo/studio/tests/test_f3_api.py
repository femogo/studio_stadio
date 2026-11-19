"""Fase 3: criterios C1–C6 por HTTP contra un servidor real en el puerto 8799 (DISENO.md §6.4, §13.5)."""
import json
import time

import pytest
from conftest import esperar_run
from test_f2_api import cadena, crear_modulo, estado, hilo, lanzar, proyecto  # noqa: F401

CODIGO_LONG = 'return {"longitud": len(state.get("texto") or "")}'


def proyecto_longitud(api, nombre="Longitud", codigo=CODIGO_LONG):
    nodes = {"cod": {"module": "codigo_python.py", "x": 0, "y": 0,
                     "config": {"codigo": codigo, "salidas": "longitud", "entradas": "texto"}}}
    return proyecto(api, nombre, nodes, cadena("cod"),
                    state_schema={"texto": {"reducer": "replace", "input": True},
                                  "longitud": {"reducer": "replace", "output": True}})


# ------------------------------------------------------------------------------------------------ C1: datasets
def test_c1_datasets_y_ejemplos(api):
    p = proyecto_longitud(api, "Longitud DS")
    r = api.post("/api/datasets", json={"name": "Longitudes", "desc": "d", "project": p["slug"]})
    assert r.status_code == 201, r.text
    ds = r.json()
    did = ds["dataset_id"]
    assert ds["n_examples"] == 0 and ds["project"] == p["slug"]
    assert api.post("/api/datasets", json={"name": "Longitudes"}).status_code == 400
    assert api.post("/api/datasets", json={"name": " "}).status_code == 400
    assert api.post("/api/datasets", json={"name": "x", "project": "no-existe"}).status_code == 404
    r = api.post(f"/api/datasets/{did}/examples", json=[
        {"input": {"texto": "hola"}, "expected": {"longitud": 4}},
        {"input": {"texto": "adiós!"}, "expected": {"longitud": 6}}])
    assert r.status_code == 201 and len(r.json()) == 2
    assert api.post(f"/api/datasets/{did}/examples", json={"expected": {}}).status_code == 400
    # from-run
    tid = hilo(api, p["slug"])
    run = lanzar(api, tid, input={"texto": "abc"})
    assert esperar_run(api, run["run_id"])["status"] == "success"
    r = api.post(f"/api/datasets/{did}/examples/from-run", json={"run_id": run["run_id"]})
    assert r.status_code == 201, r.text
    ej = r.json()
    assert ej["input"] == {"texto": "abc"} and ej["expected"] == {"longitud": 3} and ej["source_run"] == run["run_id"]
    cont = lanzar(api, tid, input=None)
    esperar_run(api, cont["run_id"])
    assert api.post(f"/api/datasets/{did}/examples/from-run", json={"run_id": cont["run_id"]}).status_code == 400
    assert api.post(f"/api/datasets/{did}/examples/from-run", json={"run_id": "nada"}).status_code == 404
    ejs = api.get(f"/api/datasets/{did}/examples").json()
    assert [e["input"]["texto"] for e in ejs] == ["hola", "adiós!", "abc"]
    # editar y borrar
    r = api.patch(f"/api/examples/{ejs[0]['example_id']}", json={"metadata": {"nota": 1}})
    assert r.status_code == 200 and r.json()["metadata"] == {"nota": 1} and r.json()["input"] == {"texto": "hola"}
    assert api.get(f"/api/datasets/{did}").json()["n_examples"] == 3
    # export / import JSONL
    txt = api.get(f"/api/datasets/{did}/export").text
    assert txt.splitlines()[0].startswith('{"__dataset__"') and len(txt.splitlines()) == 4
    r = api.post("/api/datasets/import", content=txt.encode(), headers={"Content-Type": "application/x-ndjson"})
    assert r.status_code == 201, r.text
    imp = r.json()
    assert imp["name"] == "Longitudes (2)" and imp["n_examples"] == 3
    a = [(e["input"], e["expected"], e["metadata"]) for e in api.get(f"/api/datasets/{did}/examples").json()]
    b = [(e["input"], e["expected"], e["metadata"]) for e in
         api.get(f"/api/datasets/{imp['dataset_id']}/examples").json()]
    assert a == b
    assert api.post("/api/datasets/import", content=b"{no json").status_code == 400
    # renombrar y borrar
    assert api.patch(f"/api/datasets/{imp['dataset_id']}", json={"name": "Longitudes"}).status_code == 400
    assert api.patch(f"/api/datasets/{imp['dataset_id']}", json={"name": "Copia"}).json()["name"] == "Copia"
    assert api.delete(f"/api/examples/{ejs[1]['example_id']}").status_code == 204
    assert api.get(f"/api/datasets/{did}").json()["n_examples"] == 2
    assert api.delete(f"/api/datasets/{imp['dataset_id']}").status_code == 204
    assert api.get(f"/api/datasets/{imp['dataset_id']}").status_code == 404
    assert any(d["dataset_id"] == did for d in api.get("/api/datasets").json())


# ------------------------------------------------------------------------------------------------ C1: experimentos
def esperar_exp(api, eid, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        x = api.get(f"/api/experiments/{eid}").json()
        if x["status"] not in ("pending", "running"):
            return x
        time.sleep(0.3)
    raise TimeoutError(x)


EV_IGUAL = {"name": "igual", "type": "expr", "expr": "output.get('longitud') == expected.get('longitud')"}


def test_c1_experimentos(api):
    p = proyecto_longitud(api, "Longitud EXP")
    did = api.post("/api/datasets", json={"name": "DS exp"}).json()["dataset_id"]
    api.post(f"/api/datasets/{did}/examples", json=[
        {"input": {"texto": t}, "expected": {"longitud": len(t)}} for t in ("uno", "cuatro", "diecisiete")])
    # validación
    assert api.post("/api/experiments", json={"dataset_id": did, "project": p["slug"], "evaluators": [
        {"name": "mal", "type": "expr", "expr": "output.get('x' =="}]}).status_code == 400
    assert api.post("/api/experiments", json={"dataset_id": did, "project": p["slug"],
                                              "evaluators": []}).status_code == 400
    vacio = api.post("/api/datasets", json={"name": "DS vacío"}).json()["dataset_id"]
    assert api.post("/api/experiments", json={"dataset_id": vacio, "project": p["slug"],
                                              "evaluators": [EV_IGUAL]}).status_code == 400
    # experimento A: todo bien
    evs = [EV_IGUAL, {"name": "lanza", "type": "expr", "expr": "1 / 0"}]
    r = api.post("/api/experiments", json={"dataset_id": did, "project": p["slug"], "evaluators": evs,
                                           "concurrency": 2})
    assert r.status_code == 202, r.text
    a = esperar_exp(api, r.json()["experiment_id"])
    assert a["status"] == "success" and a["n"] == 3 and a["hechos"] == 3
    assert a["resumen"] == {"igual": 1.0, "lanza": None}
    assert all(x["status"] == "success" for x in a["results"])
    assert a["results"][0]["scores"]["lanza"]["score"] is None
    assert "ZeroDivisionError" in a["results"][0]["scores"]["lanza"]["comentario"]
    hilos = {x["thread_id"] for x in a["results"]}
    assert len(hilos) == 3
    visibles = {t["thread_id"] for t in api.get(f"/api/threads?project={p['slug']}").json()}
    assert not hilos & visibles
    ocultos = {t["thread_id"]: t for t in api.get(f"/api/threads?project={p['slug']}&ocultos=1").json()}
    assert hilos <= set(ocultos) and ocultos[a["results"][0]["thread_id"]]["metadata"]["experiment"] == \
        a["experiment_id"]
    runs = api.get(f"/api/runs?project={p['slug']}&limit=100").json()
    assert sum(1 for x in runs if x.get("experiment_id") == a["experiment_id"]) == 3
    # experimento B: el código falla en un caso
    p["nodes"]["cod"]["config"]["codigo"] = 'return {"longitud": 0 if len(state["texto"]) > 9 else len(state["texto"])}'
    assert api.put(f"/api/projects/{p['slug']}", json=p).status_code == 200
    b = esperar_exp(api, api.post("/api/experiments", json={"dataset_id": did, "project": p["slug"],
                                                             "evaluators": [EV_IGUAL]}).json()["experiment_id"])
    assert b["resumen"]["igual"] == pytest.approx(2 / 3, abs=1e-3)
    cmp = api.get(f"/api/experiments/compare?ids={a['experiment_id']},{b['experiment_id']}").json()
    assert len(cmp["filas"]) == 3
    dif = [f for f in cmp["filas"] if f["por_experimento"][a["experiment_id"]]["scores"]["igual"]["score"] !=
           f["por_experimento"][b["experiment_id"]]["scores"]["igual"]["score"]]
    assert len(dif) == 1 and dif[0]["input"] == {"texto": "diecisiete"}
    # rescore sin nuevas ejecuciones
    n_runs = len(api.get(f"/api/runs?project={p['slug']}&limit=500").json())
    r = api.post(f"/api/experiments/{b['experiment_id']}/rescore",
                 json={"evaluators": [{"name": "positiva", "type": "expr", "expr": "output['longitud'] > 0"}]})
    assert r.status_code == 202
    b2 = esperar_exp(api, b["experiment_id"])
    assert b2["resumen"] == {"positiva": pytest.approx(2 / 3, abs=1e-3)} and b2["status"] == "success"
    assert len(api.get(f"/api/runs?project={p['slug']}&limit=500").json()) == n_runs
    # otro dataset → compare 400
    otro = api.post("/api/datasets", json={"name": "DS otro"}).json()["dataset_id"]
    api.post(f"/api/datasets/{otro}/examples", json={"input": {"texto": "x"}, "expected": {"longitud": 1}})
    c = esperar_exp(api, api.post("/api/experiments", json={"dataset_id": otro, "project": p["slug"],
                                                             "evaluators": [EV_IGUAL]}).json()["experiment_id"])
    assert api.get(f"/api/experiments/compare?ids={a['experiment_id']},{c['experiment_id']}").status_code == 400
    assert {x["experiment_id"] for x in api.get(f"/api/experiments?dataset={did}").json()} == \
        {a["experiment_id"], b["experiment_id"]}
    assert api.delete(f"/api/experiments/{c['experiment_id']}").status_code == 204
    assert api.get(f"/api/experiments/{c['experiment_id']}").status_code == 404


def test_c1_experimento_detener_y_juez_sin_ollama(api):
    nodes = {"lento": {"module": "prueba_lenta.py", "x": 0, "y": 0, "config": {"segundos": 3}}}
    p = proyecto(api, "Lento EXP", nodes, cadena("lento"))
    did = api.post("/api/datasets", json={"name": "DS lento"}).json()["dataset_id"]
    api.post(f"/api/datasets/{did}/examples", json=[{"input": {"n": i}} for i in range(3)])
    eid = api.post("/api/experiments", json={"dataset_id": did, "project": p["slug"], "concurrency": 1,
                                             "evaluators": [{"name": "ok", "type": "expr", "expr": "True"}]}
                   ).json()["experiment_id"]
    time.sleep(1.0)
    assert api.delete(f"/api/experiments/{eid}").status_code == 409
    assert api.post(f"/api/experiments/{eid}/stop").status_code == 202
    x = esperar_exp(api, eid, 20)
    assert x["status"] == "stopped"
    sts = sorted(r["status"] for r in x["results"])
    assert sts == ["stopped", "stopped", "stopped"], sts
    time.sleep(1)
    runs = [r for r in api.get(f"/api/runs?project={p['slug']}").json() if r.get("experiment_id") == eid]
    assert len(runs) == 1 and runs[0]["status"] == "stopped"
    # LLM-juez con Ollama caído → score null, comentario "ollama"
    st = api.get("/api/settings").json()["values"]
    url = st["ollama_url"]
    assert api.put("/api/settings", json={**st, "ollama_url": "http://127.0.0.1:9"}).status_code == 200
    try:
        p2 = proyecto_longitud(api, "Longitud juez")
        d2 = api.post("/api/datasets", json={"name": "DS juez"}).json()["dataset_id"]
        api.post(f"/api/datasets/{d2}/examples", json={"input": {"texto": "hola"}})
        y = esperar_exp(api, api.post("/api/experiments", json={
            "dataset_id": d2, "project": p2["slug"],
            "evaluators": [{"name": "juez", "type": "llm", "prompt": "¿{output}?"}]}).json()["experiment_id"])
        assert y["status"] == "success"
        assert y["results"][0]["scores"]["juez"] == {"score": None, "comentario": "ollama"}
    finally:
        st2 = api.get("/api/settings").json()["values"]
        api.put("/api/settings", json={**st2, "ollama_url": url})


# ------------------------------------------------------------------------------------------------ C2: crons
def runs_cron(api, cid):
    return api.get(f"/api/crons/{cid}/runs").json()


def test_c2_crons(api):
    import httpx
    from conftest import PORT, TOKEN
    nodes = {"lento": {"module": "prueba_lenta.py", "x": 0, "y": 0, "config": {"segundos": 0.2}}}
    p = proyecto(api, "Cron rápido", nodes, cadena("lento"))
    assert api.post("/api/crons", json={"project": p["slug"], "cron": "cada rato"}).json()["code"] == "cron"
    assert api.post("/api/crons", json={"project": p["slug"], "cron": "* * * * *", "thread_mode": "fijo"}
                    ).status_code == 404
    pv = api.get("/api/crons/preview", params={"cron": "*/5 * * * *"}).json()
    assert pv["valido"] and pv["proximas"][1] - pv["proximas"][0] == 300
    assert api.get("/api/crons/preview", params={"cron": "no"}).json()["valido"] is False
    # eventos cron.fired por SSE
    fired = []
    import threading

    def escuchar():
        try:
            with httpx.stream("GET", f"http://127.0.0.1:{PORT}/api/events", headers={"X-Token": TOKEN},
                              timeout=httpx.Timeout(12, read=12)) as r:
                t0 = time.time()
                for ln in r.iter_lines():
                    if ln.startswith("event: cron.fired"):
                        fired.append(ln)
                    if time.time() - t0 > 9:
                        break
        except httpx.HTTPError:
            pass
    th = threading.Thread(target=escuchar)
    th.start()
    time.sleep(0.5)
    c = api.post("/api/crons", json={"project": p["slug"], "name": "rápido", "cron": "* * * * * */2"})
    assert c.status_code == 201, c.text
    cid = c.json()["cron_id"]
    assert c.json()["thread_mode"] == "nuevo" and c.json()["next_run"] > time.time() - 1
    time.sleep(7)
    rs = runs_cron(api, cid)
    assert len(rs) >= 2, rs
    assert len({r["thread_id"] for r in rs}) == len(rs)
    t = api.get(f"/api/threads/{rs[0]['thread_id']}").json()
    assert t["metadata"]["cron"] == cid
    assert api.patch(f"/api/crons/{cid}", json={"active": False}).json()["active"] is False
    time.sleep(0.6)
    n = len(runs_cron(api, cid))
    time.sleep(3)
    assert len(runs_cron(api, cid)) == n
    th.join()
    assert len(fired) >= 2
    # disparar ya sin mover next_run
    antes = api.get(f"/api/crons/{cid}").json()["next_run"]
    r = api.post(f"/api/crons/{cid}/run")
    assert r.status_code == 202 and r.json()["thread_id"]
    assert api.get(f"/api/crons/{cid}").json()["next_run"] == antes
    assert esperar_run(api, r.json()["run_id"])["cron_id"] == cid
    # hilo fijo con enqueue / reject
    p2 = proyecto(api, "Cron fijo", {"lento": {"module": "prueba_lenta.py", "x": 0, "y": 0,
                                                "config": {"segundos": 3}}}, cadena("lento"))
    tid = hilo(api, p2["slug"])
    cf = api.post("/api/crons", json={"project": p2["slug"], "cron": "* * * * * */1", "thread_mode": "fijo",
                                      "thread_id": tid, "multitask_strategy": "enqueue"}).json()
    time.sleep(2.6)
    api.patch(f"/api/crons/{cf['cron_id']}", json={"active": False})
    rs = runs_cron(api, cf["cron_id"])
    assert len(rs) >= 2 and all(r["thread_id"] == tid for r in rs)
    assert any(r["status"] == "queued" for r in rs), [r["status"] for r in rs]
    api.patch(f"/api/crons/{cf['cron_id']}", json={"multitask_strategy": "reject"})
    while any(r["status"] in ("queued", "pending", "running") for r in runs_cron(api, cf["cron_id"])):
        time.sleep(0.3)
    rs = sorted(runs_cron(api, cf["cron_id"]), key=lambda r: r["t0"])
    assert all(r["status"] == "success" for r in rs)
    assert all(a["t1"] <= b["t1"] for a, b in zip(rs, rs[1:]))
    r0 = lanzar(api, tid, input={})
    api.patch(f"/api/crons/{cf['cron_id']}", json={"active": True})
    time.sleep(1.6)
    api.patch(f"/api/crons/{cf['cron_id']}", json={"active": False})
    x = api.get(f"/api/crons/{cf['cron_id']}").json()
    assert "ocupado" in (x["last_error"] or ""), x
    esperar_run(api, r0["run_id"])
    # proyecto borrado → last_error, el planificador sigue
    p3 = proyecto(api, "Cron borrado", nodes, cadena("lento"))
    cb = api.post("/api/crons", json={"project": p3["slug"], "cron": "* * * * * */1"}).json()
    assert api.delete(f"/api/projects/{p3['slug']}").status_code == 204
    time.sleep(1.6)
    assert "No existe" in (api.get(f"/api/crons/{cb['cron_id']}").json()["last_error"] or "")
    assert api.delete(f"/api/crons/{cb['cron_id']}").status_code == 204
    assert {x["cron_id"] for x in api.get(f"/api/crons?project={p['slug']}").json()} == {cid}


def test_c2_cron_vencido_se_dispara_una_vez_tras_reiniciar(tmp_path):
    import sqlite3

    import httpx
    from conftest import PORT, TOKEN, arrancar, parar, preparar_home
    h = tmp_path / "home"
    preparar_home(h)
    proc = arrancar(h, port=PORT - 1)
    c = httpx.Client(base_url=f"http://127.0.0.1:{PORT - 1}", headers={"X-Token": TOKEN}, timeout=30)
    try:
        nodes = {"lento": {"module": "prueba_lenta.py", "x": 0, "y": 0, "config": {"segundos": 0.1}}}
        p = proyecto(c, "Cron anual", nodes, cadena("lento"))
        cid = c.post("/api/crons", json={"project": p["slug"], "cron": "0 0 1 1 *"}).json()["cron_id"]
        parar(proc)
        con = sqlite3.connect(h / "data/studio.sqlite")
        con.execute("UPDATE crons SET next_run=? WHERE cron_id=?", (time.time() - 3 * 3600, cid))
        con.commit()
        con.close()
        proc = arrancar(h, port=PORT - 1)
        time.sleep(2.5)
        rs = c.get(f"/api/crons/{cid}/runs").json()
        assert len(rs) == 1, rs
        x = c.get(f"/api/crons/{cid}").json()
        assert x["next_run"] > time.time() + 86400 and x["active"] and x["last_run"]
    finally:
        c.close()
        parar(proc)


# ------------------------------------------------------------------------------------------------ C3: playground
MOD_LLM_FALSO = '''"""LLM falso para el playground (no llama a Ollama)."""
import time
META = {"name": "LLM falso", "category": "Pruebas", "kind": "task", "exec": "local", "gpu": False, "llm": True,
        "inputs": [], "outputs": [], "outputs_from_config": ["campo_salida"]}
CONFIG = [
    {"key": "prompt", "label": "Prompt", "type": "textarea", "default": "Hola {nombre}"},
    {"key": "sistema", "label": "Sistema", "type": "textarea", "default": ""},
    {"key": "temperatura", "label": "T", "type": "number", "default": 0.7},
    {"key": "campo_salida", "label": "Salida", "type": "state_key", "default": "texto"},
    {"key": "fallar", "label": "Fallar", "type": "checkbox", "default": False},
]
def run(state, cfg, ctx):
    if cfg["fallar"]:
        raise ValueError("fallo pedido")
    texto = ctx.fill(cfg["prompt"], state)
    for w in texto.split():
        ctx.token(w + " ")
    ctx.log("temperatura", cfg["temperatura"], "tono", ctx.context.get("tono"))
    time.sleep(0.05)
    return {cfg["campo_salida"]: texto.upper() + f" T={cfg['temperatura']}"}
'''


def test_c3_playground(api):
    mod = crear_modulo(api, "LLM falso", MOD_LLM_FALSO)
    nodes = {"llm": {"module": mod, "x": 0, "y": 0, "config": {"prompt": "Hola {nombre}, {saludo}"}},
             "lento": {"module": "prueba_lenta.py", "x": 0, "y": 0, "config": {"segundos": 0.1}}}
    p = proyecto(api, "Playground", nodes, cadena("llm", "lento"),
                 state_schema={"nombre": {"input": True}, "saludo": {"input": True}},
                 context_schema=[{"key": "tono", "label": "Tono", "type": "text", "default": "neutro"}])
    n_threads = len(api.get(f"/api/threads?project={p['slug']}&ocultos=1").json())
    n_runs = len(api.get("/api/runs?limit=1000").json())
    url = f"/api/projects/{p['slug']}/nodes/llm/playground"
    r = api.post(url, json={"state": {"nombre": "Ana", "saludo": "qué tal"},
                            "variantes": [{"temperatura": 0}, {"prompt": "Adiós {nombre}", "temperatura": "0.3"}]})
    assert r.status_code == 200, r.text
    a, b = r.json()["resultados"]
    assert a["prompt"] == "Hola Ana, qué tal" and a["salida"] == {"texto": "HOLA ANA, QUÉ TAL T=0"}
    assert a["tokens"] == 4 and a["error"] is None and "tono neutro" in a["logs"][0]
    assert b["prompt"] == "Adiós Ana" and b["cfg"]["temperatura"] == 0.3 and b["tokens"] == 2
    assert b["cfg"]["prompt"] == "Adiós {nombre}"
    # error en una variante → 200 con error
    r = api.post(url, json={"state": {}, "variantes": [{"fallar": True}, {}]}).json()["resultados"]
    assert "fallo pedido" in r[0]["error"] and r[1]["error"] is None and r[1]["prompt"] == "Hola , "
    # sin state: valores del hilo
    tid = hilo(api, p["slug"])
    esperar_run(api, lanzar(api, tid, input={"nombre": "Luis", "saludo": "hey"})["run_id"])
    n_threads += 1
    n_runs += 1
    r = api.post(url, json={"thread_id": tid, "variantes": [{}]}).json()["resultados"][0]
    assert r["prompt"] == "Hola Luis, hey"
    # asistente: su override se aplica antes de la variante
    asis = api.post(f"/api/projects/{p['slug']}/assistants",
                    json={"name": "serio", "context": {"tono": "serio"},
                          "node_overrides": {"llm": {"prompt": "Buenas {nombre}", "temperatura": 0.1}}}).json()
    r = api.post(url, json={"state": {"nombre": "Eva"}, "assistant_id": asis["assistant_id"],
                            "variantes": [{"temperatura": 0.9}]}).json()["resultados"][0]
    assert r["prompt"] == "Buenas Eva" and r["cfg"]["temperatura"] == 0.9 and "tono serio" in r["logs"][0]
    # aislamiento: no crea hilos ni ejecuciones
    assert len(api.get(f"/api/threads?project={p['slug']}&ocultos=1").json()) == n_threads
    assert len(api.get("/api/runs?limit=1000").json()) == n_runs
    # validaciones
    assert api.post(f"/api/projects/{p['slug']}/nodes/lento/playground", json={}).json()["code"] == "unsupported"
    assert api.post(url, json={"variantes": [{"noexiste": 1}]}).status_code == 400
    assert api.post(url, json={"variantes": [{}] * 5}).status_code == 400
    assert api.post(f"/api/projects/{p['slug']}/nodes/nada/playground", json={}).status_code == 404


def test_c3_playground_agente_y_ollama_caido(api):
    nodes = {"ag": {"module": "agente_react.py", "x": 0, "y": 0},
             "llm": {"module": "llm_ollama.py", "x": 0, "y": 0,
                     "config": {"prompt": "Di {texto}", "url": "http://127.0.0.1:9"}}}
    edges = [{"id": "a", "src": "__start__", "dst": "llm"}, {"id": "b", "src": "llm", "dst": "__end__"}]
    p = proyecto(api, "Playground agente", nodes, edges, state_schema={"messages": {"reducer": "messages"}})
    r = api.post(f"/api/projects/{p['slug']}/nodes/ag/playground", json={})
    assert r.status_code == 400 and r.json()["code"] == "unsupported"
    r = api.post(f"/api/projects/{p['slug']}/nodes/llm/playground", json={"state": {"texto": "x"}})
    assert r.status_code == 502 and r.json()["code"] == "ollama", r.text


# ------------------------------------------------------------------------------------------------ C6: MCP
SERVIDOR_MCP = '''
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("prueba")

@mcp.tool()
def sumar(a: int, b: int) -> int:
    """Suma dos enteros."""
    return a + b

@mcp.tool()
def eco(texto: str) -> str:
    """Devuelve el texto tal cual."""
    return texto

if __name__ == "__main__":
    mcp.run()
'''


def configurar_mcp(api, tmp_path, extra=None):
    from conftest import PY_ESTUDIO
    srv = tmp_path / "srv_mcp.py"
    srv.write_text(SERVIDOR_MCP)
    servidores = {"prueba": {"transport": "stdio", "command": PY_ESTUDIO, "args": [str(srv)]}, **(extra or {})}
    st = api.get("/api/settings").json()["values"]
    r = api.put("/api/settings", json={**st, "mcp_servidores": servidores})
    assert r.status_code == 200, r.text
    return servidores


def quitar_mcp(api):
    st = api.get("/api/settings").json()["values"]
    api.put("/api/settings", json={**st, "mcp_servidores": {}})


def test_c6_mcp(api, tmp_path):
    from test_f2_api import eventos
    st = api.get("/api/settings").json()["values"]
    assert api.put("/api/settings", json={**st, "mcp_servidores": {"a__b": {"transport": "stdio", "command": "x"}}}
                   ).status_code == 400
    assert api.put("/api/settings", json={**st, "mcp_servidores": {"x": {"transport": "ftp"}}}).status_code == 400
    configurar_mcp(api, tmp_path, {"roto": {"transport": "stdio", "command": "/no/existe", "args": []}})
    try:
        t0 = time.time()
        r = api.get("/api/mcp/servers")
        assert r.status_code == 200 and time.time() - t0 < 15
        srv = {s["nombre"]: s for s in r.json()}
        assert srv["prueba"]["ok"] and sorted(t["name"] for t in srv["prueba"]["tools"]) == ["eco", "sumar"]
        assert "a" in next(t for t in srv["prueba"]["tools"] if t["name"] == "sumar")["args_schema"]["properties"]
        assert srv["roto"]["ok"] is False and srv["roto"]["error"]
        assert api.post("/api/mcp/call", json={"servidor": "prueba", "herramienta": "sumar",
                                               "args": {"a": 2, "b": 3}}).json() == {"resultado": 5}
        assert api.post("/api/mcp/call", json={"servidor": "prueba", "herramienta": "nada"}).status_code == 404
        assert api.post("/api/mcp/call", json={"servidor": "nada", "herramienta": "x"}).status_code == 404
        r = api.post("/api/mcp/call", json={"servidor": "roto", "herramienta": "x"})
        assert r.status_code == 502 and r.json()["code"] == "mcp"
        # nodo mcp_herramientas
        nodes = {"m": {"module": "mcp_herramientas.py", "x": 0, "y": 0,
                       "config": {"servidor": "prueba", "herramienta": "eco", "argumentos": {"texto": "{texto}"},
                                  "campo_salida": "eco"}}}
        p = proyecto(api, "MCP eco", nodes, cadena("m"), state_schema={"texto": {"input": True}})
        tid = hilo(api, p["slug"])
        assert esperar_run(api, lanzar(api, tid, input={"texto": "hola mcp"})["run_id"])["status"] == "success"
        assert estado(api, tid)["eco"] == "hola mcp"
        # servidor caído → error del nodo, reintentado
        p["nodes"]["m"]["config"]["servidor"] = "roto"
        p["nodes"]["m"]["retry"] = {"max_attempts": 2, "initial_interval": 0.1, "jitter": False}
        assert api.put(f"/api/projects/{p['slug']}", json=p).status_code == 200
        tid = hilo(api, p["slug"])
        run = esperar_run(api, lanzar(api, tid, input={"texto": "x"})["run_id"])
        assert run["status"] == "error" and "roto" in (run["error"] or ""), run
        inicios = [e for e in eventos(api, tid, run["run_id"], "node.start") if not e["data"].get("esperando_gpu")]
        assert len({e["data"]["attempt"] for e in inicios}) == 2
    finally:
        quitar_mcp(api)
