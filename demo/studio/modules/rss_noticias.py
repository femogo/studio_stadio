"""Noticias RSS/Atom: lee un feed y devuelve las últimas entradas (título, enlace, resumen, fecha)."""
import html
import re
import xml.etree.ElementTree as ET

import httpx

META = {
    "name": "Noticias RSS", "category": "Datos", "icon": "📰", "kind": "task", "exec": "local", "gpu": False,
    "desc": "Descarga un feed RSS 2.0 o Atom. Devuelve una lista de noticias y un texto con una por línea.",
    "inputs": [], "outputs": [], "outputs_from_config": ["campo_salida", "campo_texto"], "weight": 1,
    "defaults": {"retry": {"max_attempts": 2, "initial_interval": 2.0}},
}

CONFIG = [
    {"key": "url", "label": "URL del feed", "type": "text", "default": "", "required": True},
    {"key": "max", "label": "Máx. noticias", "type": "number", "default": 5},
    {"key": "timeout", "label": "Timeout (s)", "type": "number", "default": 20, "required": True},
    {"key": "campo_salida", "label": "Campo (lista)", "type": "state_key", "default": "noticias"},
    {"key": "campo_texto", "label": "Campo (texto)", "type": "state_key", "default": "noticias_texto"},
]

ATOM = "{http://www.w3.org/2005/Atom}"


def _limpio(t):
    t = re.sub(r"<[^>]+>", " ", html.unescape(t or ""))
    return re.sub(r"\s+", " ", t).strip()


def parse(xml_text, n):
    root = ET.fromstring(xml_text)
    out = []
    for it in root.iter("item"):
        out.append({"titulo": _limpio(it.findtext("title")), "enlace": (it.findtext("link") or "").strip(),
                    "resumen": _limpio(it.findtext("description"))[:500], "fecha": (it.findtext("pubDate") or "").strip()})
    for it in root.iter(ATOM + "entry"):
        link = it.find(ATOM + "link")
        out.append({"titulo": _limpio(it.findtext(ATOM + "title")),
                    "enlace": link.get("href", "") if link is not None else "",
                    "resumen": _limpio(it.findtext(ATOM + "summary") or it.findtext(ATOM + "content"))[:500],
                    "fecha": (it.findtext(ATOM + "updated") or "").strip()})
    return out[:n]


async def run(state, cfg, ctx):
    url = ctx.fill(cfg.get("url") or "", {**(ctx.context or {}), **state})
    ctx.log(f"Leyendo {url}")
    async with httpx.AsyncClient(timeout=float(cfg.get("timeout") or 20), follow_redirects=True) as c:
        r = await c.get(url, headers={"User-Agent": "estudio-agentes/2"})
    r.raise_for_status()
    noticias = parse(r.text, int(cfg.get("max") or 5))
    if not noticias:
        raise ValueError("El feed no tiene entradas")
    texto = "\n".join(f"- {x['titulo']}: {x['resumen']}" for x in noticias)
    ctx.log(texto)
    return {cfg.get("campo_salida") or "noticias": noticias, cfg.get("campo_texto") or "noticias_texto": texto}
