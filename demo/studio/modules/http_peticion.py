"""Petición HTTP: GET/POST/PUT/DELETE a una URL con marcadores {clave}. El timeout es obligatorio."""
import json

import httpx

META = {
    "name": "Petición HTTP", "category": "Datos", "icon": "⇄", "kind": "task", "exec": "local", "gpu": False,
    "desc": "Llama a una URL y guarda la respuesta (texto o JSON) en el estado.",
    "inputs": [], "outputs": [], "outputs_from_config": ["campo_salida"], "weight": 1,
    "defaults": {"retry": {"max_attempts": 2, "initial_interval": 1.0}},
}

CONFIG = [
    {"key": "metodo", "label": "Método", "type": "select", "default": "GET", "options": ["GET", "POST", "PUT", "DELETE"]},
    {"key": "url", "label": "URL", "type": "text", "default": "", "required": True},
    {"key": "cabeceras", "label": "Cabeceras", "type": "json", "default": {}},
    {"key": "cuerpo", "label": "Cuerpo", "type": "textarea", "default": "",
     "help": "Texto con {clave}; si es JSON válido se envía como JSON"},
    {"key": "timeout", "label": "Timeout (s)", "type": "number", "default": 30, "required": True},
    {"key": "formato", "label": "Respuesta", "type": "select", "default": "json", "options": ["json", "texto"]},
    {"key": "campo_salida", "label": "Campo de salida", "type": "state_key", "default": "respuesta"},
]


async def run(state, cfg, ctx):
    t = float(cfg.get("timeout") or 0)
    if t <= 0:
        raise ValueError("El timeout es obligatorio y debe ser > 0")
    vals = {**(ctx.context or {}), **state}
    url = ctx.fill(cfg.get("url") or "", vals)
    cuerpo = ctx.fill(cfg.get("cuerpo") or "", vals)
    kw = {}
    if cuerpo:
        try:
            kw["json"] = json.loads(cuerpo)
        except ValueError:
            kw["content"] = cuerpo.encode()
    metodo = cfg.get("metodo") or "GET"
    ctx.log(f"{metodo} {url}")
    async with httpx.AsyncClient(timeout=t, follow_redirects=True) as c:
        r = await c.request(metodo, url, headers=cfg.get("cabeceras") or {}, **kw)
    ctx.log(f"→ {r.status_code} ({len(r.content)} bytes)")
    r.raise_for_status()
    campo = cfg.get("campo_salida") or "respuesta"
    if cfg.get("formato") == "texto":
        return {campo: r.text}
    try:
        return {campo: r.json()}
    except ValueError:
        raise ValueError(f"La respuesta no es JSON: {r.text[:200]}")
