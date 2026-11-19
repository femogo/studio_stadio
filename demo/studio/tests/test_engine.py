"""Paso 1d: db.py + engine.py con prueba_lenta (sin GPU)."""
import asyncio
import json
import os
import time
import uuid

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from db import DB, Projects, Settings
from engine import Engine, ThreadBusy
from modules import Registry

HUMANO = '''META={"kind":"interrupt","outputs":["aprobado","texto"]}
def run(state, cfg, ctx):
    v = ctx.interrupt({"valor": state.get("texto")}, {"type":"object","properties":{"aprobado":{"type":"boolean"}}})
    return {"aprobado": bool(v.get("aprobado")), "texto": v.get("valor") or state.get("texto")}
'''


@pytest.fixture
async def eng(home):
    (home / "modules/humano.py").write_text(HUMANO)
    db = DB(home / "data/studio.sqlite")
    await db.open()
    st = Settings(home / "settings.json")
    async with AsyncSqliteSaver.from_conn_string(str(home / "data/checkpoints.sqlite")) as cp:
        e = Engine(home, st, db, cp, Registry(home / "modules", home / "data/modsnap"),
                   Projects(home / "projects", home / "templates"))
        await e.start()
        yield e
        await e.shutdown()
    await db.close()


async def nuevo_hilo(e, pr):
    tid = str(uuid.uuid4())
    await e.db.exec("INSERT INTO threads(thread_id, project, title, created, updated) VALUES (?,?,?,?,?)",
                    (tid, pr["slug"], "t", time.time(), time.time()))
    return {"thread_id": tid, "project": pr["slug"]}


async def esperar(e, rc, timeout=30):
    await asyncio.wait_for(asyncio.shield(rc.task), timeout)
    return await e.db.one("SELECT * FROM runs WHERE run_id=?", (rc.run_id,))


def lentos(e, segs=(1, 3), extra=None):
    """__start__ → rapido ∥ lento → fin"""
    nodes = {"rapido": {"module": "prueba_lenta.py", "config": {"segundos": segs[0], "clave_salida": "r1"}},
             "lento": {"module": "prueba_lenta.py", "config": {"segundos": segs[1], "clave_salida": "r2"}},
             "fin": {"module": "prueba_lenta.py", "config": {"segundos": 0.1, "clave_salida": "r3"}}}
    edges = [{"id": "e1", "src": "__start__", "dst": "rapido"}, {"id": "e2", "src": "__start__", "dst": "lento"},
             {"id": "e3", "src": ["rapido", "lento"], "dst": "fin"}, {"id": "e4", "src": "fin", "dst": "__end__"}]
    return e.projects.create_from({"name": "Lentos", "nodes": nodes, "edges": edges, **(extra or {})})


async def counts(e, run_id):
    await e.bus.flush()
    rows = await e.db.all("SELECT node, data FROM events WHERE run_id=? AND type='node.start'", (run_id,))
    c = {}
    for r in rows:
        d = json.loads(r["data"])
        if "esperando_gpu" not in d and d.get("attempt") == 1:
            c[r["node"]] = c.get(r["node"], 0) + 1
    return c


async def test_vacio_success_y_outputs(eng):
    pr = eng.projects.create_from(eng.projects.template("vacio"))
    th = await nuevo_hilo(eng, pr)
    rc = await eng.create_run(th, {"input": {"texto": "Hola mundo"}})
    run = await esperar(eng, rc)
    assert run["status"] == "success" and run["kind"] == "input"
    outs = await eng.db.all("SELECT * FROM outputs WHERE run_id=?", (rc.run_id,))
    assert outs[0]["kind"] == "text" and outs[0]["texto"] == "Hola mundo"
    v = await eng.view(th["thread_id"])
    assert v["nodos"]["salida"]["status"] == "success" and {"e1", "e2", "e3"} <= set(v["aristas_recorridas"])
    assert await eng.thread_status(th["thread_id"]) == "idle"


async def test_pausar_drained_y_continuar_sin_repetir(eng):
    pr = lentos(eng)
    th = await nuevo_hilo(eng, pr)
    rc = await eng.create_run(th, {"input": {}})
    await asyncio.sleep(0.8)
    assert await eng.pause(rc.run_id)
    run = await esperar(eng, rc)
    assert run["status"] == "drained"
    assert await counts(eng, rc.run_id) == {"rapido": 1, "lento": 1}
    rc2 = await eng.create_run(th, {"input": None})
    run2 = await esperar(eng, rc2)
    assert run2["status"] == "success" and run2["kind"] == "continue"
    assert await counts(eng, rc2.run_id) == {"fin": 1}


async def test_detener_mata_procesos_y_continuar(eng):
    pr = lentos(eng, segs=(0.3, 20))
    th = await nuevo_hilo(eng, pr)
    rc = await eng.create_run(th, {"input": {}})
    await asyncio.sleep(2)
    pgs = set(rc.pgids)
    assert pgs
    t0 = time.time()
    await eng.stop(rc.run_id)
    run = await esperar(eng, rc)
    assert run["status"] == "stopped" and time.time() - t0 < 3
    for pg in pgs:
        with pytest.raises(ProcessLookupError):
            os.killpg(pg, 0)
    # continuar: 'rapido' ya terminó (escritura pendiente) y no se repite
    pr2 = eng.projects.get(pr["slug"])
    pr2["nodes"]["lento"]["config"]["segundos"] = 0.2
    eng.projects.put(pr["slug"], pr2)
    rc2 = await eng.create_run(th, {"input": None})
    run2 = await esperar(eng, rc2)
    assert run2["status"] == "success"
    assert await counts(eng, rc2.run_id) == {"lento": 1, "fin": 1}


async def test_thread_busy(eng):
    pr = lentos(eng, segs=(2, 2))
    th = await nuevo_hilo(eng, pr)
    rc = await eng.create_run(th, {"input": {}})
    with pytest.raises(ThreadBusy):
        await eng.create_run(th, {"input": {}})
    await eng.stop(rc.run_id)


async def test_breakpoint_antes_y_continuar(eng):
    pr = lentos(eng, segs=(0.1, 0.1))
    pr["nodes"]["fin"]["breakpoint"] = {"before": True, "after": False}
    pr = eng.projects.put(pr["slug"], pr)
    th = await nuevo_hilo(eng, pr)
    rc = await eng.create_run(th, {"input": {}})
    run = await esperar(eng, rc)
    assert run["status"] == "interrupted" and run["motivo"] == "breakpoint"
    assert await eng.thread_status(th["thread_id"]) == "interrupted"
    rc2 = await eng.create_run(th, {"input": None})
    assert (await esperar(eng, rc2))["status"] == "success"


async def test_interrupt_humano_y_resume_por_id(eng):
    pr = eng.projects.create_from({"name": "H", "nodes": {"h": {"module": "humano.py"}},
                                   "edges": [{"id": "e1", "src": "__start__", "dst": "h"}]})
    th = await nuevo_hilo(eng, pr)
    q = eng.bus.subscribe(th["thread_id"])
    rc = await eng.create_run(th, {"input": {"texto": "hola"}})
    run = await esperar(eng, rc)
    assert run["status"] == "interrupted" and run["motivo"] == "humano"
    evs = []
    while not q.empty():
        evs.append(q.get_nowait())
    it = [x for x in evs if x["type"] == "interrupt"][0]["data"]
    assert it["node"] == "h" and it["response_schema"]["properties"]["aprobado"] and it["value"] == {"valor": "hola"}
    rc2 = await eng.create_run(th, {"resume": {it["id"]: {"aprobado": True, "valor": "editado"}}})
    run2 = await esperar(eng, rc2)
    assert run2["status"] == "success" and run2["kind"] == "resume"
    graph = eng.graph_for(pr, await eng.history_keys(th["thread_id"]))
    st = await graph.aget_state({"configurable": {"thread_id": th["thread_id"]}})
    assert st.values["texto"] == "editado" and st.values["aprobado"] is True


async def test_recursion_limit_error(eng):
    pr = eng.projects.template("escritor_critico")
    pr = eng.projects.create_from({"name": "Bucle", "nodes": {
        "c": {"module": "codigo_python.py", "config": {"codigo": 'return {"i": (state.get("i") or 0) + 1}',
                                                        "entradas": "i", "salidas": "i"}}},
        "edges": [{"id": "e1", "src": "__start__", "dst": "c"}, {"id": "e2", "src": "c", "dst": "c", "cond": "True"}]})
    th = await nuevo_hilo(eng, pr)
    rc = await eng.create_run(th, {"input": {}, "recursion_limit": 3})
    run = await esperar(eng, rc)
    assert run["status"] == "error" and "GraphRecursionError" in run["error"]


async def test_reconciliacion(home):
    db = DB(home / "data/studio.sqlite")
    await db.open()
    await db.exec("INSERT INTO threads(thread_id, project) VALUES ('t','p')")
    await db.exec("INSERT INTO runs(run_id, thread_id, status, pgids, t0) VALUES ('r1','t','running','[999999]',1)")
    await db.exec("INSERT INTO runs(run_id, thread_id, status, t0) VALUES ('r2','t','pending',2)")
    e = Engine(home, Settings(home / "settings.json"), db, None, None, None)
    await e.reconcile()
    r = {x["run_id"]: x for x in await db.all("SELECT * FROM runs")}
    assert r["r1"]["status"] == "stopped" and "reiniciado" in r["r1"]["error"] and r["r2"]["status"] == "error"
    await db.close()


def test_settings_token_y_proyectos(home):
    s = Settings(home / "settings.json")
    assert len(s["token"]) >= 16 and json.loads((home / "settings.json").read_text())["token"] == s["token"]
    p = Projects(home / "projects", home / "templates")
    a = p.create_from(p.template("vacio"), name="Mi Proyecto")
    b = p.create_from(p.template("vacio"), name="Mi Proyecto")
    assert a["slug"] == "mi-proyecto" and b["slug"] == "mi-proyecto-2"
    a2 = p.put(a["slug"], a)
    assert a2["version"] == 2 and (home / "projects/mi-proyecto/versions/2.json").exists()
    from db import ConflictoVersion
    with pytest.raises(ConflictoVersion):
        p.put(a["slug"], a)
