"""Paso 1f: módulos de vídeo + LLM + revisión humana con GPU y modelos reales (criterios 1, 3, 4, 5 y 6).

Ejecutar: .venv-studio/bin/python -m pytest -m gpu -q   (≈ 15 min)
"""
import json
import subprocess
import time
from pathlib import Path

import pytest
from conftest import esperar_run

pytestmark = pytest.mark.gpu
TEXTO = "Napoleón, tú llegaste con cañones; yo goberné Egipto veinte años."   # ~10 palabras
LARGO = 900


def ffprobe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name:format=duration", "-of", "json", path], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    j = json.loads(out.stdout)
    return j["streams"][0]["codec_name"], float(j["format"]["duration"])


def clip(api, origen="texto", **kw):
    b = {"personaje": "Cleopatra VII", "origen_texto": origen, "texto": TEXTO,
         "tema": "Responde a Napoleón, que presume de su expedición a Egipto.", "imagen": "/opt/historico/demo/cleo_0.png",
         "voz": "ef_dora", "nombre": "CLEOPATRA VII", "subtitulo": "Reina de Egipto", "tam": 256, "mejora": False,
         "iniciar": False, **kw}
    r = api.post("/api/quick-video", json=b).json()
    p = api.get(f"/api/projects/{r['slug']}").json()
    p["nodes"]["guion"]["config"]["num_predict"] = 60
    p["nodes"]["entrada"]["config"]["valores"]["palabras"] = 10
    api.put(f"/api/projects/{r['slug']}", json=p)
    return r["slug"], r["thread_id"]


def tiempos(api, run_id):
    evs = api.get(f"/api/runs/{run_id}").json()
    return evs


def test_clip_texto_breakpoint_y_bifurcacion(api, server):
    slug, tid = clip(api)
    run = api.post(f"/api/threads/{tid}/runs", json={"input": {}}).json()
    r = esperar_run(api, run["run_id"], timeout=LARGO)
    assert r["status"] == "success", r
    st = api.get(f"/api/threads/{tid}/state").json()["values"]
    codec, dur = ffprobe(st["video_final"])
    assert codec == "h264" and dur > 1
    vids = [o for o in api.get("/api/outputs?kind=video").json() if o.get("thread_id") == tid]
    assert vids and vids[0]["path"] == st["video_final"]
    # criterio 4: breakpoint antes de Montaje, editar 'nombre', Continuar → rótulo nuevo
    tid2 = api.post("/api/threads", json={"project": slug}).json()["thread_id"]
    run = api.post(f"/api/threads/{tid2}/runs", json={"input": {}, "interrupt_before": ["montaje"]}).json()
    r = esperar_run(api, run["run_id"], timeout=LARGO)
    assert r["status"] == "interrupted" and r["motivo"] == "breakpoint"
    assert api.get(f"/api/threads/{tid2}/state").json()["next"] == ["montaje"]
    assert api.post(f"/api/threads/{tid2}/state", json={"values": {"nombre": "REINA NUEVA"}}).status_code == 200
    run = api.post(f"/api/threads/{tid2}/runs", json={"input": None}).json()
    assert esperar_run(api, run["run_id"], timeout=LARGO)["status"] == "success"
    st2 = api.get(f"/api/threads/{tid2}/state").json()["values"]
    assert st2["nombre"] == "REINA NUEVA" and ffprobe(st2["video_final"])[0] == "h264"
    task_dir = Path(st2["video_final"]).parent
    assert json.loads((task_dir / "_in.json").read_text())["state"]["nombre"] == "REINA NUEVA"
    # criterio 5: bifurcar el hilo 1 desde el checkpoint previo a la voz con otro texto
    hist = api.get(f"/api/threads/{tid}/history").json()
    antes_voz = next(h for h in hist if "voz" in h["next"])
    cid = api.post(f"/api/threads/{tid}/state", json={"checkpoint_id": antes_voz["checkpoint_id"],
                                                      "values": {"texto": "Soy la última reina de Egipto."}}).json()
    run = api.post(f"/api/threads/{tid}/runs", json={"checkpoint_id": cid["checkpoint_id"]}).json()
    assert esperar_run(api, run["run_id"], timeout=LARGO)["status"] == "success"
    nuevo = api.get(f"/api/threads/{tid}/state").json()["values"]
    assert nuevo["texto"] == "Soy la última reina de Egipto." and nuevo["video_final"] != st["video_final"]
    assert Path(st["video_final"]).exists() and Path(st["audio"]).exists()      # la rama original queda intacta
    orig = api.get(f"/api/threads/{tid}/state?checkpoint_id={hist[0]['checkpoint_id']}").json()["values"]
    assert orig["texto"] == TEXTO and orig["video_final"] == st["video_final"]
    hist2 = api.get(f"/api/threads/{tid}/history").json()
    assert len({h["parent_checkpoint_id"] for h in hist2 if h["parent_checkpoint_id"] == antes_voz["parent_checkpoint_id"]}) >= 1
    assert len(hist2) > len(hist)


def responder(api, tid, valor):
    st = api.get(f"/api/threads/{tid}/state").json()
    assert st["interrupts"], st
    it = st["interrupts"][0]
    assert it["node"] == "revision" and it["response_schema"]["required"] == ["aprobado"]
    return api.post(f"/api/threads/{tid}/runs", json={"resume": {it["id"]: valor}, "interrupt_before": ["lipsync"]}).json()


def test_clip_tema_revision_y_rechazos(api):
    slug, tid = clip(api, origen="tema")
    run = api.post(f"/api/threads/{tid}/runs", json={"input": {}}).json()
    r = esperar_run(api, run["run_id"], timeout=LARGO)
    assert r["status"] == "interrupted" and r["motivo"] == "humano"
    # rechazar: vuelve al guion con el comentario
    run = responder(api, tid, {"aprobado": False, "comentario": "Menciona a César"})
    r = esperar_run(api, run["run_id"], timeout=LARGO)
    assert r["status"] == "interrupted" and r["motivo"] == "humano"
    st = api.get(f"/api/threads/{tid}/state").json()["values"]
    assert st["rechazos"] == 1 and st["comentario_revision"] == "Menciona a César"
    view = api.get(f"/api/threads/{tid}/view").json()
    assert view["nodos"]["guion"]["count"] == 1     # cuenta de esta ejecución
    # aprobar con texto editado → llega a la voz
    run = responder(api, tid, {"aprobado": True, "valor": "Texto editado por la persona revisora."})
    r = esperar_run(api, run["run_id"], timeout=LARGO)
    assert r["status"] == "interrupted" and r["motivo"] == "breakpoint"
    st = api.get(f"/api/threads/{tid}/state").json()["values"]
    assert st["texto"] == "Texto editado por la persona revisora." and st["audio"]
    vin = json.loads((Path(st["audio"]).parent / "_in.json").read_text())
    assert vin["state"]["texto"] == "Texto editado por la persona revisora."
    # tres rechazos → termina sin vídeo
    _, tid3 = clip(api, origen="tema")
    run = api.post(f"/api/threads/{tid3}/runs", json={"input": {}}).json()
    esperar_run(api, run["run_id"], timeout=LARGO)
    for i in range(3):
        run = responder(api, tid3, {"aprobado": False, "comentario": f"no {i}"})
        r = esperar_run(api, run["run_id"], timeout=LARGO)
    assert r["status"] == "success"
    st = api.get(f"/api/threads/{tid3}/state").json()
    assert st["next"] == [] and st["values"]["rechazos"] == 3 and "video_final" not in st["values"]


def vram():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    return int(out.split()[0])


def test_detener_durante_lipsync_y_continuar(api):
    base = vram()
    slug, tid = clip(api, tam=512)
    run = api.post(f"/api/threads/{tid}/runs", json={"input": {}}).json()
    t0 = time.time()
    while time.time() - t0 < LARGO:
        if subprocess.run(["pgrep", "-f", "SadTalker/inference.py|[i]nference.py --driven_audio"],
                          capture_output=True).returncode == 0:
            break
        time.sleep(0.5)
    time.sleep(8)
    assert api.post(f"/api/runs/{run['run_id']}/stop").status_code == 202
    time.sleep(3)
    assert subprocess.run(["pgrep", "-f", "[i]nference.py --driven_audio"], capture_output=True).returncode == 1
    r = esperar_run(api, run["run_id"])
    assert r["status"] == "stopped"
    import httpx
    assert httpx.get("http://localhost:11434/api/ps").json()["models"] == []
    assert vram() <= max(300, base + 50), (vram(), base)
    st = api.get(f"/api/threads/{tid}/state").json()
    assert st["next"] == ["lipsync"]
    p = api.get(f"/api/projects/{slug}").json()
    p["nodes"]["lipsync"]["config"]["tam"] = 256
    api.put(f"/api/projects/{slug}", json=p)
    run = api.post(f"/api/threads/{tid}/runs", json={"input": None}).json()
    assert esperar_run(api, run["run_id"], timeout=LARGO)["status"] == "success"
    view = api.get(f"/api/threads/{tid}/view").json()
    assert set(view["nodos"]) == {"lipsync", "montaje", "salida"}


def test_escritor_critico(api):
    p = api.post("/api/projects", json={"name": "Escritor", "template": "escritor_critico"}).json()
    for e in p["edges"]:
        if e["id"] == "e5":
            e["cond"] = "int(s.get('puntuacion') or 0) >= 11 or (s.get('iteracion') or 0) >= 2"
    p = api.put(f"/api/projects/{p['slug']}", json=p).json()
    tid = api.post("/api/threads", json={"project": p["slug"]}).json()["thread_id"]
    run = api.post(f"/api/threads/{tid}/runs", json={"input": {}}).json()
    r = esperar_run(api, run["run_id"], timeout=LARGO)
    assert r["status"] == "success", r
    v = api.get(f"/api/threads/{tid}/view").json()
    assert v["nodos"]["escritor"]["count"] >= 2 and v["nodos"]["salida"]["status"] == "success"
    st = api.get(f"/api/threads/{tid}/state").json()["values"]
    assert st["iteracion"] >= 2 and isinstance(st["texto"], str) and len(st["texto"]) > 20
    tid = api.post("/api/threads", json={"project": p["slug"]}).json()["thread_id"]
    run = api.post(f"/api/threads/{tid}/runs", json={"input": {}, "recursion_limit": 5}).json()
    r = esperar_run(api, run["run_id"], timeout=LARGO)
    assert r["status"] == "error" and "GraphRecursionError" in r["error"]
