"""Memoria a largo plazo (Store): guardar, buscar (semántica), leer o borrar recuerdos entre hilos."""
import uuid

META = {
    "name": "Memoria", "category": "IA", "icon": "🧠", "kind": "task", "exec": "local", "gpu": False,
    "desc": "Usa el Store del estudio (persistente entre hilos). Namespace con segmentos separados por '/', que "
            "admiten {clave} del contexto del agente o del estado. 'buscar' usa embeddings de Ollama.",
    "inputs": [], "outputs": [], "outputs_from_config": ["campo_salida"], "weight": 1,
}

CONFIG = [
    {"key": "accion", "label": "Acción", "type": "select", "default": "buscar",
     "options": ["guardar", "buscar", "leer", "borrar", "listar"]},
    {"key": "namespace", "label": "Namespace", "type": "text", "default": "memorias/{usuario}"},
    {"key": "clave", "label": "Clave", "type": "text", "default": "",
     "help": "guardar: vacío = clave nueva; leer/borrar: obligatoria (admite {clave})"},
    {"key": "valor", "label": "Texto a guardar", "type": "textarea", "default": "{texto}"},
    {"key": "consulta", "label": "Consulta (buscar)", "type": "textarea", "default": "{pregunta}"},
    {"key": "limite", "label": "Máx. resultados", "type": "number", "default": 3},
    {"key": "campo_salida", "label": "Campo de salida", "type": "state_key", "default": "recuerdos"},
]


async def run(state, cfg, ctx):
    if ctx.store is None:
        raise RuntimeError("Este grafo no tiene Store")
    vals = {**(ctx.context or {}), **state}
    ns = tuple(s for s in ctx.fill(cfg.get("namespace") or "memorias", vals).split("/") if s)
    if not ns:
        raise ValueError("Namespace vacío")
    accion = cfg.get("accion") or "buscar"
    campo = cfg.get("campo_salida") or "recuerdos"
    clave = ctx.fill(cfg.get("clave") or "", vals).strip()
    if accion == "guardar":
        texto = ctx.fill(cfg.get("valor") or "", vals).strip()
        if not texto:
            ctx.log("Nada que guardar")
            return None
        clave = clave or uuid.uuid4().hex[:12]
        await ctx.store.aput(ns, clave, {"texto": texto})
        ctx.log(f"Guardado {'/'.join(ns)}/{clave}: {texto}")
        return {campo: clave}
    if accion == "borrar":
        await ctx.store.adelete(ns, clave)
        return None
    if accion == "leer":
        it = await ctx.store.aget(ns, clave)
        return {campo: it.value if it else None}
    consulta = ctx.fill(cfg.get("consulta") or "", vals).strip() if accion == "buscar" else ""
    res = await ctx.store.asearch(ns, query=consulta or None, limit=int(cfg.get("limite") or 3))
    out = [{"clave": r.key, **(r.value if isinstance(r.value, dict) else {"valor": r.value}),
            **({"score": round(r.score, 4)} if r.score is not None else {})} for r in res]
    for r in out:
        ctx.log(f"· {r.get('texto') or r}")
    return {campo: out}
