"""Chat (Ollama): responde a la conversación de la clave 'messages' con ChatOllama (tokens en streaming)."""

META = {
    "name": "Chat (Ollama)", "category": "IA", "icon": "💬", "kind": "task", "exec": "local", "gpu": "shared",
    "llm": True, "desc": "Añade a 'messages' la respuesta del modelo. Los tokens llegan por el stream 'messages'.",
    "inputs": ["messages"], "outputs": ["messages"], "weight": 2,
    "defaults": {"retry": {"max_attempts": 2, "initial_interval": 2.0}, "timeout": {"run": 600, "idle": 240}},
}

CONFIG = [
    {"key": "modelo", "label": "Modelo", "type": "model", "default": "", "setting": "ollama_model"},
    {"key": "url", "label": "URL de Ollama", "type": "text", "default": "", "setting": "ollama_url"},
    {"key": "sistema", "label": "Sistema", "type": "textarea", "default": "Eres un asistente útil. Responde en español.",
     "help": "Admite {clave} del estado y del contexto del agente"},
    {"key": "temperatura", "label": "Temperatura", "type": "number", "default": 0.7},
]


async def run(state, cfg, ctx):
    from langchain_core.messages import SystemMessage
    from langchain_ollama import ChatOllama

    llm = ChatOllama(model=cfg.get("modelo") or "qwen2.5:7b", base_url=cfg.get("url") or None,
                     temperature=float(cfg.get("temperatura") or 0), keep_alive=ctx.settings.get("keep_alive", "5m"))
    sistema = ctx.fill(cfg.get("sistema") or "", {**(ctx.context or {}), **state})
    msgs = ([SystemMessage(sistema)] if sistema else []) + list(state.get("messages") or [])
    resp = None
    async for ch in llm.astream(msgs):
        resp = ch if resp is None else resp + ch
    ctx.log(f"Respuesta: {resp.content if resp else ''}")
    return {"messages": [resp]} if resp is not None else None
