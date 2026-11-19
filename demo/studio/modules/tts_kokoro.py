"""Voz (Kokoro): texto a voz en español."""
import sys

META = {
    "name": "Voz (Kokoro)", "category": "Audio", "icon": "🎙", "desc": "Texto a voz en español.",
    "kind": "task", "exec": "proceso", "interprete": "pesado", "gpu": "shared",
    "inputs": [], "inputs_from_config": ["campo_texto", "campo_voz"], "outputs": ["audio", "duracion", "voz_usada"], "weight": 5,
    "defaults": {"retry": {"max_attempts": 1}, "timeout": {"run": 900, "idle": 300}},
}

CONFIG = [
    {"key": "campo_texto", "label": "Campo de texto", "type": "state_key", "default": "texto"},
    {"key": "voz", "label": "Voz", "type": "select", "default": "", "options": ["ef_dora", "em_alex", "em_santa"],
     "setting": "video.voz", "help": "ef_dora = mujer · em_alex / em_santa = hombre"},
    {"key": "velocidad", "label": "Velocidad", "type": "number", "default": 0.95},
    {"key": "campo_voz", "label": "Campo con la voz (opcional)", "type": "state_key", "default": "",
     "help": "Si el estado trae una voz en esta clave, se usa en vez de la de arriba (p. ej. una por personaje)"},
]


def run(state, cfg, ctx):
    texto = (state.get(cfg.get("campo_texto") or "texto") or "").strip()
    if cfg.get("campo_voz") and state.get(cfg["campo_voz"]):
        cfg["voz"] = state[cfg["campo_voz"]]
    if not texto:
        sys.exit(f"El estado no tiene '{cfg.get('campo_texto') or 'texto'}'")
    ctx.progress(0.05)
    ctx.log("Cargando Kokoro…")
    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline
    pipe = KPipeline(lang_code="e", repo_id="hexgrad/Kokoro-82M")
    ctx.progress(0.5)
    trozos = []
    for g, _, a in pipe(texto, voice=cfg["voz"], speed=float(cfg["velocidad"])):
        ctx.log("Sintetizado:", g)
        trozos.append(a)
    ctx.progress(0.9)
    audio = np.concatenate(trozos)
    out = ctx.run_dir / "voz.wav"
    sf.write(out, audio, 24000)
    dur = round(len(audio) / 24000, 2)
    ctx.log(f"Audio: {out} ({dur} s)")
    ctx.progress(1)
    return {"audio": str(out), "duracion": dur, "voz_usada": cfg["voz"]}
