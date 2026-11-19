"""[F3] Cliente MCP (langchain-mcp-adapters) para el estudio (DISENO.md §6.4). Sin sesiones persistentes: cada
operación abre y cierra la suya. Sin FastAPI (lo usan también los módulos y la exportación)."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re

NOMBRE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
SEP = "__"


class McpError(RuntimeError):
    pass


def validar_servidores(d) -> dict:
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise ValueError("mcp_servidores debe ser un objeto {nombre: configuración}")
    for n, c in d.items():
        if not NOMBRE_RE.match(n) or SEP in n:
            raise ValueError(f"nombre de servidor MCP no válido: {n!r} (letras, números, - y _; sin '__')")
        if not isinstance(c, dict):
            raise ValueError(f"servidor MCP {n}: la configuración debe ser un objeto")
        t = c.get("transport")
        if t == "stdio":
            if not isinstance(c.get("command"), str) or not c["command"]:
                raise ValueError(f"servidor MCP {n}: falta command")
            if not isinstance(c.get("args", []), list) or not all(isinstance(a, str) for a in c.get("args", [])):
                raise ValueError(f"servidor MCP {n}: args debe ser una lista de cadenas")
            if c.get("env") is not None and not isinstance(c["env"], dict):
                raise ValueError(f"servidor MCP {n}: env debe ser un objeto")
        elif t == "streamable_http":
            if not isinstance(c.get("url"), str) or not c["url"].startswith(("http://", "https://")):
                raise ValueError(f"servidor MCP {n}: falta url (http/https)")
        else:
            raise ValueError(f"servidor MCP {n}: transport debe ser stdio o streamable_http")
    return d


def _conexion(c: dict) -> dict:
    if c["transport"] == "stdio":
        out = {"transport": "stdio", "command": c["command"], "args": list(c.get("args") or [])}
        if c.get("env"):
            import os
            out["env"] = {**os.environ, **{k: str(v) for k, v in c["env"].items()}}
        return out
    return {"transport": "streamable_http", "url": c["url"]}


def _cliente(servidores: dict, nombre: str):
    from langchain_mcp_adapters.client import MultiServerMCPClient
    if nombre not in (servidores or {}):
        raise KeyError(nombre)
    return MultiServerMCPClient({nombre: _conexion(servidores[nombre])})


def _msg(e: BaseException) -> str:
    if isinstance(e, BaseExceptionGroup):
        return "; ".join(_msg(x) for x in e.exceptions)
    return f"{type(e).__name__}: {e}"


async def listar_uno(servidores: dict, nombre: str, timeout=10) -> dict:
    c = servidores[nombre]
    out = {"nombre": nombre, "transport": c.get("transport"), "ok": False, "error": None, "tools": []}
    try:
        tools = await asyncio.wait_for(_cliente(servidores, nombre).get_tools(server_name=nombre), timeout)
        out["tools"] = [{"name": t.name, "desc": t.description or "",
                         "args_schema": t.args_schema if isinstance(t.args_schema, dict) else
                         t.args_schema.model_json_schema()} for t in tools]
        out["ok"] = True
    except asyncio.TimeoutError:
        out["error"] = f"sin respuesta en {timeout} s"
    except Exception as e:  # noqa: BLE001  (incluye ExceptionGroup)
        out["error"] = _msg(e)
    return out


async def listar(servidores: dict, timeout=10) -> list[dict]:
    return list(await asyncio.gather(*[listar_uno(servidores, n, timeout) for n in sorted(servidores or {})]))


def resultado(r) -> object:
    """CallToolResult → valor: structuredContent['result'] si es la única clave, si no structuredContent, si no el
    texto (JSON si se puede)."""
    sc = getattr(r, "structuredContent", None)
    if isinstance(sc, dict) and sc:
        return sc["result"] if set(sc) == {"result"} else sc
    textos = [getattr(b, "text", None) for b in (getattr(r, "content", None) or [])]
    texto = "\n".join(t for t in textos if t is not None)
    try:
        return json.loads(texto)
    except ValueError:
        return texto


async def llamar(servidores: dict, nombre: str, herramienta: str, args: dict, timeout=120):
    """→ valor. KeyError si el servidor o la herramienta no existen; McpError si falla."""
    cli = _cliente(servidores, nombre)

    async def _go():
        # Nada se lanza dentro de la sesión (anyio lo envolvería en ExceptionGroup): se devuelve (tipo, valor)
        async with cli.session(nombre) as s:
            lt = await s.list_tools()
            if herramienta not in {t.name for t in lt.tools}:
                return "falta", None
            r = await s.call_tool(herramienta, args or {})
            return ("error" if getattr(r, "isError", False) else "ok"), resultado(r)
    try:
        tipo, valor = await asyncio.wait_for(_go(), timeout)
    except asyncio.TimeoutError as e:
        raise McpError(f"el servidor MCP {nombre} no respondió en {timeout} s") from e
    except Exception as e:  # noqa: BLE001  (incluye ExceptionGroup)
        raise McpError(f"servidor MCP {nombre}: {_msg(e)}") from None
    if tipo == "falta":
        raise KeyError(f"{nombre}{SEP}{herramienta}")
    if tipo == "error":
        raise McpError(f"la herramienta {herramienta} devolvió error: {valor}")
    return valor


def herramientas_sync(servidores: dict, nombres: list[str], timeout=20) -> list:
    """Herramientas LangChain de los servidores indicados, renombradas `<servidor>__<herramienta>`. Se puede llamar
    desde dentro de un bucle asyncio (corre en un hilo con su propio bucle)."""
    async def _todas():
        out = []
        for n in nombres:
            if n not in (servidores or {}):
                raise McpError(f"no existe el servidor MCP {n!r} en mcp_servidores")
            try:
                ts = await asyncio.wait_for(_cliente(servidores, n).get_tools(server_name=n), timeout)
            except Exception as e:  # noqa: BLE001
                raise McpError(f"servidor MCP {n}: {_msg(e)}") from None
            for t in ts:
                out.append(t.model_copy(update={"name": f"{n}{SEP}{t.name}"}))
        return out
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, _todas()).result()
