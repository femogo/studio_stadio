"""Paso 1e: API REST + SSE + token, contra un servidor real en el puerto 8799."""
import json
import os
import signal
import threading
import time

import httpx
import pytest
from conftest import BASE, PORT, TOKEN, arrancar, esperar_run, parar, preparar_home

URL = f"http://127.0.0.1:{PORT}"


def test_token(server):
    assert httpx.get(URL + "/api/projects").status_code == 401
    assert httpx.get(URL + "/api/projects", headers={"X-Token": "malo"}).status_code == 401
    assert httpx.get(URL + "/api/projects", cookies={"studio_token": TOKEN}).status_code == 200
    assert httpx.get(URL + "/").status_code == 200
    r = httpx.get(URL + "/api/settings", headers={"X-Token": TOKEN})
    assert r.json()["values"]["token"] == TOKEN
    assert httpx.get(URL + "/static/../settings.json").status_code == 404


def test_modulos(api):
    mods = {m["file"]: m for m in api.get("/api/modules").json()}
    assert mods["entrada.py"]["meta"]["kind"] == "input"
    f = api.post("/api/modules", json={"name": "Prueba API"}).json()["file"]
    src = api.get(f"/api/modules/{f}/source").json()
    r = api.put(f"/api/modules/{f}/source", json={"source": "META = {}\ndef run(:\n", "sha": src["sha"]})
    assert r.status_code == 422 and r.json()["line"] == 2 and r.json()["code"] == "syntax"
    r = api.put(f"/api/modules/{f}/source", json={"source": src["source"] + "\n", "sha": "otro"})
    assert r.status_code == 409 and r.json()["code"] == "version_conflict"
    r = api.put(f"/api/modules/{f}/source", json={"source": src["source"] + "\n", "sha": src["sha"]})
    assert r.status_code == 200
    assert len(api.get(f"/api/modules/{f}/history").json()) == 1
    api.post("/api/projects", json={"name": "Usa entrada", "template": "vacio"})
    r = api.delete("/api/modules/entrada.py")
    assert r.status_code == 409 and r.json()["code"] == "module_in_use" and r.json()["usos"]
    assert api.delete(f"/api/modules/{f}").status_code == 204


def test_proyectos_version_export_import(api):
    p = api.post("/api/projects", json={"name": "Exportable", "template": "clip_personaje"}).json()
    assert p["version"] == 1
    p2 = api.put(f"/api/projects/{p['slug']}", json=p).json()
    assert p2["version"] == 2
    r = api.put(f"/api/projects/{p['slug']}", json=p)
    assert r.status_code == 409 and r.json()["code"] == "version_conflict" and r.json()["current"]["version"] == 2
    exp = api.get(f"/api/projects/{p['slug']}/export").json()
    imp = api.post("/api/projects/import", json=exp).json()
    exp2 = api.get(f"/api/projects/{imp['slug']}/export").json()
    assert exp == exp2
    v = api.get(f"/api/projects/{p['slug']}/validate").json()
    assert v["errores"] == [] and v["canales"]["video_final"] == "replace"
    assert "lipsync" in api.get(f"/api/projects/{p['slug']}/mermaid").json()["mermaid"]
    assert any(x["slug"] == p["slug"] for x in api.get("/api/projects").json())
    assert api.delete(f"/api/projects/{imp['slug']}").status_code == 204


def proyecto_lento(api, segundos=3):
    p = api.post("/api/projects", json={"name": "Lento", "template": "vacio"}).json()
    p["nodes"]["lento"] = {"module": "prueba_lenta.py", "label": "Lento", "x": 200, "y": 300,
                           "config": {"segundos": segundos}}
    p["edges"] = [{"id": "e1", "src": "__start__", "dst": "entrada"}, {"id": "e2", "src": "entrada", "dst": "lento"},
                  {"id": "e3", "src": "lento", "dst": "salida"}, {"id": "e4", "src": "salida", "dst": "__end__"}]
    return api.put(f"/api/projects/{p['slug']}", json=p).json()


def test_ejecucion_estado_historia_sse(api):
    p = api.post("/api/projects", json={"name": "Vacío", "template": "vacio"}).json()
    tid = api.post("/api/threads", json={"project": p["slug"]}).json()["thread_id"]
    eventos = []

    def sse():
        with httpx.stream("GET", URL + f"/api/events?thread={tid}", headers={"X-Token": TOKEN}, timeout=20) as r:
            for line in r.iter_lines():
                if line.startswith("event: "):
                    eventos.append(line[7:])
                if "thread.status" in eventos and eventos.count("run.status") >= 3:
                    break
    th = threading.Thread(target=sse, daemon=True)
    th.start()
    time.sleep(0.5)
    run = api.post(f"/api/threads/{tid}/runs", json={"input": {"texto": "Hola"}})
    assert run.status_code == 202
    r = esperar_run(api, run.json()["run_id"])
    assert r["status"] == "success"
    th.join(10)
    assert {"run.status", "node.start", "node.end", "checkpoint", "edge"} <= set(eventos)
    st = api.get(f"/api/threads/{tid}/state").json()
    assert st["values"]["texto"] == "Hola" and st["next"] == []
    hist = api.get(f"/api/threads/{tid}/history").json()
    assert len(hist) >= 3 and hist[0]["writes"] == ["salida"]
    # SSE con Last-Event-ID: reenvía desde la tabla
    with httpx.stream("GET", URL + f"/api/events?thread={tid}", headers={"X-Token": TOKEN, "Last-Event-ID": "1"},
                      timeout=5) as rs:
        primeras = []
        for line in rs.iter_lines():
            if line.startswith("id: "):
                primeras.append(int(line[4:]))
            if len(primeras) >= 3:
                break
    assert primeras and min(primeras) > 1
    v = api.get(f"/api/threads/{tid}/view").json()
    assert v["nodos"]["entrada"]["count"] == 1 and "e2" in v["aristas_recorridas"]
    outs = api.get(f"/api/outputs?thread={tid}").json()
    assert outs and outs[0]["texto"] == "Hola"
    # resume sin interrupt → 409 not_interrupted
    r = api.post(f"/api/threads/{tid}/runs", json={"resume": {"x": 1}})
    assert r.status_code == 409 and r.json()["code"] == "not_interrupted"


def test_bifurcar_y_copiar(api):
    p = api.post("/api/projects", json={"name": "Fork", "template": "vacio"}).json()
    tid = api.post("/api/threads", json={"project": p["slug"]}).json()["thread_id"]
    esperar_run(api, api.post(f"/api/threads/{tid}/runs", json={"input": {"texto": "original"}}).json()["run_id"])
    hist = api.get(f"/api/threads/{tid}/history").json()
    antes = next(h for h in hist if h["next"] == ["salida"])
    r = api.post(f"/api/threads/{tid}/state", json={"checkpoint_id": antes["checkpoint_id"],
                                                     "values": {"texto": "rama"}})
    assert r.status_code == 200, r.text
    cid = r.json()["checkpoint_id"]
    st = api.get(f"/api/threads/{tid}/state?checkpoint_id={cid}").json()
    assert st["values"]["texto"] == "rama" and st["next"] == ["salida"]
    run = api.post(f"/api/threads/{tid}/runs", json={"checkpoint_id": cid}).json()
    r = esperar_run(api, run["run_id"])
    assert r["status"] == "success" and r["kind"] == "fork"
    assert api.get(f"/api/threads/{tid}/state").json()["values"]["texto"] == "rama"
    hist2 = api.get(f"/api/threads/{tid}/history").json()
    orig = next(h for h in hist2 if h["checkpoint_id"] == hist[0]["checkpoint_id"])
    assert api.get(f"/api/threads/{tid}/state?checkpoint_id={orig['checkpoint_id']}").json()["values"]["texto"] == \
        "original"
    # copiar hilo
    c = api.post(f"/api/threads/{tid}/copy").json()["thread_id"]
    assert api.get(f"/api/threads/{c}/state").json()["values"]["texto"] == "rama"
    assert len(api.get(f"/api/threads/{c}/history").json()) == len(hist2)
    assert api.delete(f"/api/threads/{c}").status_code == 204
    assert api.get(f"/api/threads/{c}").status_code == 404


def test_busy_pausa_y_detener(api):
    p = proyecto_lento(api, 3)
    tid = api.post("/api/threads", json={"project": p["slug"]}).json()["thread_id"]
    run = api.post(f"/api/threads/{tid}/runs", json={"input": {}}).json()
    time.sleep(1)
    r = api.post(f"/api/threads/{tid}/runs", json={"input": {}})
    assert r.status_code == 409 and r.json()["code"] == "thread_busy"
    assert api.post(f"/api/threads/{tid}/state", json={"values": {"texto": "x"}}).status_code == 409
    assert api.post(f"/api/runs/{run['run_id']}/pause").status_code == 202
    r = esperar_run(api, run["run_id"])
    assert r["status"] == "drained"
    run2 = api.post(f"/api/threads/{tid}/runs", json={"input": None}).json()
    assert esperar_run(api, run2["run_id"])["status"] == "success"
    v = api.get(f"/api/threads/{tid}/view").json()
    assert "lento" not in v["nodos"] and v["nodos"]["salida"]["count"] == 1   # no se repitió 'lento'
    # detener
    tid2 = api.post("/api/threads", json={"project": p["slug"]}).json()["thread_id"]
    run = api.post(f"/api/threads/{tid2}/runs", json={"input": {}, "interrupt_before": [], "interrupt_after": []}).json()
    time.sleep(1.5)
    t0 = time.time()
    assert api.post(f"/api/runs/{run['run_id']}/stop").status_code == 202
    r = esperar_run(api, run["run_id"])
    assert r["status"] == "stopped" and time.time() - t0 < 3
    for pg in r["pgids"] or []:
        with pytest.raises(ProcessLookupError):
            os.killpg(pg, 0)


@pytest.mark.skipif(not (BASE / "cleo_0.png").exists(), reason="necesita los retratos de demo/ (no se publican)")
def test_media_y_quick_video(api, server):
    assert api.get("/api/media", params={"path": "/etc/passwd"}).status_code == 403
    assert api.get("/api/media", params={"path": str(server / "settings.json")}).status_code == 403
    r = api.get("/api/media", params={"path": str(BASE / "cleo_0.png")}, headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and len(r.content) == 10
    assert any(i["name"] == "cleo_0.png" for i in api.get("/api/images").json())
    r = api.post("/api/quick-video", json={"personaje": "Napoleón", "origen_texto": "texto", "texto": "Hola Francia",
                                           "imagen": str(BASE / "napoleon_demo.png"), "voz": "em_alex",
                                           "iniciar": False})
    assert r.status_code == 201
    p = api.get(f"/api/projects/{r.json()['slug']}").json()
    assert p["nodes"]["entrada"]["config"]["valores"]["texto"] == "Hola Francia"
    assert p["nodes"]["voz"]["config"]["voz"] == "em_alex"
    assert api.get(f"/api/threads/{r.json()['thread_id']}").json()["project"] == p["slug"]
    s = api.get("/api/system").json()
    assert "ollama" in s and "ram" in s


def test_reinicio_no_pierde_datos_y_running_pasa_a_stopped(tmp_path):
    h = tmp_path / "home"
    preparar_home(h)
    p = arrancar(h, PORT + 0) if False else None
    port = PORT - 1          # 8798: no choca con el servidor del módulo
    proc = arrancar(h, port)
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"X-Token": TOKEN}, timeout=20)
    try:
        pr = proyecto_lento(c, 30)
        tid = c.post("/api/threads", json={"project": pr["slug"]}).json()["thread_id"]
        run = c.post(f"/api/threads/{tid}/runs", json={"input": {}}).json()
        time.sleep(2.5)
        pgs = c.get(f"/api/runs/{run['run_id']}").json()["pgids"]
        assert pgs
        parar(proc, signal.SIGKILL)               # caída brusca: el subproceso sigue vivo (otra sesión)
        proc = arrancar(h, port)
        r = c.get(f"/api/runs/{run['run_id']}").json()
        assert r["status"] == "stopped" and "reiniciado" in r["error"]
        time.sleep(0.3)
        for pg in pgs:
            with pytest.raises(ProcessLookupError):
                os.killpg(pg, 0)
        assert c.get(f"/api/threads/{tid}").json()["project"] == pr["slug"]
        assert c.get(f"/api/projects/{pr['slug']}").status_code == 200
        assert len(c.get(f"/api/threads/{tid}/history").json()) >= 1
        assert c.get("/api/settings").json()["values"]["token"] == TOKEN
    finally:
        c.close()
        parar(proc)


def test_borrar_salida_v1(api, server):
    """Salidas: los vídeos heredados de v1 también se pueden eliminar (carpeta de ejecución completa)."""
    run = server / "legacy" / "runs" / "20260101-120000"
    run.mkdir(parents=True)
    (run / "final.mp4").write_bytes(b"\0")
    (run / "state.json").write_text('{"texto": "hola"}', encoding="utf-8")
    vs = [o for o in api.get("/api/outputs?kind=video").json() if o["legacy"]]
    assert [o["id"] for o in vs] == ["v1-20260101-120000"] and vs[0]["texto"] == "hola"
    for malo in ("v1-..", "v1-no-existe", "v1-20260101-120000%2F..", "abc"):
        assert api.delete(f"/api/outputs/{malo}").status_code == 404, malo
    assert api.delete("/api/outputs/v1-20260101-120000").status_code == 204
    assert not run.exists() and (server / "legacy" / "runs").is_dir()
    assert not [o for o in api.get("/api/outputs").json() if o["legacy"]]
