"""MCP: llama a una herramienta de un servidor MCP configurado en Ajustes → MCP (mcp_servidores)."""
import json

META = {
    "name": "Herramienta MCP", "category": "Integración", "icon": "🔌", "kind": "task", "exec": "local", "gpu": False,
    "desc": "Llama a una herramienta de un servidor MCP. Los argumentos (JSON) admiten {clave} del estado.",
    "inputs": [], "outputs": [], "outputs_from_config": ["campo_salida"], "weight": 1,
    "defaults": {"retry": {"max_attempts": 2, "initial_interval": 1.0}, "timeout": {"run": 180, "idle": None}},
}

CONFIG = [
    {"key": "servidor", "label": "Servidor", "type": "text", "default": "", "required": True,
     "help": "Nombre del servidor en Ajustes → MCP"},
    {"key": "herramienta", "label": "Herramienta", "type": "text", "default": "", "required": True},
    {"key": "argumentos", "label": "Argumentos", "type": "json", "default": {},
     "help": 'Objeto JSON; las cadenas admiten {clave} del estado, p. ej. {"texto": "{texto}"}'},
    {"key": "campo_salida", "label": "Campo de salida", "type": "state_key", "default": "resultado_mcp"},
]


def _rellenar(v, state, ctx):
    if isinstance(v, str):
        import re
        m = re.fullmatch(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", v)
        if m:                                   # "{clave}" sola conserva el tipo del valor del estado
            return state.get(m.group(1))
        return ctx.fill(v, state)
    if isinstance(v, dict):
        return {k: _rellenar(x, state, ctx) for k, x in v.items()}
    if isinstance(v, list):
        return [_rellenar(x, state, ctx) for x in v]
    return v


async def run(state, cfg, ctx):
    from mcp_cliente import McpError, llamar
    args = cfg.get("argumentos") or {}
    if isinstance(args, str):
        args = json.loads(args) if args.strip() else {}
    if not isinstance(args, dict):
        raise ValueError("argumentos debe ser un objeto JSON")
    args = _rellenar(args, state, ctx)
    srv, herr = cfg.get("servidor"), cfg.get("herramienta")
    ctx.log(f"MCP {srv}.{herr}({json.dumps(args, ensure_ascii=False)[:500]})")
    try:
        r = await llamar(ctx.settings.get("mcp_servidores") or {}, srv, herr, args)
    except KeyError as e:
        raise ValueError(f"No existe {e} en los servidores MCP configurados") from None
    except McpError as e:
        raise ConnectionError(str(e)) from None          # reintentable (RetryPolicy incluye ConnectionError)
    ctx.log("→ " + json.dumps(r, ensure_ascii=False, default=str)[:500])
    return {cfg.get("campo_salida") or "resultado_mcp": r}
