"""Agente con herramientas (kind agent; antes «Agente ReAct»): LLM con herramientas en bucle (ToolNode + tools_condition). Lo compila el estudio."""

META = {
    "name": "Agente con herramientas", "category": "IA", "icon": "🤖", "kind": "agent", "exec": "local", "gpu": False,
    "llm": True,
    "desc": "Modelo de Ollama con herramientas de modules/tools/. Lee y amplía 'messages'. No usa create_react_agent "
            "(deprecado): el estudio compila LLM ↔ herramientas como subgrafo.",
    "inputs": ["messages"], "outputs": ["messages"], "weight": 3,
    "defaults": {"timeout": {"run": 900, "idle": None}},
}

CONFIG = [
    {"key": "modelo", "label": "Modelo", "type": "model", "default": "", "setting": "ollama_model"},
    {"key": "url", "label": "URL de Ollama", "type": "text", "default": "", "setting": "ollama_url"},
    {"key": "sistema", "label": "Sistema", "type": "textarea",
     "default": "Eres un asistente útil. Responde en español. Usa las herramientas cuando hagan falta.",
     "help": "Admite {clave} del contexto del agente"},
    {"key": "herramientas", "label": "Herramientas", "type": "keys", "default": "",
     "help": "Nombres separados por comas (vacío = todas las de modules/tools)"},
    {"key": "temperatura", "label": "Temperatura", "type": "number", "default": 0.2},
    {"key": "mcp", "label": "Servidores MCP", "type": "keys", "default": "",
     "help": "Nombres de Ajustes → MCP cuyas herramientas se añaden (se llaman <servidor>__<herramienta>)"},
    {"key": "flujos", "label": "Flujos como herramientas", "type": "keys", "default": "",
     "help": "Slugs de flujos separados por comas; cada uno es la herramienta flujo__<slug> (argumentos = claves "
             "input: true del flujo; devuelve las output: true)"},
]


def run(state, cfg, ctx):
    raise RuntimeError("El agente lo compila el estudio; run() no se usa")
