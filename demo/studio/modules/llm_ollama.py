"""LLM (Ollama): genera texto o JSON a partir de un prompt con marcadores {clave}."""
import json
import re

import httpx

META = {
    "name": "LLM (Ollama)", "category": "IA", "icon": "✦", "kind": "task", "exec": "local", "gpu": "shared",
    "llm": True, "desc": "Llama a Ollama con un prompt que admite {clave} del estado; devuelve texto o JSON.",
    "inputs": [], "outputs": [], "outputs_from_config": ["campo_salida", "claves_json"], "weight": 2,
    "defaults": {"retry": {"max_attempts": 2, "initial_interval": 2.0}, "timeout": {"run": 600, "idle": 240}},
}

CONFIG = [
    {"key": "modelo", "label": "Modelo", "type": "model", "default": "", "setting": "ollama_model"},
    {"key": "url", "label": "URL de Ollama", "type": "text", "default": "", "setting": "ollama_url"},
    {"key": "sistema", "label": "Sistema", "type": "textarea", "default": "", "help": "Prompt de sistema (opcional)"},
    {"key": "prompt", "label": "Prompt", "type": "textarea", "default": "{texto}",
     "help": "Texto con marcadores {clave} que se sustituyen por valores del estado"},
    {"key": "campo_salida", "label": "Campo de salida", "type": "state_key", "default": "texto"},
    {"key": "formato", "label": "Formato", "type": "select", "default": "texto", "options": ["texto", "json"]},
    {"key": "claves_json", "label": "Claves JSON al estado", "type": "keys", "default": "",
     "help": "Con formato json: claves del objeto que se copian al estado (vacío = objeto entero en el campo de salida)"},
    {"key": "temperatura", "label": "Temperatura", "type": "number", "default": 0.7},
    {"key": "num_predict", "label": "Máx. tokens", "type": "number", "default": 400},
    {"key": "timeout", "label": "Timeout HTTP (s)", "type": "number", "default": 300},
]


async def run(state, cfg, ctx):
    prompt = ctx.fill(cfg.get("prompt") or "", state)
    sistema = ctx.fill(cfg.get("sistema") or "", state)
    modelo = cfg.get("modelo") or ctx.settings.get("ollama_model") or "qwen2.5:7b"
    url = (cfg.get("url") or ctx.settings.get("ollama_url") or "http://localhost:11434").rstrip("/")
    ctx.log(f"Modelo {modelo}\nPrompt:\n{prompt}")
    body = {"model": modelo, "prompt": prompt, "stream": True,
            "keep_alive": ctx.settings.get("keep_alive", "5m"),
            "options": {"temperature": float(cfg.get("temperatura") or 0), "num_predict": int(cfg.get("num_predict") or 400)}}
    if sistema:
        body["system"] = sistema
    json_mode = cfg.get("formato") == "json"
    if json_mode:
        body["format"] = "json"
    ctx.progress(0.02)
    out, n = "", 0
    objetivo = max(20, int(cfg.get("num_predict") or 400))
    async with httpx.AsyncClient(timeout=httpx.Timeout(float(cfg.get("timeout") or 300), connect=10)) as c:
        async with c.stream("POST", url + "/api/generate", json=body) as r:
            if r.status_code >= 400:
                txt = (await r.aread()).decode("utf-8", "replace")
                raise RuntimeError(f"Ollama respondió {r.status_code}: {txt[:300]}")
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                j = json.loads(line)
                if j.get("error"):
                    raise RuntimeError(f"Ollama: {j['error']}")
                tok = j.get("response", "")
                if tok:
                    out += tok
                    n += 1
                    ctx.token(tok)
                    ctx.progress(0.05 + 0.9 * min(1.0, n / objetivo))
                    if n % 8 == 0:
                        ctx.update({cfg.get("campo_salida") or "texto": out})
                if j.get("done"):
                    break
    ctx.log("Respuesta:\n" + out)
    campo = cfg.get("campo_salida") or "texto"
    if not json_mode:
        texto = out.strip().strip('"«»“”').strip()
        return {campo: texto}
    try:
        obj = json.loads(out)
    except ValueError:
        m = re.search(r"\{.*\}", out, re.S)
        if not m:
            raise ValueError("La respuesta no es JSON: " + out[:200])
        obj = json.loads(m.group(0))
    claves = [k.strip() for k in (cfg.get("claves_json") or "").split(",") if k.strip()]
    if claves:
        return {k: obj.get(k) for k in claves}
    return {campo: obj}
