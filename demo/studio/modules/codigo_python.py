"""Código Python: ejecuta un fragmento de código como cuerpo de una función (state, ctx) en un subproceso."""

META = {
    "name": "Código Python", "category": "Utilidades", "icon": "λ", "kind": "task", "exec": "proceso",
    "interprete": "estudio", "gpu": False,
    "desc": "Cuerpo de una función def _f(state, ctx): que devuelve un dict con las salidas.",
    "inputs": [], "outputs": [], "inputs_from_config": ["entradas"], "outputs_from_config": ["salidas"],
    "weight": 1, "defaults": {"timeout": {"run": 120, "idle": None}},
}

CONFIG = [
    {"key": "codigo", "label": "Código", "type": "code",
     "default": 'return {"resultado": len(state.get("texto") or "")}', "help": "Cuerpo de def _f(state, ctx):"},
    {"key": "entradas", "label": "Entradas", "type": "keys", "default": "texto",
     "help": "Claves del estado que recibe (separadas por comas)"},
    {"key": "salidas", "label": "Salidas", "type": "keys", "default": "resultado",
     "help": "Claves que puede devolver (separadas por comas)"},
]


def run(state, cfg, ctx):
    cuerpo = cfg.get("codigo") or "return None"
    src = "def _f(state, ctx):\n" + "\n".join("    " + l for l in cuerpo.splitlines())
    ns = {}
    exec(compile(src, "<codigo>", "exec"), ns)
    return ns["_f"](state, ctx)
