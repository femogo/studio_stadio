#!/usr/bin/env python
"""Estudio: servidor del pipeline de vídeo (estilo LangGraph).

Arranque:  python server.py  [--host 0.0.0.0] [--port 8790]
"""
import argparse, asyncio, copy, json, os, re, signal, sys, time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
RUNS = HERE / "runs"
RUNS.mkdir(exist_ok=True)
CFG_FILE = HERE / "config.json"

# ------------------------------------------------------------------ esquema de nodos
F = lambda key, label, type="text", default="", **kw: dict(key=key, label=label, type=type, default=default, **kw)

NODES = {
    "entrada": dict(label="Entrada", desc="Prompt inicial: texto fijo o tema para que el LLM escriba el guion.", fields=[
        F("modo", "Modo", "select", "texto", options=["texto", "tema"],
          help="texto: se usa tal cual (se omite el nodo Guion). tema: el LLM escribe la intervención."),
        F("personaje", "Personaje", "text", "Cleopatra VII"),
        F("texto", "Texto fijo", "textarea",
          "Napoleón, tú llegaste a Egipto con cañones y sabios. Yo lo goberné veinte años."),
        F("tema", "Tema / instrucción", "textarea", "Responde a Napoleón, que presume de su expedición a Egipto de 1798."),
    ]),
    "guion": dict(label="Guion", desc="El LLM local (Ollama) escribe la intervención.", weight=10, fields=[
        F("modelo", "Modelo Ollama", "text", "qwen2.5:7b"),
        F("ollama_url", "URL de Ollama", "text", "http://localhost:11434"),
        F("palabras", "Palabras (≈2,6 por segundo)", "number", 14),
        F("temperatura", "Temperatura", "number", 0.7, step=0.1),
        F("plantilla", "Plantilla del prompt", "textarea",
          "Eres {personaje} en un debate histórico televisado. {tema} Responde con UNA intervención de unas "
          "{palabras} palabras, en español, en primera persona, con datos históricos reales y el tono propio "
          "del personaje. Solo el texto hablado, sin comillas ni acotaciones.",
          help="Variables: {personaje}, {tema}, {palabras}"),
    ]),
    "revision": dict(label="Revisión", desc="Revisión humana del guion (interrupt): el pipeline espera tu aprobación.",
                     weight=0, fields=[F("activo", "Pedir revisión del guion", "checkbox", True)]),
    "voz": dict(label="Voz", desc="Texto a voz con Kokoro (local).", weight=5, fields=[
        F("voz", "Voz", "select", "ef_dora", options=["ef_dora", "em_alex", "em_santa"],
          help="ef_dora = mujer · em_alex / em_santa = hombre"),
        F("velocidad", "Velocidad", "number", 0.95, step=0.05),
    ]),
    "retrato": dict(label="Retrato", desc="Imagen del personaje: existente o generada con SDXL.", weight=15, fields=[
        F("modo", "Modo", "select", "existente", options=["existente", "sdxl"]),
        F("imagen", "Imagen existente (ruta)", "text", "cleo_0.png", help="Relativa a /opt/historico/demo"),
        F("prompt", "Prompt SDXL", "textarea",
          "photorealistic portrait of Cleopatra VII, ancient Egyptian queen, looking directly at camera, frontal, "
          "head and shoulders, neutral closed mouth, olive skin, dark braided hair with gold diadem and cobra uraeus, "
          "gold broad collar necklace, inside dim palace TV debate studio, soft cinematic key light, 85mm, sharp focus"),
        F("negativo", "Prompt negativo", "textarea",
          "cartoon, painting, illustration, open mouth, teeth, profile, side view, hands, text, watermark, blurry, "
          "deformed, celebrity"),
        F("semilla", "Semilla", "number", 7),
        F("pasos", "Pasos", "number", 30),
        F("guidance", "Guidance", "number", 6.5, step=0.5),
    ]),
    "lipsync": dict(label="Lip-sync", desc="SadTalker anima la cara con el audio.", weight=60, fields=[
        F("tam", "Resolución de cara", "select", "512", options=["256", "512"]),
        F("mejora", "Mejorar cara con GFPGAN (más lento)", "checkbox", True),
        F("quieto", "Cabeza quieta (--still)", "checkbox", True),
        F("expresion", "Intensidad de expresión", "number", 1.1, step=0.1),
    ]),
    "montaje": dict(label="Montaje", desc="FFmpeg: 16:9, fondo, rótulo y aviso de IA.", weight=10, fields=[
        F("nombre", "Rótulo: nombre", "text", "CLEOPATRA VII"),
        F("subtitulo", "Rótulo: subtítulo", "text", "Reina de Egipto (51–30 a.C.)"),
        F("aviso", "Aviso", "text", "Recreación ficticia generada con IA"),
        F("color", "Color de acento", "text", "0xE8C766"),
        F("crf", "Calidad CRF (menor = mejor)", "number", 18),
    ]),
    "resultado": dict(label="Resultado", desc="Vídeo final.", fields=[]),
}
ORDER = ["guion", "revision", "voz", "retrato", "lipsync", "montaje"]
ALL = ["entrada"] + ORDER + ["resultado"]


def defaults():
    d = {n: {f["key"]: f["default"] for f in spec["fields"]} for n, spec in NODES.items()}
    for n in ORDER:
        d[n]["breakpoint"] = False
    return d


def load_config():
    cfg = defaults()
    if CFG_FILE.exists():
        try:
            saved = json.loads(CFG_FILE.read_text())
            for n in cfg:
                cfg[n].update({k: v for k, v in saved.get(n, {}).items() if k in cfg[n]})
        except json.JSONDecodeError:
            pass
    return cfg


def save_config(cfg):
    tmp = CFG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    tmp.replace(CFG_FILE)


# ------------------------------------------------------------------ estado en memoria
def fresh_nodes():
    return {n: dict(status="pending", progress=0.0, t0=None, t1=None, error=None, note=None) for n in ALL}


R = dict(run_id=None, status="idle", current=None, t0=None, t1=None, nodes=fresh_nodes(), state={})
LOGS = {n: [] for n in ALL}


class Ctl:
    task = None
    proc = None
    stop = False
    pause_req = False
    resume = None
    approve = None


C = Ctl()
VERSION = 0
CHANGED = None


def notify():
    global VERSION, CHANGED
    VERSION += 1
    if CHANGED:
        CHANGED.set()
        CHANGED = asyncio.Event()


def busy():
    return C.task is not None and not C.task.done()


def overall():
    num = den = 0.0
    for n in ORDER:
        w = NODES[n].get("weight", 0)
        ns = R["nodes"][n]
        if ns["status"] == "skipped":
            continue
        den += w
        num += w * (1.0 if ns["status"] == "done" else ns["progress"])
    return num / den if den else 0.0


def snapshot():
    return dict(R, overall=overall(), busy=busy(), version=VERSION, config=load_config(),
                logs={n: LOGS[n][-300:] for n in ALL})


def run_dir():
    return RUNS / R["run_id"]


def persist():
    if R["run_id"]:
        (run_dir() / "state.json").write_text(json.dumps(
            dict(run_id=R["run_id"], status=R["status"], nodes=R["nodes"], state=R["state"]),
            ensure_ascii=False, indent=2))


TQDM = re.compile(r"^(.{1,60}?):+\s*\d+%\|")


def add_log(n, line):
    lg = LOGS[n]
    m = TQDM.match(line)
    if m and lg:
        m2 = TQDM.match(lg[-1])
        if m2 and m2.group(1) == m.group(1):
            lg[-1] = line
            return
    lg.append(line)
    if len(lg) > 1000:
        del lg[:200]


# ------------------------------------------------------------------ ejecución
async def run_worker(n, ncfg):
    rd = run_dir()
    (rd / f"_in_{n}.json").write_text(json.dumps(dict(state=R["state"], cfg=ncfg), ensure_ascii=False))
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-u", str(HERE / "nodes.py"), n, str(rd), cwd=str(BASE),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    C.proc = proc
    ns = R["nodes"][n]

    def handle(line):
        line = line.rstrip()
        if not line:
            return
        if line.startswith("@@P "):
            ns["progress"] = float(line[4:])
        elif line.startswith("@@S "):
            R["state"].update(json.loads(line[4:]))
        else:
            add_log(n, line)
        notify()

    buf = b""
    while True:
        ch = await proc.stdout.read(4096)
        if not ch:
            break
        buf += ch
        parts = re.split(rb"[\r\n]", buf)
        buf = parts.pop()
        for raw in parts:
            handle(raw.decode("utf-8", "replace"))
    if buf:
        handle(buf.decode("utf-8", "replace"))
    rc = await proc.wait()
    C.proc = None
    if rc != 0 and not C.stop:
        tail = [l for l in LOGS[n] if l.strip()][-1:] or [""]
        ns["error"] = f"Código de salida {rc}. {tail[0]}"[:500]
    return rc == 0


def start_node(n):
    R["current"] = n
    R["nodes"][n].update(status="running", progress=0.0, t0=time.time(), t1=None, error=None, note=None)
    LOGS[n].clear()
    notify()


def end_node(n, status, note=None):
    R["nodes"][n].update(status=status, t1=time.time(), note=note,
                         progress=1.0 if status in ("done", "skipped") else R["nodes"][n]["progress"])
    persist()
    notify()


async def pipeline(start_idx):
    R.update(status="running", t1=None)
    if start_idx == 0:
        R["t0"] = time.time()
    try:
        if start_idx == 0:
            cfg = load_config()
            e = cfg["entrada"]
            start_node("entrada")
            R["state"] = dict(modo=e["modo"], personaje=e["personaje"], tema=e["tema"])
            if e["modo"] == "texto":
                R["state"].update(texto=e["texto"].strip(), texto_origen="manual")
            add_log("entrada", json.dumps(R["state"], ensure_ascii=False, indent=2))
            end_node("entrada", "done")

        for n in ORDER[start_idx:]:
            cfg = load_config()
            if C.stop:
                break
            if cfg[n].get("breakpoint") or C.pause_req:
                C.pause_req = False
                R.update(status="paused", current=n)
                R["nodes"][n].update(status="paused")
                notify()
                C.resume.clear()
                await C.resume.wait()
                if C.stop:
                    break
                cfg = load_config()  # aplica cambios hechos durante la pausa
            R["status"] = "running"
            start_node(n)
            ns = R["nodes"][n]

            if n == "guion" and R["state"].get("modo") == "texto":
                add_log(n, "Modo texto fijo: no se llama al LLM.")
                end_node(n, "skipped", "texto fijo")
                continue
            if n == "revision":
                if not cfg[n]["activo"]:
                    end_node(n, "skipped", "desactivada")
                    continue
                ns["status"] = "waiting"
                R["status"] = "waiting"
                add_log(n, "Esperando aprobación del guion…")
                notify()
                C.approve.clear()
                await C.approve.wait()
                if C.stop:
                    break
                add_log(n, "Aprobado: " + R["state"].get("texto", ""))
                end_node(n, "done")
                R["status"] = "running"
                continue

            ok = await run_worker(n, cfg[n])
            if C.stop:
                break
            if not ok:
                end_node(n, "error")
                R.update(status="error", t1=time.time())
                return
            end_node(n, "done")

        if C.stop:
            for n in ALL:
                if R["nodes"][n]["status"] in ("running", "paused", "waiting"):
                    end_node(n, "stopped", "detenido por el usuario")
            R.update(status="stopped", t1=time.time())
        else:
            start_node("resultado")
            add_log("resultado", R["state"].get("video_final", ""))
            end_node("resultado", "done")
            R.update(status="done", current=None, t1=time.time())
    except Exception as ex:  # noqa
        n = R["current"] or "entrada"
        add_log(n, f"ERROR interno: {ex!r}")
        R["nodes"][n]["error"] = repr(ex)
        end_node(n, "error")
        R.update(status="error", t1=time.time())
    finally:
        persist()
        notify()


# ------------------------------------------------------------------ API
app = FastAPI(title="Estudio de debates históricos")


@app.on_event("startup")
async def _startup():
    global CHANGED
    CHANGED = asyncio.Event()
    C.resume = asyncio.Event()
    C.approve = asyncio.Event()


@app.get("/")
async def index():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/schema")
async def schema():
    return dict(nodes=NODES, order=ORDER, all=ALL)


@app.get("/api/config")
async def get_config():
    return load_config()


@app.put("/api/config/{node}")
async def put_config(node: str, request: Request):
    cfg = load_config()
    if node not in cfg:
        raise HTTPException(404, "nodo desconocido")
    body = await request.json()
    cfg[node].update({k: v for k, v in body.items() if k in cfg[node]})
    save_config(cfg)
    notify()
    return cfg[node]


@app.post("/api/config/{node}/reset")
async def reset_config(node: str):
    cfg = load_config()
    if node not in cfg:
        raise HTTPException(404)
    cfg[node] = defaults()[node]
    save_config(cfg)
    notify()
    return cfg[node]


@app.post("/api/run")
async def run(request: Request):
    if busy():
        raise HTTPException(409, "Ya hay una ejecución en curso")
    body = await request.json() if (await request.body()) else {}
    desde = body.get("from")
    C.stop = False
    C.pause_req = False
    if desde and desde in ORDER and R["run_id"]:
        idx = ORDER.index(desde)
        for n in ORDER[idx:] + ["resultado"]:
            R["nodes"][n] = fresh_nodes()[n]
            LOGS[n].clear()
    else:
        idx = 0
        R.update(run_id=time.strftime("%Y%m%d-%H%M%S"), nodes=fresh_nodes(), state={}, current=None)
        for n in ALL:
            LOGS[n].clear()
        run_dir().mkdir(parents=True, exist_ok=True)
    C.task = asyncio.create_task(pipeline(idx))
    notify()
    return dict(ok=True, run_id=R["run_id"], start=ORDER[idx])


@app.post("/api/pause")
async def pause():
    if R["status"] == "running":
        C.pause_req = True
        add_log(R["current"] or "entrada", "⏸ Pausa solicitada: se detendrá antes del siguiente nodo.")
        notify()
    return dict(ok=True)


@app.post("/api/resume")
async def resume():
    C.resume.set()
    return dict(ok=True)


@app.post("/api/stop")
async def stop():
    C.stop = True
    if C.proc and C.proc.returncode is None:
        try:
            os.killpg(C.proc.pid, signal.SIGTERM)
            await asyncio.sleep(1.5)
            if C.proc and C.proc.returncode is None:
                os.killpg(C.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    C.resume.set()
    C.approve.set()
    return dict(ok=True)


@app.post("/api/approve")
async def approve(request: Request):
    body = await request.json()
    if R["status"] != "waiting":
        raise HTTPException(409, "No hay nada esperando aprobación")
    if isinstance(body.get("texto"), str):
        R["state"]["texto"] = body["texto"].strip()
        R["state"]["texto_origen"] = (R["state"].get("texto_origen") or "") + "+revisado"
    C.approve.set()
    return dict(ok=True)


@app.post("/api/state")
async def set_state(request: Request):
    if R["status"] == "running":
        raise HTTPException(409, "No se puede editar el estado mientras un nodo se ejecuta")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "El estado debe ser un objeto JSON")
    R["state"] = body
    persist()
    notify()
    return dict(ok=True)


@app.get("/api/runs")
async def runs():
    out = []
    for d in sorted(RUNS.iterdir(), reverse=True):
        f = d / "state.json"
        if d.is_dir() and f.exists() and (d / "final.mp4").exists():
            try:
                s = json.loads(f.read_text())
                out.append(dict(id=d.name, personaje=s["state"].get("personaje", ""), texto=s["state"].get("texto", ""),
                                duracion=s["state"].get("duracion")))
            except Exception:
                pass
    return out


@app.get("/api/runs/{rid}")
async def run_info(rid: str):
    f = RUNS / rid / "state.json"
    if not re.fullmatch(r"[\w-]+", rid) or not f.exists():
        raise HTTPException(404)
    return json.loads(f.read_text())


@app.get("/api/events")
async def events(request: Request):
    async def gen():
        last = -1
        while True:
            if await request.is_disconnected():
                break
            if VERSION != last:
                last = VERSION
                yield f"data: {json.dumps(snapshot(), ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.2)
            else:
                ev = CHANGED
                try:
                    await asyncio.wait_for(ev.wait(), 15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


app.mount("/runs", StaticFiles(directory=RUNS), name="runs")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8790)
    a = ap.parse_args()
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
