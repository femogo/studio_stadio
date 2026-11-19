"""Router LLM: elige la rama de salida preguntando a Ollama (kind router)."""
import json
import re

import httpx

META = {
    "name": "Router (LLM)", "category": "IA", "icon": "⑂", "kind": "router", "exec": "local", "gpu": "shared",
    "llm": True,
    "desc": "Clasifica el texto y sigue la arista de salida elegida. 'opciones' = {id_destino: descripción}.",
    "inputs": [], "outputs": [], "weight": 1,
    "defaults": {"retry": {"max_attempts": 2, "initial_interval": 2.0}, "timeout": {"run": 300, "idle": 120}},
}

CONFIG = [
    {"key": "modelo", "label": "Modelo", "type": "model", "default": "", "setting": "ollama_model"},
    {"key": "url", "label": "URL de Ollama", "type": "text", "default": "", "setting": "ollama_url"},
    {"key": "texto", "label": "Texto a clasificar", "type": "textarea", "default": "{texto}"},
    {"key": "opciones", "label": "Opciones", "type": "json", "default": {},
     "help": '{"id_nodo": "cuándo ir ahí", …}; los ids deben ser aristas de salida'},
    {"key": "por_defecto", "label": "Destino si no hay respuesta válida", "type": "text", "default": ""},
]


async def run(state, cfg, ctx):
    opciones = cfg.get("opciones") or {}
    if not isinstance(opciones, dict) or not opciones:
        raise ValueError("Configura 'opciones' como {id_destino: descripción}")
    texto = ctx.fill(cfg.get("texto") or "", state)
    lista = "\n".join(f'- "{k}": {v}' for k, v in opciones.items())
    prompt = (f"Clasifica el siguiente texto en UNA de estas categorías:\n{lista}\n\nTexto:\n{texto}\n\n"
              f'Responde solo con JSON: {{"destino": "<una de: {", ".join(opciones)}>"}}')
    modelo = cfg.get("modelo") or ctx.settings.get("ollama_model") or "qwen2.5:7b"
    url = (cfg.get("url") or ctx.settings.get("ollama_url") or "http://localhost:11434").rstrip("/")
    async with httpx.AsyncClient(timeout=httpx.Timeout(240, connect=10)) as c:
        r = await c.post(url + "/api/generate", json={
            "model": modelo, "prompt": prompt, "stream": False, "format": "json",
            "keep_alive": ctx.settings.get("keep_alive", "5m"), "options": {"temperature": 0, "num_predict": 40}})
        r.raise_for_status()
        out = r.json().get("response", "")
    ctx.log("Respuesta: " + out)
    destino = None
    try:
        destino = json.loads(out).get("destino")
    except (ValueError, AttributeError):
        m = re.search(r'"destino"\s*:\s*"([^"]+)"', out)
        destino = m.group(1) if m else None
    if destino not in opciones:
        destino = cfg.get("por_defecto") or None
    if not destino:
        raise ValueError(f"El LLM no eligió una opción válida: {out[:200]}")
    ctx.log(f"→ {destino}")
    return destino
