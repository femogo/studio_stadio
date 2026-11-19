"""Concatenar vídeos: une una lista de clips en el orden indicado (ffmpeg)."""
import json
import subprocess
import sys

META = {
    "name": "Concatenar vídeos", "category": "Vídeo", "icon": "🎞", "kind": "task", "exec": "proceso",
    "interprete": "pesado", "gpu": False,
    "desc": "Une los clips de una lista [{orden, video}] (p. ej. reunidos con una clave append tras un Send) en el "
            "orden de 'orden'. Normaliza tamaño, fps y audio.",
    "inputs": [], "inputs_from_config": ["campo_clips"], "outputs": ["clips_usados"],
    "outputs_from_config": ["campo_salida"], "weight": 5,
    "defaults": {"retry": {"max_attempts": 1}, "timeout": {"run": 1800, "idle": 300}},
}

CONFIG = [
    {"key": "campo_clips", "label": "Clave de la lista de clips", "type": "state_key", "default": "clips"},
    {"key": "campo_salida", "label": "Clave de salida", "type": "state_key", "default": "video_final"},
    {"key": "crf", "label": "Calidad (CRF)", "type": "number", "default": 20},
]


def _probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                        "-of", "json", path], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ffprobe no puede leer {path}: {r.stderr[-300:]}")
    d = json.loads(r.stdout)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        sys.exit(f"{path} no tiene vídeo")
    audio = any(s.get("codec_type") == "audio" for s in d.get("streams", []))
    return int(v["width"]), int(v["height"]), float(d["format"]["duration"]), audio


def run(state, cfg, ctx):
    clips = state.get(cfg.get("campo_clips") or "clips") or []
    if not isinstance(clips, list) or not clips:
        sys.exit("No hay clips que concatenar")
    norm = []
    for i, c in enumerate(clips):
        if isinstance(c, str):
            c = {"orden": i, "video": c}
        if not isinstance(c, dict) or not c.get("video"):
            sys.exit(f"Clip no válido: {c!r}")
        norm.append(c)
    norm.sort(key=lambda c: (float(c.get("orden") if c.get("orden") is not None else 1e9)))
    rutas = [c["video"] for c in norm]
    info = [_probe(r) for r in rutas]
    w, h = info[0][0] // 2 * 2, info[0][1] // 2 * 2
    cmd, filtros, n_in = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"], [], 0
    pares = []
    for r, (_, _, dur, audio) in zip(rutas, info):
        cmd += ["-i", r]
        vi = n_in
        n_in += 1
        if audio:
            ai = vi
        else:
            cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
            ai = n_in
            n_in += 1
        pares.append((vi, ai))
    for k, (vi, ai) in enumerate(pares):
        filtros.append(f"[{vi}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                       f"setsar=1,fps=25,format=yuv420p[v{k}]")
        filtros.append(f"[{ai}:a]aresample=44100,aformat=channel_layouts=stereo[a{k}]")
    filtros.append("".join(f"[v{k}][a{k}]" for k in range(len(pares))) + f"concat=n={len(pares)}:v=1:a=1[v][a]")
    out = ctx.run_dir / "concatenado.mp4"
    cmd += ["-filter_complex", ";".join(filtros), "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
            "-crf", str(int(cfg.get("crf") or 20)), "-c:a", "aac", "-movflags", "+faststart", str(out)]
    ctx.log(f"Concatenando {len(rutas)} clips ({w}x{h}): " + ", ".join(str(c.get('orden')) for c in norm))
    ctx.progress(0.1)
    code = ctx.stream(cmd, None, None)
    if code != 0 or not out.exists():
        sys.exit(f"ffmpeg terminó con código {code}")
    ctx.progress(1)
    return {cfg.get("campo_salida") or "video_final": str(out), "clips_usados": rutas}
