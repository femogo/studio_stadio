"""Salida: marca qué claves del estado son el resultado (vídeo, audio, imagen o texto)."""

META = {
    "name": "Salida", "category": "Flujo", "icon": "⇤", "kind": "output", "exec": "local", "gpu": False,
    "desc": "Registra las claves indicadas como resultado del hilo (galería de vídeos, pestaña Salida).",
    "inputs": [], "outputs": [], "inputs_from_config": ["mostrar"], "weight": 0,
}

CONFIG = [
    {"key": "mostrar", "label": "Mostrar", "type": "json", "default": [{"clave": "texto", "como": "texto"}],
     "help": 'Lista de {"clave": …, "como": "texto|video|audio|imagen"}'},
]


def run(state, cfg, ctx):
    for m in cfg.get("mostrar") or []:
        v = state.get(m.get("clave"))
        ctx.log(f"{m.get('clave')} ({m.get('como', 'texto')}): {str(v)[:200] if v is not None else '—'}")
    return None
