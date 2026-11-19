"""Herramientas: memoria a largo plazo del usuario (Store del estudio). El usuario sale del contexto del agente."""
import uuid

from langchain_core.tools import tool
from langgraph.config import get_store
from langgraph.runtime import get_runtime


def _ns():
    try:
        ctx = get_runtime().context or {}
    except Exception:
        ctx = {}
    return ("memorias", str((ctx.get("usuario") if isinstance(ctx, dict) else None) or "anonimo"))


@tool
async def recordar(dato: str) -> str:
    """Guarda en la memoria a largo plazo un dato sobre el usuario (su nombre, gustos, etc.)."""
    await get_store().aput(_ns(), uuid.uuid4().hex[:12], {"texto": dato})
    return f"Guardado: {dato}"


@tool
async def consultar_memoria(pregunta: str) -> str:
    """Busca en la memoria a largo plazo datos guardados sobre el usuario. Úsala antes de decir que no sabes algo."""
    res = await get_store().asearch(_ns(), query=pregunta, limit=3)
    if not res:
        return "No hay nada guardado."
    return "\n".join(f"- {r.value.get('texto')}" for r in res)
