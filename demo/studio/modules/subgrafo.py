"""Subflujo: ejecuta otro flujo (proyecto) como un nodo (kind subgraph). Lo compila el estudio; run() no se llama."""

META = {
    "name": "Subflujo", "category": "Flujo", "icon": "⧉", "kind": "subgraph", "exec": "local", "gpu": False,
    "desc": "Ejecuta otro flujo con las claves que comparte con este. Devuelve las claves output: true del "
            "subflujo (o todas las compartidas). Las claves append/sum del hijo son internas.",
    "inputs": [], "outputs": [], "weight": 3,
}

CONFIG = [
    {"key": "proyecto", "label": "Flujo", "type": "text", "default": "", "help": "slug del subflujo"},
]


def run(state, cfg, ctx):
    raise RuntimeError("El nodo subgrafo lo compila el estudio; run() no se usa")
