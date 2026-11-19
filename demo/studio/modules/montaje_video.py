"""Montaje (FFmpeg): composición 16:9 con fondo desenfocado, rótulos y aviso."""
import subprocess
import sys

META = {
    "name": "Montaje de vídeo", "category": "Vídeo", "icon": "🎞", "desc": "Composición 16:9 con rótulos (FFmpeg).",
    "kind": "task", "exec": "proceso", "interprete": "pesado", "gpu": False,
    "inputs": ["video_lipsync", "duracion", "nombre", "subtitulo", "texto"], "outputs": ["video_final"], "weight": 8,
    "defaults": {"retry": {"max_attempts": 1}, "timeout": {"run": 900, "idle": 180}},
}

CONFIG = [
    {"key": "nombre", "label": "Rótulo", "type": "text", "default": "", "help": "Si el estado tiene 'nombre', gana el estado"},
    {"key": "subtitulo", "label": "Subtítulo", "type": "text", "default": "", "help": "Si el estado tiene 'subtitulo', gana el estado"},
    {"key": "aviso", "label": "Aviso", "type": "text", "default": "", "setting": "video.aviso"},
    {"key": "color", "label": "Color", "type": "text", "default": "", "setting": "video.color"},
    {"key": "crf", "label": "CRF", "type": "number", "default": 18},
]
FONT_SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
FONT_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def esc(t):
    return str(t).replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")


def run(state, cfg, ctx):
    if not state.get("video_lipsync"):
        sys.exit("El estado no tiene 'video_lipsync'")
    nombre = state.get("nombre") if state.get("nombre") is not None else cfg.get("nombre")
    subtitulo = state.get("subtitulo") if state.get("subtitulo") is not None else cfg.get("subtitulo")
    color = cfg.get("color") or "0xE8C766"
    out = ctx.run_dir / "final.mp4"
    f = ("[0:v]split[a][b];[a]scale=1920:1920,crop=1920:1080,boxblur=30:3,eq=brightness=-0.15[bg];"
         "[b]scale=1080:1080[fg];[bg][fg]overlay=420:0")
    if nombre:
        f += (",drawbox=x=0:y=850:w=1100:h=130:color=black@0.65:t=fill"
              f",drawbox=x=0:y=850:w=12:h=130:color={color}:t=fill"
              f",drawtext=fontfile={FONT_SERIF}:text='{esc(nombre)}':fontcolor={color}:fontsize=52:x=40:y=868")
        if subtitulo:
            f += f",drawtext=fontfile={FONT_SANS}:text='{esc(subtitulo)}':fontcolor=white:fontsize=30:x=42:y=932"
    if cfg.get("aviso"):
        f += (f",drawtext=fontfile={FONT_SANS}:text='{esc(cfg['aviso'])}':fontcolor=white@0.8:fontsize=24"
              ":x=w-tw-30:y=30:box=1:boxcolor=black@0.5:boxborderw=10")
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", state["video_lipsync"],
           "-filter_complex", f, "-c:v", "libx264", "-crf", str(int(cfg.get("crf") or 18)), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    ctx.log(f"$ ffmpeg … {out}  (rótulo: {nombre!r})")
    dur = float(state.get("duracion") or 5)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_us="):
            try:
                ctx.progress(0.99 * int(line.split("=")[1]) / 1e6 / dur)
            except ValueError:
                pass
        elif line and "=" not in line:
            ctx.log(line)
    if proc.wait() != 0:
        sys.exit("FFmpeg falló")
    (ctx.run_dir / "texto.txt").write_text((state.get("texto") or "") + "\n", encoding="utf-8")
    ctx.log(f"Vídeo final: {out}")
    ctx.progress(1)
    return {"video_final": str(out)}
