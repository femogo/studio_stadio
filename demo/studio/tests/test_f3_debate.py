"""Fase 3, criterio transversal T1: plantilla `debate` (subproyecto + Send + concatenar_video) (DISENO.md §13.5)."""
import json
import subprocess

import pytest
from conftest import esperar_run
from test_f2_api import estado, hilo, lanzar, py_node

INTERVENCIONES = [{"personaje": "Cleopatra VII", "texto": "uno"}, {"personaje": "Napoleón", "texto": "dos"},
                  {"personaje": "cleopatra", "texto": "tres"}, {"personaje": "Napoleón Bonaparte", "texto": "cuatro"}]

# clip sintético: duración 1 + 0,5·orden s
CLIP_FALSO = '''import subprocess, time
o = int(state.get("orden") or 0)
dur = 1 + 0.5 * o
out = str(ctx.run_dir / "clip.mp4")
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=256x256:rate=25:duration={dur}",
                "-f", "lavfi", "-i", f"sine=frequency={300 + 100 * o}:duration={dur}", "-shortest",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", out], check=True)
return {"video_lipsync": out, "duracion": dur}'''


def duracion(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    return float(json.loads(r.stdout)["format"]["duration"])


def crear_debate(api, nombre):
    r = api.post("/api/projects", json={"name": nombre, "template": "debate"})
    assert r.status_code == 201, r.text
    p = r.json()
    sub_slug = p["nodes"]["intervencion"]["config"]["proyecto"]
    assert sub_slug == f"{p['slug']}-intervencion"
    hijo = api.get(f"/api/projects/{sub_slug}").json()
    assert set(hijo["nodes"]) == {"prep", "voz", "lipsync", "fin"}
    v = api.get(f"/api/projects/{p['slug']}/validate").json()
    assert v["errores"] == [], v
    assert v["subgrafos"]["intervencion"]["proyecto"] == sub_slug
    return p, hijo


def test_t1_debate_topologia_sin_gpu(api):
    assert any(t["id"] == "debate" and t["fase"] == 3 for t in api.get("/api/templates").json())
    p, hijo = crear_debate(api, "Debate prueba")
    # segunda creación: slugs libres para padre e hijo
    p2, hijo2 = crear_debate(api, "Debate prueba")
    assert p2["slug"] != p["slug"] and hijo2["slug"] == f"{p2['slug']}-intervencion"
    # sustituir LLM, voz y lip-sync por módulos de prueba (misma topología)
    p["nodes"]["moderador"] = {**py_node(f"return {{'intervenciones': {INTERVENCIONES!r}}}", "", "intervenciones"),
                               "label": "Moderador falso"}
    # los Send se aplican en el orden de la lista: se invierte para que los clips lleguen desordenados al append
    p["nodes"]["invertir"] = py_node('return {"turnos_debate": list(reversed(state["turnos_debate"]))}',
                                     "turnos_debate", "turnos_debate")
    p["edges"] = [e for e in p["edges"] if e["id"] != "e4"] + [
        {"id": "e4a", "src": "reparto", "dst": "invertir"}, {"id": "e4b", "src": "invertir", "dst": "mapa"}]
    assert api.put(f"/api/projects/{p['slug']}", json=p).status_code == 200
    hijo["nodes"]["voz"] = py_node('return {"audio": "x"}', "texto", "audio")
    hijo["nodes"]["lipsync"] = py_node(CLIP_FALSO, "orden", "video_lipsync,duracion")
    assert api.put(f"/api/projects/{hijo['slug']}", json=hijo).status_code == 200
    tid = hilo(api, p["slug"])
    run = esperar_run(api, lanzar(api, tid, input={})["run_id"], timeout=120)
    assert run["status"] == "success", run
    st = estado(api, tid)
    turnos = st["turnos_debate"]
    assert [t["orden"] for t in turnos] == [3, 2, 1, 0]
    assert [t["voz"] for t in turnos] == ["em_alex", "ef_dora", "em_alex", "ef_dora"]
    assert turnos[0]["imagen"].endswith("napoleon_demo.png")
    assert len(st["clips"]) == 4
    assert [c["orden"] for c in st["clips"]] != [0, 1, 2, 3]          # llegaron desordenados…
    por_orden = {c["orden"]: c["video"] for c in st["clips"]}
    assert st["clips_usados"] == [por_orden[i] for i in range(4)]     # …y se concatenan en orden
    assert duracion(st["video_final"]) == pytest.approx(1 + 1.5 + 2 + 2.5, abs=0.5)


@pytest.mark.gpu
def test_t1_debate_real(api):
    p, hijo = crear_debate(api, "Debate real")
    p["nodes"]["entrada"]["config"]["valores"]["palabras"] = 12
    assert api.put(f"/api/projects/{p['slug']}", json=p).status_code == 200
    tid = hilo(api, p["slug"])
    run = esperar_run(api, lanzar(api, tid, input={"tema": "¿Quién fue mejor estratega?"})["run_id"], timeout=1800)
    assert run["status"] == "success", (run["error"], (run.get("traceback") or "")[-3000:])
    st = estado(api, tid)
    assert len(st["turnos_debate"]) == 4, st["turnos_debate"]
    suma = sum(duracion(c["video"]) for c in st["clips"])
    assert duracion(st["video_final"]) == pytest.approx(suma, abs=1.0)
    por_orden = {c["orden"]: c["video"] for c in st["clips"]}
    assert st["clips_usados"] == [por_orden[i] for i in sorted(por_orden)]
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
                          "-of", "csv=p=0", st["video_final"]], capture_output=True, text=True)
    assert out.stdout.strip() == "h264"
