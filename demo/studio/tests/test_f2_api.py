"""Fase 2: criterios A1–A9 por HTTP contra un servidor real en el puerto 8799 (DISENO.md §6.3, §13.3)."""
import time

import pytest
from conftest import esperar_run

MOD_CTX = '''"""Devuelve el tono del contexto (módulo local de prueba)."""
META = {"name": "Ver contexto", "category": "Pruebas", "kind": "task", "exec": "local", "gpu": False,
        "inputs": [], "outputs": ["tono_local"]}
CONFIG = []
def run(state, cfg, ctx):
    return {"tono_local": ctx.context.get("tono")}
'''


def crear_modulo(api, nombre, source):
    f = api.post("/api/modules", json={"name": nombre}).json()["file"]
    sha = api.get(f"/api/modules/{f}/source").json()["sha"]
    r = api.put(f"/api/modules/{f}/source", json={"source": source, "sha": sha})
    assert r.status_code == 200, r.text
    return f


def proyecto(api, nombre, nodes, edges, **extra):
    p = api.post("/api/projects", json={"name": nombre, "template": "vacio"}).json()
    p["nodes"] = nodes
    p["edges"] = edges
    p.update(extra)
    r = api.put(f"/api/projects/{p['slug']}", json=p)
    assert r.status_code == 200, r.text
    return r.json()


def cadena(*ids):
    seq = ["__start__", *ids, "__end__"]
    return [{"id": f"e{i}", "src": a, "dst": b} for i, (a, b) in enumerate(zip(seq, seq[1:]))]


def lanzar(api, tid, **body):
    r = api.post(f"/api/threads/{tid}/runs", json=body)
    assert r.status_code == 202, r.text
    return r.json()


def hilo(api, slug):
    return api.post("/api/threads", json={"project": slug}).json()["thread_id"]


def estado(api, tid):
    return api.get(f"/api/threads/{tid}/state").json()["values"]


# ------------------------------------------------------------------------------------------------ A2
def test_a2_asistentes_versionados(api):
    mod = crear_modulo(api, "Ver contexto", MOD_CTX)
    nodes = {
        "lento": {"module": "prueba_lenta.py", "x": 0, "y": 0, "config": {"segundos": 0.1}},
        "ver": {"module": mod, "x": 0, "y": 0},
        "py": {"module": "codigo_python.py", "x": 0, "y": 0,
               "config": {"codigo": 'return {"tono_proc": ctx.context.get("tono")}', "salidas": "tono_proc",
                          "entradas": ""}},
    }
    ctx_schema = [{"key": "tono", "label": "Tono", "type": "text", "default": "neutro"},
                  {"key": "n", "label": "N", "type": "number", "default": 1}]
    p = proyecto(api, "Asistentes", nodes, cadena("lento", "ver", "py"), context_schema=ctx_schema)
    slug = p["slug"]
    url = f"/api/projects/{slug}/assistants"
    # validación
    assert api.post(url, json={"name": "x", "node_overrides": {"nope": {}}}).status_code == 400
    r = api.post(url, json={"name": "x", "node_overrides": {"lento": {"campo_raro": 1}}})
    assert r.status_code == 400 and "campo_raro" in r.json()["detail"]
    assert api.post(url, json={"name": "x", "context": {"otra": 1}}).status_code == 400
    a = api.post(url, json={"name": "Serio", "context": {"tono": "serio"},
                            "node_overrides": {"lento": {"clave_salida": "x"}}})
    assert a.status_code == 201, a.text
    a = a.json()
    aid = a["assistant_id"]
    assert a["active_version"] == 1 and a["n_versions"] == 1 and a["context"] == {"tono": "serio"}
    assert [x["assistant_id"] for x in api.get(url).json()] == [aid]

    tid = hilo(api, slug)
    run = lanzar(api, tid, input={}, assistant_id=aid)
    r = esperar_run(api, run["run_id"])
    assert r["status"] == "success", r
    assert r["assistant_id"] == aid and r["assistant_version"] == 1
    v = estado(api, tid)
    assert v["x"] == "lento ok" and "prueba" not in v
    assert v["tono_local"] == "serio" and v["tono_proc"] == "serio"

    # versión 2
    a2 = api.patch(f"/api/assistants/{aid}", json={"node_overrides": {"lento": {"clave_salida": "y"}}}).json()
    assert a2["active_version"] == 2 and a2["context"] == {"tono": "serio"}
    vs = api.get(f"/api/assistants/{aid}/versions").json()
    assert [x["version"] for x in vs] == [2, 1] and vs[1]["node_overrides"] == {"lento": {"clave_salida": "x"}}
    assert api.patch(f"/api/assistants/{aid}", json={"name": "Muy serio"}).json()["active_version"] == 2
    tid2 = hilo(api, slug)
    esperar_run(api, lanzar(api, tid2, input={}, assistant_id=aid)["run_id"])
    assert estado(api, tid2)["y"] == "lento ok"
    # activar la 1
    assert api.post(f"/api/assistants/{aid}/activate/1").json()["active_version"] == 1
    assert api.post(f"/api/assistants/{aid}/activate/9").status_code == 404
    tid3 = hilo(api, slug)
    esperar_run(api, lanzar(api, tid3, input={}, assistant_id=aid)["run_id"])
    assert estado(api, tid3)["x"] == "lento ok"
    # versión concreta y sin asistente (contexto por defecto del esquema)
    tid4 = hilo(api, slug)
    esperar_run(api, lanzar(api, tid4, input={}, assistant_id=aid, assistant_version=2)["run_id"])
    assert estado(api, tid4)["y"] == "lento ok"
    tid5 = hilo(api, slug)
    esperar_run(api, lanzar(api, tid5, input={})["run_id"])
    assert estado(api, tid5)["prueba"] == "lento ok" and estado(api, tid5)["tono_local"] == "neutro"
    assert api.post(f"/api/threads/{tid5}/runs", json={"input": {}, "assistant_id": "no-existe"}).status_code == 404
    assert api.delete(f"/api/assistants/{aid}").status_code == 204
    assert api.get(f"/api/assistants/{aid}").status_code == 404


def lentos(*spec):
    """spec: (id, segundos[, extra cfg]) → nodos prueba_lenta con clave_salida = id."""
    out = {}
    for it in spec:
        nid, seg = it[0], it[1]
        cfg = {"segundos": seg, "clave_salida": f"k_{nid}", **(it[2] if len(it) > 2 else {})}
        out[nid] = {"module": "prueba_lenta.py", "x": 0, "y": 0, "config": cfg}
    return out


def eventos(api, tid, run_id, types="node.start"):
    return api.get(f"/api/threads/{tid}/events", params={"run": run_id, "types": types, "limit": 1000}).json()


def esperar_status(api, run_id, status, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = api.get(f"/api/runs/{run_id}").json()
        if r["status"] == status:
            return r
        time.sleep(0.1)
    raise TimeoutError(r)


# ------------------------------------------------------------------------------------------------ A1
def test_a1_reejecutar_nodo(api):
    nodes = lentos(("a", 0.1), ("b", 0.1), ("c", 0.1), ("d", 0.1))
    p = proyecto(api, "Rerun", nodes, cadena("a", "b", "c"))
    tid = hilo(api, p["slug"])
    assert api.post(f"/api/threads/{tid}/nodes/b/rerun", json={}).status_code == 409
    r1 = lanzar(api, tid, input={})
    assert esperar_run(api, r1["run_id"])["status"] == "success"
    hist0 = api.get(f"/api/threads/{tid}/history").json()
    r = api.post(f"/api/threads/{tid}/nodes/b/rerun", json={})
    assert r.status_code == 202, r.text
    rr = r.json()
    run = esperar_run(api, rr["run_id"])
    assert run["status"] == "success" and run["kind"] == "replay"
    nodos = [e["node"] for e in eventos(api, tid, rr["run_id"]) if "esperando_gpu" not in e["data"]]
    assert sorted(nodos) == ["b", "c"]
    # la cabeza cuelga del checkpoint elegido y la rama original sigue en la historia
    hist = api.get(f"/api/threads/{tid}/history").json()
    ids = {h["checkpoint_id"]: h for h in hist}
    cab = hist[0]
    cur, cadena_ids = cab["checkpoint_id"], []
    while cur:
        cadena_ids.append(cur)
        cur = ids[cur]["parent_checkpoint_id"] if cur in ids else None
    assert rr["checkpoint_id"] in cadena_ids
    assert ids[rr["checkpoint_id"]]["next"] == ["b"]
    assert {h["checkpoint_id"] for h in hist0} <= set(ids)
    r = api.post(f"/api/threads/{tid}/nodes/d/rerun", json={})
    assert r.status_code == 409 and r.json()["code"] == "not_rerunnable"
    assert api.post(f"/api/threads/{tid}/nodes/zz/rerun", json={}).status_code == 404


# ------------------------------------------------------------------------------------------------ A8
def test_a8_enqueue(api, server):
    p = proyecto(api, "Cola", lentos(("a", 2)), cadena("a"),
                 state_schema={"n": {"reducer": "append", "input": True}})
    tid = hilo(api, p["slug"])
    r0 = lanzar(api, tid, input={"n": [0]})
    esperar_status(api, r0["run_id"], "running")
    assert api.post(f"/api/threads/{tid}/runs", json={"input": {"n": [9]}}).status_code == 409
    q1 = lanzar(api, tid, input={"n": [1]}, multitask_strategy="enqueue")
    q2 = lanzar(api, tid, input={"n": [2]}, multitask_strategy="enqueue")
    q3 = lanzar(api, tid, input={"n": [3]}, multitask_strategy="enqueue")
    assert (q1["queued"], q1["position"], q2["position"], q3["position"]) == (True, 1, 2, 3)
    assert api.get(f"/api/runs/{q2['run_id']}").json()["status"] == "queued"
    assert api.post(f"/api/runs/{q3['run_id']}/stop").status_code == 202
    assert api.get(f"/api/runs/{q3['run_id']}").json()["status"] == "stopped"
    for r in (r0, q1, q2):
        assert esperar_run(api, r["run_id"], timeout=30)["status"] == "success"
    t = {r: api.get(f"/api/runs/{r}").json() for r in (r0["run_id"], q1["run_id"], q2["run_id"])}
    assert t[r0["run_id"]]["t1"] <= t[q1["run_id"]]["t0"] <= t[q1["run_id"]]["t1"] <= t[q2["run_id"]]["t0"]
    assert estado(api, tid)["n"] == [0, 1, 2]


def test_a8_interrupt(api):
    p = proyecto(api, "Interrumpir", lentos(("a", 0.1), ("b", 6)), cadena("a", "b"))
    tid = hilo(api, p["slug"])
    r0 = lanzar(api, tid, input={})
    t_ini = time.time()
    while time.time() - t_ini < 15:
        if any(e["node"] == "b" for e in eventos(api, tid, r0["run_id"])):
            break
        time.sleep(0.1)
    t0 = time.time()
    r1 = lanzar(api, tid, input=None, multitask_strategy="interrupt")
    assert r1["replaces"] == r0["run_id"]
    old = esperar_run(api, r0["run_id"], timeout=5)
    assert time.time() - t0 < 3
    assert old["status"] == "stopped" and old["motivo"] == "interrupt"
    new = esperar_run(api, r1["run_id"], timeout=30)
    assert new["status"] == "success"
    # «a» terminó en la ejecución interrumpida: no se repite
    assert [e["node"] for e in eventos(api, tid, r1["run_id"]) if "esperando_gpu" not in e["data"]] == ["b"]


def test_a8_rollback(api):
    p = proyecto(api, "Rollback", lentos(("a", 0.1), ("b", 6)), cadena("a", "b"),
                 state_schema={"marca": {"reducer": "replace", "input": True}})
    tid = hilo(api, p["slug"])
    # 1) rollback sobre la primera ejecución de un hilo vacío → sus checkpoints van a un hilo oculto
    r0 = lanzar(api, tid, input={"marca": "vieja"})
    while not any(e["node"] == "b" for e in eventos(api, tid, r0["run_id"])):
        time.sleep(0.1)
    r1 = lanzar(api, tid, input={"marca": "nueva"}, multitask_strategy="rollback")
    old = esperar_run(api, r0["run_id"], timeout=5)
    assert old["status"] == "stopped" and old["motivo"] == "rollback"
    esperar_run(api, r1["run_id"], timeout=30)
    v = estado(api, tid)
    assert v["marca"] == "nueva"
    oculto = f"rb-{r0['run_id']}"
    assert oculto not in [t["thread_id"] for t in api.get(f"/api/threads?project={p['slug']}").json()]
    assert oculto in [t["thread_id"] for t in api.get(f"/api/threads?project={p['slug']}&ocultos=1").json()]
    assert api.get(f"/api/threads/{oculto}/state").json()["values"]["marca"] == "vieja"
    # 2) rollback sobre una ejecución que empezó en un checkpoint → se oculta en la historia, nada se borra
    n_antes = len(api.get(f"/api/threads/{tid}/history", params={"rolled_back": 1}).json())
    r2 = lanzar(api, tid, input={"marca": "abandonada"})
    while not any(e["node"] == "b" for e in eventos(api, tid, r2["run_id"])):
        time.sleep(0.1)
    r3 = lanzar(api, tid, input={"marca": "final"}, multitask_strategy="rollback")
    assert esperar_run(api, r2["run_id"], timeout=5)["motivo"] == "rollback"
    assert esperar_run(api, r3["run_id"], timeout=30)["status"] == "success"
    assert estado(api, tid)["marca"] == "final"
    todos = api.get(f"/api/threads/{tid}/history", params={"rolled_back": 1}).json()
    visibles = api.get(f"/api/threads/{tid}/history").json()
    ocultos = [h for h in todos if h["rolled_back"]]
    assert ocultos and len(visibles) == len(todos) - len(ocultos)
    assert len(todos) > n_antes
    # el estado de la nueva rama no contiene nada de la abandonada: la cadena de la cabeza no pasa por ocultos
    ids = {h["checkpoint_id"]: h for h in todos}
    cur = visibles[0]["checkpoint_id"]
    while cur:
        assert not ids[cur]["rolled_back"]
        cur = ids[cur]["parent_checkpoint_id"]


def test_a8_cola_sobrevive_reinicio(tmp_path):
    import httpx
    from conftest import PORT, TOKEN, arrancar, parar, preparar_home
    h = tmp_path / "home"
    preparar_home(h)
    proc = arrancar(h, port=PORT - 1)
    c = httpx.Client(base_url=f"http://127.0.0.1:{PORT - 1}", headers={"X-Token": TOKEN}, timeout=30)
    try:
        p = proyecto(c, "Cola reinicio", lentos(("a", 3)), cadena("a"))
        tid = hilo(c, p["slug"])
        r0 = lanzar(c, tid, input={})
        esperar_status(c, r0["run_id"], "running")
        q = lanzar(c, tid, input={}, multitask_strategy="enqueue")
        parar(proc)
        proc = arrancar(h, port=PORT - 1)
        assert c.get(f"/api/runs/{r0['run_id']}").json()["status"] == "stopped"
        assert esperar_run(c, q["run_id"], timeout=30)["status"] == "success"
    finally:
        c.close()
        parar(proc)


def nodos_run(api, tid, run_id):
    return [e for e in eventos(api, tid, run_id, "node.start,node.end")]


def fin_de(evs, node):
    return [e for e in evs if e["node"] == node and e["type"] == "node.end"]


# ------------------------------------------------------------------------------------------------ A4
def test_a4_cache(api, server):
    import pathlib
    escribir = ("import os\np = os.path.join(str(ctx.run_dir), 'f.txt')\nopen(p, 'w').write('x')\n"
                "return {'fichero': p}")
    nodes = {**lentos(("lento", 3)),
             "fich": {"module": "codigo_python.py", "x": 0, "y": 0, "cache": {"ttl": None},
                      "config": {"codigo": escribir, "entradas": "x", "salidas": "fichero"}}}
    nodes["lento"]["cache"] = {"ttl": 60}
    p = proyecto(api, "Cache", nodes, cadena("lento", "fich"), state_schema={"x": {"reducer": "replace", "input": True}})
    slug = p["slug"]

    def correr(inp=None, **kw):
        tid = hilo(api, slug)
        t0 = time.time()
        r = lanzar(api, tid, input=inp or {"x": 1}, **kw)
        run = esperar_run(api, r["run_id"], timeout=30)
        assert run["status"] == "success", run
        evs = nodos_run(api, tid, r["run_id"])
        return tid, r["run_id"], fin_de(evs, "lento")[-1]["data"], time.time() - t0, fin_de(evs, "fich")[-1]["data"]

    _, _, d1, _, _ = correr()
    assert d1["status"] == "success"
    tid2, run2, d2, dur2, f2 = correr()
    assert d2["status"] == "cached" and f2["status"] == "cached"
    assert estado(api, tid2)["k_lento"] == "lento ok"
    view = api.get(f"/api/threads/{tid2}/view").json()
    assert view["nodos"]["lento"]["cached"] is True
    assert dur2 < 3
    assert api.get(f"/api/projects/{slug}/cache").json()["entradas"] >= 2
    # fallos de caché: entrada distinta, cfg (asistente), código, fichero borrado
    r = correr({"x": 2})
    assert r[2]["status"] == "cached" and r[4]["status"] == "success"  # «lento» no lee x; «fich» sí
    a = api.post(f"/api/projects/{slug}/assistants", json={"name": "otra clave",
                                                            "node_overrides": {"lento": {"clave_salida": "k_otra"}}}).json()
    assert correr(assistant_id=a["assistant_id"])[2]["status"] == "success"
    pathlib.Path(f2["output"]["fichero"]).unlink()
    assert correr()[4]["status"] == "success"
    assert correr()[4]["status"] == "cached"
    src = api.get("/api/modules/prueba_lenta.py/source").json()
    api.put("/api/modules/prueba_lenta.py/source", json={"source": src["source"] + "\n# cambio\n", "sha": src["sha"]})
    assert correr()[2]["status"] == "success"
    api.delete(f"/api/projects/{slug}/cache", params={"node": "lento"})
    assert correr()[2]["status"] == "success"
    api.delete(f"/api/projects/{slug}/cache")
    assert api.get(f"/api/projects/{slug}/cache").json()["entradas"] == 0


def test_a4_cache_ttl(api):
    nodes = lentos(("lento", 0.2))
    nodes["lento"]["cache"] = {"ttl": 2}
    p = proyecto(api, "Cache ttl", nodes, cadena("lento"))
    res = []
    for espera in (0, 0, 2.5):
        time.sleep(espera)
        tid = hilo(api, p["slug"])
        r = lanzar(api, tid, input={})
        esperar_run(api, r["run_id"])
        res.append(fin_de(nodos_run(api, tid, r["run_id"]), "lento")[-1]["data"]["status"])
    assert res == ["success", "cached", "success"]


# ------------------------------------------------------------------------------------------------ A5
def test_a5_error_to(api):
    nodes = {**lentos(("falla", 0.1, {"fallar": True})),
             "recuperar": {"module": "codigo_python.py", "x": 0, "y": 0,
                           "config": {"codigo": "return {'visto': state.get('error')}", "entradas": "error",
                                      "salidas": "visto"}}}
    nodes["falla"]["retry"] = {"max_attempts": 2, "initial_interval": 0.05, "jitter": False}
    nodes["falla"]["error_to"] = "recuperar"
    p = proyecto(api, "Error to", nodes, cadena("falla") + [{"id": "r1", "src": "recuperar", "dst": "__end__"}])
    tid = hilo(api, p["slug"])
    r = lanzar(api, tid, input={})
    run = esperar_run(api, r["run_id"], timeout=30)
    assert run["status"] == "success", run
    v = estado(api, tid)
    assert v["error"]["node"] == "falla" and "Fallo pedido" in v["error"]["msg"]
    assert v["visto"]["node"] == "falla"
    evs = nodos_run(api, tid, r["run_id"])
    assert len([e for e in evs if e["node"] == "falla" and e["type"] == "node.start"
                and "esperando_gpu" not in e["data"]]) == 2
    view = api.get(f"/api/threads/{tid}/view").json()["nodos"]
    assert view["falla"]["status"] == "error" and view["recuperar"]["status"] == "success"
    assert not any(k.startswith("__") for k in view)
    # validación
    p["nodes"]["falla"]["error_to"] = "falla"
    p = api.put(f"/api/projects/{p['slug']}", json=p).json()
    assert any("error_to" in e["msg"] for e in api.get(f"/api/projects/{p['slug']}/validate").json()["errores"])
    p["nodes"]["falla"]["error_to"] = "nadie"
    p = api.put(f"/api/projects/{p['slug']}", json=p).json()
    assert any("nadie" in e["msg"] for e in api.get(f"/api/projects/{p['slug']}/validate").json()["errores"])


def test_a5_defer(api):
    nodes = lentos(("a", 0.2), ("b", 2), ("c", 0.1))
    nodes["c"]["defer"] = True
    edges = [{"id": "e1", "src": "__start__", "dst": "a"}, {"id": "e2", "src": "__start__", "dst": "b"},
             {"id": "e3", "src": "a", "dst": "c"}, {"id": "e4", "src": "b", "dst": "c"},
             {"id": "e5", "src": "c", "dst": "__end__"}]
    p = proyecto(api, "Defer", nodes, edges)
    tid = hilo(api, p["slug"])
    r = lanzar(api, tid, input={})
    assert esperar_run(api, r["run_id"], timeout=30)["status"] == "success"
    evs = nodos_run(api, tid, r["run_id"])
    starts_c = [e for e in evs if e["node"] == "c" and e["type"] == "node.start" and "esperando_gpu" not in e["data"]]
    assert len(starts_c) == 1
    assert starts_c[0]["ts"] >= fin_de(evs, "b")[-1]["ts"]


# ------------------------------------------------------------------------------------------------ A6
MOD_ROUTER = '''META = {"name": "Router fijo", "kind": "router", "exec": "local", "inputs": ["elige"], "outputs": []}
CONFIG = []
def run(state, cfg, ctx):
    return state.get("elige")
'''
MOD_CMD = '''from langgraph.types import Command
META = {"name": "Cmd", "kind": "task", "exec": "local", "command": True, "inputs": ["destino"], "outputs": ["via"]}
CONFIG = []
def run(state, cfg, ctx):
    return Command(goto=state.get("destino"), update={"via": "cmd"})
'''


def py_node(codigo, entradas="", salidas=""):
    return {"module": "codigo_python.py", "x": 0, "y": 0,
            "config": {"codigo": codigo, "entradas": entradas, "salidas": salidas}}


def test_a6_send(api):
    nodes = {"entrada": {"module": "entrada.py", "x": 0, "y": 0, "config": {"valores": {"items": [0.1, 0.1, 4]}}},
             "mapa": {"module": "mapa_send.py", "x": 0, "y": 0, "config": {"lista": "items", "destino": "doble"}},
             "doble": py_node("import time\ntime.sleep(state['item'])\nreturn {'resultados': [state['item'] * 2]}",
                              "item", "resultados")}
    edges = cadena("entrada", "mapa") [:2] + [{"id": "m1", "src": "mapa", "dst": "doble"},
                                              {"id": "m2", "src": "doble", "dst": "__end__"}]
    p = proyecto(api, "Send", nodes, edges, state_schema={"resultados": {"reducer": "append", "output": True}})
    tid = hilo(api, p["slug"])
    r = lanzar(api, tid, input={})
    t0 = time.time()
    while time.time() - t0 < 20:
        ends = [e for e in nodos_run(api, tid, r["run_id"]) if e["node"] == "doble" and e["type"] == "node.end"]
        if len(ends) >= 2:
            break
        time.sleep(0.1)
    assert api.post(f"/api/runs/{r['run_id']}/stop").status_code == 202
    assert esperar_run(api, r["run_id"], timeout=10)["status"] == "stopped"
    r2 = lanzar(api, tid, input=None)
    assert esperar_run(api, r2["run_id"], timeout=30)["status"] == "success"
    starts = [e for e in nodos_run(api, tid, r2["run_id"]) if e["node"] == "doble" and e["type"] == "node.start"
              and "esperando_gpu" not in e["data"]]
    assert len(starts) == 1
    assert sorted(estado(api, tid)["resultados"]) == [0.2, 0.2, 8]
    # carpetas de tarea distintas
    run_dirs = list((api_home() / "projects" / p["slug"] / "threads" / tid / "doble").iterdir())
    assert len(run_dirs) >= 3


def api_home():
    import pathlib
    import glob
    return pathlib.Path(sorted(glob.glob("/tmp/pytest-of-*/pytest-current/srv*/home"))[-1])


def test_a6_command_y_router(api):
    cmd = crear_modulo(api, "Cmd", MOD_CMD)
    rt = crear_modulo(api, "Router fijo", MOD_ROUTER)
    nodes = {"cmd": {"module": cmd, "x": 0, "y": 0},
             "x": py_node("return {'llego': 'x'}", "", "llego"), "y": py_node("return {'llego': 'y'}", "", "llego")}
    edges = [{"id": "e0", "src": "__start__", "dst": "cmd"}, {"id": "e1", "src": "cmd", "dst": "x"},
             {"id": "e2", "src": "cmd", "dst": "y"}, {"id": "e3", "src": "x", "dst": "__end__"},
             {"id": "e4", "src": "y", "dst": "__end__"}]
    p = proyecto(api, "Command", nodes, edges, state_schema={"destino": {"input": True}})
    tid = hilo(api, p["slug"])
    esperar_run(api, lanzar(api, tid, input={"destino": "y"})["run_id"])
    v = estado(api, tid)
    assert v["llego"] == "y" and v["via"] == "cmd"
    tid = hilo(api, p["slug"])
    run = esperar_run(api, lanzar(api, tid, input={"destino": "z"})["run_id"])
    assert run["status"] == "error" and "válidos: x, y" in run["error"]
    # router
    nodes["cmd"]["module"] = rt
    p2 = proyecto(api, "Router", nodes, edges, state_schema={"elige": {"input": True}})
    for elige, esperado in (("x", "x"), (["x", "y"], None)):
        tid = hilo(api, p2["slug"])
        run = esperar_run(api, lanzar(api, tid, input={"elige": elige})["run_id"])
        if esperado:
            assert run["status"] == "success" and estado(api, tid)["llego"] == "x"
        else:
            assert run["status"] == "error"  # x e y escriben 'llego' (replace) en paralelo: InvalidUpdateError
    tid = hilo(api, p2["slug"])
    run = esperar_run(api, lanzar(api, tid, input={"elige": "nadie"})["run_id"])
    assert run["status"] == "error" and "válidos: x, y" in run["error"]
    # condición en una arista de un enrutador → error de validación
    p2["edges"][1]["cond"] = "True"
    p2 = api.put(f"/api/projects/{p2['slug']}", json=p2).json()
    assert any("Command/Send" in e["msg"] for e in api.get(f"/api/projects/{p2['slug']}/validate").json()["errores"])


def test_a6_subgrafo(api):
    hijo = proyecto(api, "Hijo", {"rev": {"module": "revision_humana.py", "x": 0, "y": 0}}, cadena("rev"),
                    state_schema={"texto": {"reducer": "replace", "output": True}})
    nodes = {"entrada": {"module": "entrada.py", "x": 0, "y": 0, "config": {"valores": {"texto": "hola"}}},
             "sub": {"module": "subgrafo.py", "x": 0, "y": 0, "config": {"proyecto": hijo["slug"]}},
             "fin": py_node("return {'largo': len(state['texto'])}", "texto", "largo")}
    padre = proyecto(api, "Padre", nodes, cadena("entrada", "sub", "fin"))
    v = api.get(f"/api/projects/{padre['slug']}/validate").json()
    assert v["errores"] == [] and v["subgrafos"]["sub"]["nodos"] == ["rev"]
    tid = hilo(api, padre["slug"])
    run = esperar_run(api, lanzar(api, tid, input={})["run_id"])
    assert run["status"] == "interrupted", run
    st = api.get(f"/api/threads/{tid}/state").json()
    assert st["next"] == ["sub"] and len(st["interrupts"]) == 1
    iid = st["interrupts"][0]["id"]
    evs = api.get(f"/api/threads/{tid}/events", params={"limit": 1000}).json()
    assert any(e["ns"] for e in evs)
    r2 = lanzar(api, tid, resume={iid: {"aprobado": True, "valor": "editado"}})
    assert esperar_run(api, r2["run_id"])["status"] == "success"
    v = estado(api, tid)
    assert v["texto"] == "editado" and v["largo"] == 7 and "aprobado" not in v
    view = api.get(f"/api/threads/{tid}/view").json()["nodos"]
    assert view["sub"]["status"] == "success"
    mm = api.get(f"/api/projects/{padre['slug']}/mermaid", params={"xray": 1}).json()["mermaid"]
    assert "rev" in mm and "subgraph sub" in mm
    # ciclo
    hijo["nodes"]["vuelta"] = {"module": "subgrafo.py", "x": 0, "y": 0, "config": {"proyecto": padre["slug"]}}
    hijo = api.put(f"/api/projects/{hijo['slug']}", json=hijo).json()
    errs = api.get(f"/api/projects/{padre['slug']}/validate").json()["errores"]
    assert any("Ciclo" in e["msg"] for e in errs)
    del hijo["nodes"]["vuelta"]
    # append compartido
    hijo["state_schema"]["log"] = {"reducer": "append"}
    hijo = api.put(f"/api/projects/{hijo['slug']}", json=hijo).json()
    padre["state_schema"] = {"log": {"reducer": "append"}}
    padre = api.put(f"/api/projects/{padre['slug']}", json=padre).json()
    errs = api.get(f"/api/projects/{padre['slug']}/validate").json()["errores"]
    assert any("'log'" in e["msg"] for e in errs)


# ------------------------------------------------------------------------------------------------ A3
MOD_STORE_PROC = '''"""Lee y escribe el Store desde un subproceso."""
META = {"name": "Store proceso", "kind": "task", "exec": "proceso", "interprete": "estudio",
        "inputs": ["usuario"], "outputs": ["leidos"], "store_reads": [["notas", "{usuario}"]]}
CONFIG = []
def run(state, cfg, ctx):
    leidos = sorted(it["value"]["t"] for it in ctx.store.search(["notas", state["usuario"]]))
    ctx.store.put(["notas", state["usuario"]], "desde_proceso", {"t": "escrito"}, index=False)
    ctx.store.delete(["notas", state["usuario"]], "borrame")
    return {"leidos": leidos}
'''


def test_a3_store_crud(api):
    ns = ["pruebas", "ana"]
    for k, t in (("a", "uno"), ("b", "dos")):
        r = api.put("/api/store/items", json={"namespace": ns, "key": k, "value": {"t": t}, "index": False})
        assert r.status_code == 200 and r.json()["value"] == {"t": t}
    assert api.get("/api/store/items", params={"ns": "pruebas/ana", "key": "a"}).json()["value"] == {"t": "uno"}
    assert api.get("/api/store/items", params={"ns": "pruebas/ana", "key": "zz"}).status_code == 404
    lst = api.get("/api/store/items", params={"ns": "pruebas/ana"}).json()
    assert {x["key"] for x in lst} == {"a", "b"}
    assert ["pruebas", "ana"] in api.get("/api/store/namespaces", params={"prefix": "pruebas"}).json()
    assert api.delete("/api/store/items", params={"ns": "pruebas/ana", "key": "a"}).status_code == 204
    assert api.get("/api/store/items", params={"ns": "pruebas/ana", "key": "a"}).status_code == 404
    assert api.put("/api/store/items", json={"namespace": ["a/b"], "key": "k", "value": {}}).status_code == 400
    assert api.get("/api/store/items", params={"ns": "a//b"}).status_code == 400


def test_a3_store_proceso(api):
    mod = crear_modulo(api, "Store proceso", MOD_STORE_PROC)
    for k, t in (("x1", "hola"), ("borrame", "adios")):
        api.put("/api/store/items", json={"namespace": ["notas", "luis"], "key": k, "value": {"t": t}, "index": False})
    p = proyecto(api, "Store proc", {"sp": {"module": mod, "x": 0, "y": 0}}, cadena("sp"),
                 state_schema={"usuario": {"input": True}})
    tid = hilo(api, p["slug"])
    run = esperar_run(api, lanzar(api, tid, input={"usuario": "luis"})["run_id"])
    assert run["status"] == "success", run
    assert estado(api, tid)["leidos"] == ["adios", "hola"]
    keys = {x["key"] for x in api.get("/api/store/items", params={"ns": "notas/luis"}).json()}
    assert keys == {"x1", "desde_proceso"}


def test_a3_store_ollama_caido(api):
    vals = api.get("/api/settings").json()["values"]
    api.put("/api/store/items", json={"namespace": ["caido"], "key": "k", "value": {"t": "x"}, "index": False})
    try:
        assert api.put("/api/settings", json={"ollama_url": "http://127.0.0.1:9"}).status_code == 200
        r = api.get("/api/store/items", params={"ns": "caido", "query": "algo"})
        assert r.status_code == 502 and r.json()["code"] == "ollama"
        r = api.put("/api/store/items", json={"namespace": ["caido"], "key": "k2", "value": {"t": "y"}})
        assert r.status_code == 502
        assert api.get("/api/store/items", params={"ns": "caido", "key": "k"}).status_code == 200
    finally:
        api.put("/api/settings", json={"ollama_url": vals["ollama_url"]})


def test_a3_store_sobrevive_reinicio(tmp_path):
    import httpx
    from conftest import PORT, TOKEN, arrancar, parar, preparar_home
    h = tmp_path / "home"
    preparar_home(h)
    proc = arrancar(h, port=PORT - 1)
    c = httpx.Client(base_url=f"http://127.0.0.1:{PORT - 1}", headers={"X-Token": TOKEN}, timeout=30)
    try:
        c.put("/api/store/items", json={"namespace": ["dura"], "key": "k", "value": {"t": 1}, "index": False})
        parar(proc)
        proc = arrancar(h, port=PORT - 1)
        assert c.get("/api/store/items", params={"ns": "dura", "key": "k"}).json()["value"] == {"t": 1}
    finally:
        c.close()
        parar(proc)


# ------------------------------------------------------------------------------------------------ A7 (sin Ollama)
MOD_CHAT_FALSO = '''"""Chat falso para pruebas: responde con un texto fijo en fragmentos (stream messages)."""
META = {"name": "Chat falso", "kind": "task", "exec": "local", "inputs": ["messages"], "outputs": ["messages"]}
CONFIG = [{"key": "respuesta", "type": "text", "default": "hola soy un modelo falso de pruebas"}]
async def run(state, cfg, ctx):
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    llm = GenericFakeChatModel(messages=iter([AIMessage(content=cfg["respuesta"])]))
    resp = None
    async for ch in llm.astream(state["messages"]):
        resp = ch if resp is None else resp + ch
    return {"messages": [resp]}
'''


def test_a7_messages_sin_ollama(api):
    mod = crear_modulo(api, "Chat falso", MOD_CHAT_FALSO)
    p = proyecto(api, "Chat falso", {"chat": {"module": mod, "x": 0, "y": 0}}, cadena("chat"),
                 state_schema={"messages": {"reducer": "messages", "input": True, "output": True}})
    assert api.get(f"/api/projects/{p['slug']}/validate").json()["canales"]["messages"] == "messages"
    tid = hilo(api, p["slug"])
    r = lanzar(api, tid, input={"messages": [{"role": "user", "content": "hola"}]})
    assert esperar_run(api, r["run_id"])["status"] == "success"
    toks = api.get(f"/api/threads/{tid}/events", params={"run": r["run_id"], "types": "token"}).json()
    assert len(toks) >= 5 and all(t["node"] == "chat" and t["data"]["msg_id"] for t in toks)
    msgs = api.get(f"/api/threads/{tid}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "hola soy un modelo falso de pruebas"
    r = lanzar(api, tid, input={"messages": [{"role": "user", "content": "otra"}]})
    esperar_run(api, r["run_id"])
    assert len(api.get(f"/api/threads/{tid}/messages").json()) == 4
    st = api.get(f"/api/threads/{tid}/state").json()
    assert st["values"]["messages"][0]["content"] == "hola"
    p2 = proyecto(api, "Sin messages", lentos(("a", 0.1)), cadena("a"))
    t2 = hilo(api, p2["slug"])
    assert api.get(f"/api/threads/{t2}/messages").status_code == 404
    tools = {t["name"]: t for t in api.get("/api/tools").json()}
    assert tools["calculadora"]["args_schema"] == {"expresion": "str"} and "pedir_confirmacion" in tools
