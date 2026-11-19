"""Revisión humana: detiene el grafo hasta que una persona aprueba, edita o rechaza un valor."""

META = {
    "name": "Revisión humana", "category": "Flujo", "icon": "✋", "kind": "interrupt", "exec": "local", "gpu": False,
    "desc": "Pide aprobación. Aprobado → sigue con el valor (editable). Rechazado → guarda el comentario y suma un rechazo.",
    "inputs": [], "outputs": ["aprobado", "rechazos"], "inputs_from_config": ["campo"],
    "outputs_from_config": ["campo", "campo_comentario"], "weight": 0,
    "defaults": {"timeout": {"run": None, "idle": None}},
}

CONFIG = [
    {"key": "campo", "label": "Campo a revisar", "type": "state_key", "default": "texto"},
    {"key": "mensaje", "label": "Mensaje", "type": "text", "default": "Revisa el guion"},
    {"key": "campo_comentario", "label": "Campo del comentario", "type": "state_key", "default": "comentario_revision"},
]

SCHEMA = {"type": "object", "properties": {"aprobado": {"type": "boolean", "title": "Aprobar"},
                                           "valor": {"type": "string", "title": "Texto (puedes editarlo)"},
                                           "comentario": {"type": "string", "title": "Comentario si rechazas"}},
          "required": ["aprobado"]}


def run(state, cfg, ctx):
    campo = cfg.get("campo") or "texto"
    # interrupt es la PRIMERA operación: al reanudar, el nodo se ejecuta desde el principio
    r = ctx.interrupt({"campo": campo, "valor": state.get(campo), "mensaje": cfg.get("mensaje") or ""}, SCHEMA) or {}
    if r.get("aprobado"):
        valor = r.get("valor")
        return {campo: valor if valor not in (None, "") else state.get(campo), "aprobado": True}
    return {"aprobado": False, cfg.get("campo_comentario") or "comentario_revision": r.get("comentario") or "",
            "rechazos": int(state.get("rechazos") or 0) + 1}
