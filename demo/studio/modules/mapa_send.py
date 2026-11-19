"""Mapa (Send): lanza el nodo destino una vez por elemento de una lista, en paralelo (map-reduce)."""
from langgraph.types import Send

META = {
    "name": "Mapa (Send)", "category": "Flujo", "icon": "⋔", "kind": "task", "exec": "local", "gpu": False,
    "command": True,
    "desc": "Por cada elemento de la lista lanza el nodo conectado a su salida con {clave_item: elemento} y las "
            "claves extra. Para reunir resultados, el destino debe escribir en una clave con reducer append.",
    "inputs": [], "outputs": [], "inputs_from_config": ["lista", "extra"], "outputs_from_config": ["clave_item"],
    "weight": 0,
}

CONFIG = [
    {"key": "lista", "label": "Clave de la lista", "type": "state_key", "default": "items"},
    {"key": "destino", "label": "Nodo destino", "type": "text", "default": "",
     "help": "Id del nodo que se lanza por cada elemento (vacío = el único conectado a la salida)"},
    {"key": "clave_item", "label": "Clave del elemento", "type": "state_key", "default": "item"},
    {"key": "extra", "label": "Claves que se pasan también", "type": "keys", "default": ""},
]


def run(state, cfg, ctx):
    lista = state.get(cfg.get("lista") or "items") or []
    if not isinstance(lista, list):
        raise ValueError(f"'{cfg.get('lista')}' debe ser una lista (es {type(lista).__name__})")
    destino = (cfg.get("destino") or "").strip()
    if not destino:
        raise ValueError("Indica el nodo destino en la configuración")
    extra = [k.strip() for k in (cfg.get("extra") or "").split(",") if k.strip()]
    base = {k: state.get(k) for k in extra}
    clave = cfg.get("clave_item") or "item"
    ctx.log(f"{len(lista)} elementos → {destino}")
    return [Send(destino, {**base, clave: it}) for it in lista]
