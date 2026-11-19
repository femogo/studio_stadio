"""Plantilla de texto: compone un texto con marcadores {clave} del estado (y del contexto del agente)."""

META = {
    "name": "Plantilla de texto", "category": "Texto", "icon": "✎", "kind": "task", "exec": "local", "gpu": False,
    "desc": "Sustituye {clave} por su valor; las listas de dicts se escriben una por línea.",
    "inputs": [], "outputs": [], "outputs_from_config": ["campo_salida"], "weight": 0,
}

CONFIG = [
    {"key": "plantilla", "label": "Plantilla", "type": "textarea", "default": "{texto}"},
    {"key": "campo_salida", "label": "Campo de salida", "type": "state_key", "default": "texto"},
]


def _plano(v):
    if isinstance(v, list):
        return "\n".join(" · ".join(str(x) for x in it.values() if x) if isinstance(it, dict) else str(it) for it in v)
    if isinstance(v, dict):
        return "\n".join(f"{k}: {x}" for k, x in v.items())
    return v


def run(state, cfg, ctx):
    vals = {k: _plano(v) for k, v in {**(ctx.context or {}), **state}.items()}
    return {cfg.get("campo_salida") or "texto": ctx.fill(cfg.get("plantilla") or "", vals)}
