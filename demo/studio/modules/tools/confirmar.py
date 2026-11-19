"""Herramienta: pide confirmación humana (interrupt) antes de una acción."""
from langchain_core.tools import tool
from langgraph.types import interrupt

SCHEMA = {"type": "object", "properties": {"aprobado": {"type": "boolean"}, "comentario": {"type": "string"}},
          "required": ["aprobado"]}


@tool
def pedir_confirmacion(accion: str) -> str:
    """Pide al usuario que confirme una acción importante antes de hacerla. Devuelve si la aprobó."""
    r = interrupt({"mensaje": f"¿Confirmas esta acción? {accion}", "accion": accion}, response_schema=SCHEMA)
    if isinstance(r, dict) and r.get("aprobado"):
        return "El usuario lo ha aprobado." + (f" Comentario: {r['comentario']}" if r.get("comentario") else "")
    return "El usuario NO lo ha aprobado." + (f" Comentario: {r.get('comentario')}" if isinstance(r, dict) and r.get("comentario") else "")
