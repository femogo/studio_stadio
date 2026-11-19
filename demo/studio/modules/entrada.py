"""Entrada: introduce valores iniciales en el estado (los de la ejecución tienen prioridad)."""

META = {
    "name": "Entrada", "category": "Flujo", "icon": "⇥", "kind": "input", "exec": "local", "gpu": False,
    "desc": "Valores iniciales del estado. Si la entrada de la ejecución ya trae una clave, gana la de la ejecución.",
    "inputs": [], "outputs": [], "outputs_from_config": ["valores"], "weight": 0,
}

CONFIG = [
    {"key": "valores", "label": "Valores", "type": "json", "default": {},
     "help": "Objeto JSON {clave: valor}; cada clave se convierte en un canal del estado"},
]


def run(state, cfg, ctx):
    valores = cfg.get("valores") or {}
    if not isinstance(valores, dict):
        raise ValueError("'valores' debe ser un objeto JSON")
    return {k: v for k, v in valores.items() if k not in state}
