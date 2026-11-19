"""Lip-sync (SadTalker): anima el retrato con el audio."""
import re
import shutil
import sys

META = {
    "name": "Lip-sync (SadTalker)", "category": "Vídeo", "icon": "👄", "desc": "Anima el retrato con el audio.",
    "kind": "task", "exec": "proceso", "interprete": "pesado", "gpu": "exclusive",
    "inputs": ["audio", "imagen"], "outputs": ["video_lipsync"], "weight": 60,
    "defaults": {"retry": {"max_attempts": 1}, "timeout": {"run": 3600, "idle": 600}},
}

CONFIG = [
    {"key": "tam", "label": "Tamaño", "type": "select", "default": 512, "options": [256, 512]},
    {"key": "mejora", "label": "Mejora de cara (GFPGAN)", "type": "checkbox", "default": True},
    {"key": "quieto", "label": "Cabeza quieta", "type": "checkbox", "default": True},
    {"key": "expresion", "label": "Escala de expresión", "type": "number", "default": 1.1},
]

FASES = [("landmark Det", 3), ("3DMM Extraction", 5), ("mel", 1), ("audio2exp", 2),
         ("Face Renderer", 40), ("seamlessClone", 15), ("Face Enhancer", 34)]


def run(state, cfg, ctx):
    for k in ("audio", "imagen"):
        if not state.get(k):
            sys.exit(f"El estado no tiene '{k}': ejecuta antes los nodos previos")
    res = ctx.run_dir / "sadtalker"
    shutil.rmtree(res, ignore_errors=True)
    cmd = [sys.executable, "-u", "inference.py", "--driven_audio", state["audio"], "--source_image", state["imagen"],
           "--result_dir", str(res), "--preprocess", "full", "--size", str(int(cfg["tam"])),
           "--expression_scale", str(cfg["expresion"])]
    if cfg.get("quieto"):
        cmd.append("--still")
    fases = [f for f in FASES if cfg.get("mejora") or f[0] != "Face Enhancer"]
    if cfg.get("mejora"):
        cmd += ["--enhancer", "gfpgan"]
    total = sum(w for _, w in fases)
    frac = {k: 0.0 for k, _ in fases}

    def on_line(line):
        m = re.match(r"^(.*?):+\s*(\d+)%", line)
        if not m:
            return
        for idx, (k, _) in enumerate(fases):
            if m.group(1).startswith(k):
                for prev, _ in fases[:idx]:
                    frac[prev] = 1.0
                frac[k] = int(m.group(2)) / 100
                ctx.progress(0.99 * sum(frac[n] * w for n, w in fases) / total)

    rc = ctx.stream(cmd, cwd=ctx.base / "SadTalker", on_line=on_line)
    if rc != 0:
        sys.exit(f"SadTalker terminó con código {rc}")
    videos = sorted(res.glob("*.mp4"))
    if not videos:
        sys.exit("SadTalker no produjo vídeo")
    out = ctx.run_dir / "lipsync.mp4"
    shutil.copy(videos[-1], out)
    ctx.log(f"Vídeo lip-sync: {out}")
    ctx.progress(1)
    return {"video_lipsync": str(out)}
