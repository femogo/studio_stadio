"""[F3] Exportar un proyecto a Python: zip con graph.py, funcional.py y langgraph.json (DISENO.md §4, §6.4)."""
from __future__ import annotations

import importlib.metadata as md
import io
import json
import sys
import zipfile
from pathlib import Path

from funcional_rt import motivos_no_funcional

STUDIO = Path(__file__).resolve().parent
RUNTIME = ("compiler.py", "modules.py", "runner.py", "herramientas.py", "funcional_rt.py", "mcp_cliente.py")
PAQUETES = ("langgraph", "langgraph-checkpoint", "langgraph-checkpoint-sqlite", "langchain-core", "httpx",
            "langchain-ollama", "langchain-mcp-adapters")

GRAPH_PY = '''"""Grafo «{name}» exportado desde el Estudio de agentes (StateGraph). Ver LEEME.md."""
import json
import os
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
os.environ.setdefault("STUDIO_HOME", str(AQUI))
sys.path.insert(0, str(AQUI / "studio_runtime"))

from compiler import Env, build  # noqa: E402

SLUG = {slug!r}
SETTINGS = json.loads((AQUI / "settings.json").read_text(encoding="utf-8"))


def cargar_proyecto(slug):
    return json.loads((AQUI / "projects" / f"{{slug}}.json").read_text(encoding="utf-8"))


PROYECTO = cargar_proyecto(SLUG)
ASISTENTE = {{"context": {{f["key"]: f.get("default") for f in PROYECTO.get("context_schema") or []}},
             "node_overrides": {{}}}}
env = Env.default(AQUI, SETTINGS)
env.load_project = cargar_proyecto

# Sin checkpointer: lo pone quien lo sirve (langgraph dev / Platform) o el que lo use:
#   app = graph.builder.compile(checkpointer=InMemorySaver())
graph = build(PROYECTO, env=env, assistant=ASISTENTE).graph
'''

FUNCIONAL_PY = '''"""Grafo «{name}» con la API funcional de LangGraph (@entrypoint/@task). Ver LEEME.md."""
from graph import ASISTENTE, PROYECTO, env  # noqa: F401  (prepara sys.path y STUDIO_HOME)

from funcional_rt import build_funcional

# Sin checkpointer: para interrupts, usar build_funcional(PROYECTO, env=env, assistant=ASISTENTE, checkpointer=…)
graph = build_funcional(PROYECTO, env=env, assistant=ASISTENTE)
'''


def subproyectos(project: dict, registry, load_project, _vistos=None) -> dict:
    """{slug: proyecto} de los subgrafos y de los flujos usados como herramientas [D9], recursivamente."""
    from modules import keys_from_value
    vistos = _vistos if _vistos is not None else {}
    for n in (project.get("nodes") or {}).values():
        info = registry.get(n.get("module") or "")
        kind = (info.meta or {}).get("kind") if info else None
        cfg = n.get("config") or {}
        slugs = [cfg.get("proyecto")] if kind == "subgraph" else keys_from_value(cfg.get("flujos")) if kind == "agent" else []
        for s in slugs:
            if s and s not in vistos:
                vistos[s] = load_project(s)
                subproyectos(vistos[s], registry, load_project, vistos)
    return vistos


def _usa_agente(proyectos, registry) -> bool:
    return any((registry.get(n.get("module") or "") or type("x", (), {"meta": {}})).meta.get("kind") == "agent"
               for p in proyectos for n in (p.get("nodes") or {}).values())


def funcional_py(project: dict) -> str:
    return FUNCIONAL_PY.format(name=project.get("name") or project["slug"])


def _requirements() -> str:
    out = [f"# Python {sys.version.split()[0]} (intérprete «estudio»)"]
    for p in PAQUETES:
        try:
            out.append(f"{p}=={md.version(p)}")
        except md.PackageNotFoundError:
            pass
    return "\n".join(out) + "\n"


def _leeme(project, motivos, modulos, registry) -> str:
    slug = project["slug"]
    pesados = [f for f in modulos if (registry.get(f).meta or {}).get("interprete") == "pesado"
               and (registry.get(f).meta or {}).get("exec") == "proceso"]
    gpu = [f for f in modulos if (registry.get(f).meta or {}).get("gpu")]
    t = [f"# {project.get('name') or slug}", "",
         "Exportado desde el Estudio de agentes. Contiene el compilador del estudio (`studio_runtime/`), los módulos",
         "usados (`modules/`) y el proyecto (`projects/`).", "",
         "## Ejecutar", "",
         "```python", "import asyncio", "from langgraph.checkpoint.memory import InMemorySaver",
         "import graph as G", "app = G.graph.builder.compile(checkpointer=InMemorySaver())",
         "print(asyncio.run(app.ainvoke({...}, {\"configurable\": {\"thread_id\": \"1\"}})))", "```", "",
         "Con `langgraph dev` (paquete `langgraph-cli`) basta con `langgraph.json`; el servidor pone el checkpointer.",
         "Los artefactos de los nodos se guardan en `projects/<slug>/threads/<thread_id>/…` dentro de esta carpeta.",
         "", "## Requisitos", "",
         "- Dependencias en `requirements.txt`. Los intérpretes de los nodos en subproceso se leen de `settings.json`",
         "  (`python_estudio`, `python_pesado`): ajústalos si cambias de máquina.",
         "- Ollama en `ollama_url` para los módulos LLM.",
         "- Store / caché: el grafo exportado no trae Store ni caché; quien lo sirva puede pasarlos al compilar."]
    if pesados:
        t.append(f"- Nodos con el intérprete pesado (3.10, torch…): {', '.join(pesados)}.")
    if gpu:
        t.append(f"- Nodos que usan GPU: {', '.join(gpu)}.")
    t += ["", "## API funcional", ""]
    if motivos:
        t += ["No hay `funcional.py`: el proyecto usa funciones que la exportación funcional no admite:", ""]
        t += [f"- {m}" for m in motivos]
    else:
        t += ["`funcional.py` define el mismo grafo con `@entrypoint`/`@task` (un task por nodo; el entrypoint recorre",
              "los supersteps con las mismas reglas de aristas)."]
    return "\n".join(t) + "\n"


def zip_proyecto(project: dict, registry, settings: dict, load_project, home: Path) -> bytes:
    slug = project["slug"]
    subs = subproyectos(project, registry, load_project)
    proyectos = [project, *subs.values()]
    modulos = sorted({n.get("module") for p in proyectos for n in (p.get("nodes") or {}).values() if n.get("module")})
    motivos = motivos_no_funcional(project, registry)
    graphs = {slug: "./graph.py:graph"}
    if not motivos:
        graphs[slug.replace("-", "_") + "_funcional"] = "./funcional.py:graph"
    raiz = f"export_{slug}/"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in RUNTIME:
            z.write(STUDIO / f, raiz + "studio_runtime/" + f)
        for f in modulos:
            z.write(home / "modules" / f, raiz + "modules/" + f)
        if _usa_agente(proyectos, registry):
            for f in sorted((home / "modules" / "tools").glob("*.py")):
                z.write(f, raiz + "modules/tools/" + f.name)
        for p in proyectos:
            z.writestr(raiz + f"projects/{p['slug']}.json", json.dumps(p, ensure_ascii=False, indent=2))
        ajustes = {k: v for k, v in settings.items() if k != "token"}
        # intérpretes vacíos = los del estudio que exporta (el grafo exportado no tiene .venv propio)
        ajustes["python_estudio"] = settings.get("python_estudio") or str(STUDIO / ".venv-studio/bin/python")
        ajustes["python_pesado"] = settings.get("python_pesado") or str(STUDIO.parent / ".venv/bin/python")
        z.writestr(raiz + "settings.json", json.dumps(ajustes, ensure_ascii=False, indent=2))
        z.writestr(raiz + "graph.py", GRAPH_PY.format(name=project.get("name") or slug, slug=slug))
        if not motivos:
            z.writestr(raiz + "funcional.py", funcional_py(project))
        z.writestr(raiz + "langgraph.json",
                   json.dumps({"dependencies": ["."], "graphs": graphs, "env": ".env"}, indent=2))
        z.writestr(raiz + ".env", "LANGSMITH_TRACING=false\n")
        z.writestr(raiz + "requirements.txt", _requirements())
        z.writestr(raiz + "LEEME.md", _leeme(project, motivos, modulos, registry))
    return buf.getvalue()
