#!/usr/bin/env python
"""Worker de un nodo del pipeline. Lo lanza server.py como proceso independiente.

Uso: python nodes.py <nodo> <carpeta_run>
Lee  <carpeta_run>/_in_<nodo>.json  -> {"state": {...}, "cfg": {...}}
Protocolo por stdout:
  @@P <0..1>        progreso del nodo
  @@S <json>        actualización parcial del estado (VideoState)
  cualquier otra línea = log
Código de salida != 0 -> error.
"""
import json, re, shutil, subprocess, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # /opt/historico/demo
SADTALKER = BASE / "SadTalker"
FONT_SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
FONT_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def P(x):
    print(f"@@P {max(0.0, min(1.0, x)):.4f}", flush=True)


def S(d):
    print("@@S " + json.dumps(d, ensure_ascii=False), flush=True)


def log(*a):
    print(*a, flush=True)


def resolver(p):
    p = Path(p)
    return p if p.is_absolute() else BASE / p


# ---------------------------------------------------------------- guion
def guion(st, cfg, rd):
    import urllib.request
    prompt = cfg["plantilla"].format(personaje=st.get("personaje", ""), tema=st.get("tema", ""),
                                     palabras=cfg["palabras"])
    log("Prompt enviado a Ollama:\n" + prompt)
    body = {"model": cfg["modelo"], "prompt": prompt, "stream": True, "keep_alive": 0,
            "options": {"temperature": float(cfg["temperatura"])}}
    req = urllib.request.Request(cfg["ollama_url"].rstrip("/") + "/api/generate",
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    P(0.03)
    log(f"Cargando {cfg['modelo']}…")
    out, n, objetivo = "", 0, int(cfg["palabras"]) * 2.2
    with urllib.request.urlopen(req, timeout=600) as r:
        for line in r:
            j = json.loads(line)
            out += j.get("response", "")
            n += 1
            P(0.05 + 0.9 * min(1, n / objetivo))
            if n % 3 == 0:
                S({"texto": out.strip()})
            if j.get("done"):
                break
    texto = out.strip().strip('"«»“”').strip()
    log("Respuesta:\n" + texto)
    S({"texto": texto, "texto_origen": "ollama:" + cfg["modelo"]})


# ---------------------------------------------------------------- voz
def voz(st, cfg, rd):
    texto = st.get("texto", "").strip()
    if not texto:
        sys.exit("El estado no tiene 'texto'")
    P(0.05)
    log("Cargando Kokoro…")
    import numpy as np, soundfile as sf
    from kokoro import KPipeline
    pipe = KPipeline(lang_code="e", repo_id="hexgrad/Kokoro-82M")
    P(0.5)
    trozos = []
    for g, _, a in pipe(texto, voice=cfg["voz"], speed=float(cfg["velocidad"])):
        log("Sintetizado:", g)
        trozos.append(a)
    P(0.9)
    audio = np.concatenate(trozos)
    out = rd / "voz.wav"
    sf.write(out, audio, 24000)
    dur = round(len(audio) / 24000, 2)
    log(f"Audio: {out} ({dur} s)")
    S({"audio": str(out), "duracion": dur, "voz": cfg["voz"]})


# ---------------------------------------------------------------- retrato
def retrato(st, cfg, rd):
    out = rd / "retrato.png"
    if cfg["modo"] == "existente":
        src = resolver(cfg["imagen"])
        if not src.exists():
            sys.exit(f"No existe la imagen {src}")
        shutil.copy(src, out)
        log(f"Usando retrato existente {src}")
        P(1)
        S({"imagen": str(out)})
        return
    P(0.02)
    log("Cargando SDXL…")
    import torch
    from diffusers import StableDiffusionXLPipeline
    pipe = StableDiffusionXLPipeline.from_pretrained(
        "stabilityai/stable-diffusion-xl-base-1.0", torch_dtype=torch.float16, variant="fp16", use_safetensors=True)
    pipe.enable_model_cpu_offload()
    P(0.2)
    pasos = int(cfg["pasos"])

    def cb(_p, i, _t, kw):
        P(0.2 + 0.78 * (i + 1) / pasos)
        return kw

    img = pipe(prompt=cfg["prompt"], negative_prompt=cfg["negativo"], num_inference_steps=pasos,
               guidance_scale=float(cfg["guidance"]), width=1024, height=1024,
               generator=torch.Generator("cuda").manual_seed(int(cfg["semilla"])),
               callback_on_step_end=cb).images[0]
    img.save(out)
    log(f"Retrato guardado en {out}")
    S({"imagen": str(out), "semilla": int(cfg["semilla"])})


# ---------------------------------------------------------------- lipsync
FASES = [("landmark Det", 3), ("3DMM Extraction", 5), ("mel", 1), ("audio2exp", 2),
         ("Face Renderer", 40), ("seamlessClone", 15), ("Face Enhancer", 34)]


def lipsync(st, cfg, rd):
    for k in ("audio", "imagen"):
        if not st.get(k):
            sys.exit(f"El estado no tiene '{k}': ejecuta antes los nodos previos")
    res = rd / "sadtalker"
    shutil.rmtree(res, ignore_errors=True)
    cmd = [sys.executable, "-u", "inference.py", "--driven_audio", st["audio"], "--source_image", st["imagen"],
           "--result_dir", str(res), "--preprocess", "full", "--size", str(cfg["tam"]),
           "--expression_scale", str(cfg["expresion"])]
    if cfg["quieto"]:
        cmd.append("--still")
    fases = [f for f in FASES if cfg["mejora"] or f[0] != "Face Enhancer"]
    if cfg["mejora"]:
        cmd += ["--enhancer", "gfpgan"]
    total = sum(w for _, w in fases)
    frac = {k: 0.0 for k, _ in fases}
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=SADTALKER, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    buf = b""
    while True:
        ch = proc.stdout.read1(4096)
        if not ch:
            break
        buf += ch
        partes = re.split(rb"[\r\n]", buf)
        buf = partes.pop()
        for raw in partes:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            log(line)
            m = re.match(r"^(.*?):+\s*(\d+)%", line)
            if not m:
                continue
            for idx, (k, _) in enumerate(fases):
                if m.group(1).startswith(k):
                    for prev, _ in fases[:idx]:
                        frac[prev] = 1.0
                    frac[k] = int(m.group(2)) / 100
                    P(0.99 * sum(frac[n] * w for n, w in fases) / total)
    rc = proc.wait()
    if rc != 0:
        sys.exit(f"SadTalker terminó con código {rc}")
    videos = sorted(res.glob("*.mp4"))
    if not videos:
        sys.exit("SadTalker no produjo vídeo")
    out = rd / "lipsync.mp4"
    shutil.copy(videos[-1], out)
    log(f"Vídeo lip-sync: {out}")
    S({"video_lipsync": str(out)})


# ---------------------------------------------------------------- montaje
def esc(t):
    return t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")


def montaje(st, cfg, rd):
    if not st.get("video_lipsync"):
        sys.exit("El estado no tiene 'video_lipsync'")
    out = rd / "final.mp4"
    f = ("[0:v]split[a][b];[a]scale=1920:1920,crop=1920:1080,boxblur=30:3,eq=brightness=-0.15[bg];"
         "[b]scale=1080:1080[fg];[bg][fg]overlay=420:0")
    if cfg["nombre"]:
        f += (",drawbox=x=0:y=850:w=1100:h=130:color=black@0.65:t=fill"
              f",drawbox=x=0:y=850:w=12:h=130:color={cfg['color']}:t=fill"
              f",drawtext=fontfile={FONT_SERIF}:text='{esc(cfg['nombre'])}':fontcolor={cfg['color']}:fontsize=52:x=40:y=868")
        if cfg["subtitulo"]:
            f += f",drawtext=fontfile={FONT_SANS}:text='{esc(cfg['subtitulo'])}':fontcolor=white:fontsize=30:x=42:y=932"
    if cfg["aviso"]:
        f += (f",drawtext=fontfile={FONT_SANS}:text='{esc(cfg['aviso'])}':fontcolor=white@0.8:fontsize=24"
              ":x=w-tw-30:y=30:box=1:boxcolor=black@0.5:boxborderw=10")
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", st["video_lipsync"],
           "-filter_complex", f, "-c:v", "libx264", "-crf", str(cfg["crf"]), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    log("$ ffmpeg … " + str(out))
    dur = float(st.get("duracion") or 5)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_us="):
            try:
                P(0.99 * int(line.split("=")[1]) / 1e6 / dur)
            except ValueError:
                pass
        elif line and "=" not in line:
            log(line)
    if proc.wait() != 0:
        sys.exit("FFmpeg falló")
    (rd / "texto.txt").write_text(st.get("texto", "") + "\n")
    log(f"Vídeo final: {out}")
    S({"video_final": str(out)})


if __name__ == "__main__":
    nodo, rd = sys.argv[1], Path(sys.argv[2])
    entrada = json.loads((rd / f"_in_{nodo}.json").read_text())
    {"guion": guion, "voz": voz, "retrato": retrato, "lipsync": lipsync, "montaje": montaje}[nodo](
        entrada["state"], entrada["cfg"], rd)
    P(1)
