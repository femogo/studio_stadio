"""Fase 2, T1: plantilla resumen_rss de punta a punta contra un RSS local (Ollama + Kokoro reales)."""
import subprocess

import pytest
from conftest import esperar_run
from test_f2_modulos import web  # noqa: F401  (fixture)

pytestmark = pytest.mark.gpu


def test_t1_resumen_rss(api, web):  # noqa: F811
    p = api.post("/api/projects", json={"name": "Resumen RSS", "template": "resumen_rss"}).json()
    p["nodes"]["entrada"]["config"]["valores"]["url_rss"] = web + "/feed.xml"
    p["nodes"]["entrada"]["config"]["valores"]["palabras"] = 30
    p = api.put(f"/api/projects/{p['slug']}", json=p).json()
    tid = api.post("/api/threads", json={"project": p["slug"]}).json()["thread_id"]
    r = api.post(f"/api/threads/{tid}/runs", json={"input": {}}).json()
    run = esperar_run(api, r["run_id"], timeout=300)
    assert run["status"] == "interrupted" and run["motivo"] == "humano", run
    st = api.get(f"/api/threads/{tid}/state").json()
    assert "noticia" in st["values"]["noticias_texto"].lower()
    it = st["interrupts"][0]
    r = api.post(f"/api/threads/{tid}/runs", json={"resume": {it["id"]: {"aprobado": True}}}).json()
    run = esperar_run(api, r["run_id"], timeout=600)
    assert run["status"] == "success", run
    audio = api.get(f"/api/threads/{tid}/state").json()["values"]["audio"]
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", audio],
                               capture_output=True, text=True).stdout.strip())
    assert dur > 1
    salidas = api.get("/api/outputs", params={"kind": "audio"}).json()
    assert any(o.get("thread_id") == tid for o in salidas)
