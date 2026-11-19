"""Lógica de F3 sin FastAPI (DISENO.md §6.4): evaluadores, crons, salida de un hilo, JSONL de datasets."""
from __future__ import annotations

import json
import re
import time

import httpx

from compiler import SAFE_BUILTINS

# ------------------------------------------------------------------------------------------ salida
def claves_salida(project: dict) -> list[str] | None:
    """Claves `output: true` del state_schema; None = todas."""
    ks = [k for k, v in (project.get("state_schema") or {}).items() if isinstance(v, dict) and v.get("output")]
    return ks or None


def salida(project: dict, values: dict) -> dict:
    ks = claves_salida(project)
    vals = {k: v for k, v in (values or {}).items() if v is not None}
    return {k: vals[k] for k in ks if k in vals} if ks else vals


# ------------------------------------------------------------------------------------------ evaluadores
NOMBRE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ .-]{0,47}$")


def validar_evaluadores(evs) -> list[dict]:
    if not isinstance(evs, list) or not evs:
        raise ValueError("evaluators debe ser una lista con al menos un evaluador")
    out, vistos = [], set()
    for e in evs:
        if not isinstance(e, dict):
            raise ValueError("cada evaluador debe ser un objeto")
        n = str(e.get("name") or "").strip()
        if not NOMBRE_RE.match(n):
            raise ValueError(f"nombre de evaluador no válido: {n!r}")
        if n in vistos:
            raise ValueError(f"evaluador repetido: {n}")
        vistos.add(n)
        t = e.get("type")
        if t == "expr":
            expr = str(e.get("expr") or "").strip()
            if not expr:
                raise ValueError(f"evaluador {n}: falta expr")
            try:
                compile(expr, "<evaluador>", "eval")
            except SyntaxError as ex:
                raise ValueError(f"evaluador {n}: sintaxis no válida: {ex.msg}") from ex
            out.append({"name": n, "type": "expr", "expr": expr})
        elif t == "llm":
            pr = str(e.get("prompt") or "").strip()
            if not pr:
                raise ValueError(f"evaluador {n}: falta prompt")
            out.append({"name": n, "type": "llm", "prompt": pr, "modelo": e.get("modelo") or ""})
        else:
            raise ValueError(f"evaluador {n}: type debe ser expr o llm")
    return out


def eval_expr(expr: str, output: dict, expected: dict | None, inp: dict | None) -> dict:
    try:
        v = eval(compile(expr, "<evaluador>", "eval"), {"__builtins__": SAFE_BUILTINS},
                 {"output": output or {}, "expected": expected or {}, "input": inp or {}})
    except Exception as e:  # noqa: BLE001
        return {"score": None, "comentario": f"{type(e).__name__}: {e}"}
    if isinstance(v, bool):
        return {"score": 1.0 if v else 0.0, "comentario": ""}
    if isinstance(v, (int, float)):
        return {"score": float(v), "comentario": ""}
    return {"score": None, "comentario": f"la expresión devolvió {type(v).__name__}, no bool ni número"}


def _fill(prompt: str, vals: dict) -> str:
    def rep(m):
        k = m.group(1)
        return json.dumps(vals[k], ensure_ascii=False) if k in vals else m.group(0)
    return re.sub(r"\{(input|output|expected)\}", rep, prompt)


async def eval_llm(ev: dict, output, expected, inp, settings: dict) -> dict:
    url = (settings.get("ollama_url") or "http://localhost:11434").rstrip("/")
    modelo = ev.get("modelo") or settings.get("ollama_model") or "qwen2.5:7b"
    prompt = _fill(ev["prompt"], {"input": inp, "output": output, "expected": expected}) + (
        '\n\nResponde SOLO con JSON: {"score": número entre 0 y 1, "comentario": "texto breve"}')
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5)) as c:
            r = await c.post(url + "/api/generate", json={
                "model": modelo, "prompt": prompt, "stream": False, "format": "json",
                "keep_alive": settings.get("keep_alive", "5m"), "options": {"temperature": 0}})
            r.raise_for_status()
            txt = r.json().get("response") or ""
    except (httpx.HTTPError, ValueError):
        return {"score": None, "comentario": "ollama"}
    try:
        d = json.loads(txt)
        s = float(d["score"])
        if not 0 <= s <= 1:
            raise ValueError
        return {"score": s, "comentario": str(d.get("comentario") or "")}
    except (ValueError, KeyError, TypeError):
        return {"score": None, "comentario": f"respuesta no válida del juez: {txt[:200]}"}


async def puntuar(evs: list[dict], output, expected, inp, settings: dict) -> dict:
    out = {}
    for ev in evs:
        if ev["type"] == "expr":
            out[ev["name"]] = eval_expr(ev["expr"], output, expected, inp)
        else:
            out[ev["name"]] = await eval_llm(ev, output, expected, inp, settings)
    return out


def resumen(scores_list: list[dict], evs: list[dict]) -> dict:
    out = {}
    for ev in evs:
        xs = [s[ev["name"]]["score"] for s in scores_list
              if s and ev["name"] in s and s[ev["name"]].get("score") is not None]
        out[ev["name"]] = round(sum(xs) / len(xs), 4) if xs else None
    return out


# ------------------------------------------------------------------------------------------ crons
def cron_valido(expr: str) -> bool:
    from croniter import croniter
    try:
        return bool(expr) and croniter.is_valid(expr)
    except Exception:  # noqa: BLE001
        return False


def siguiente(expr: str, desde: float | None = None) -> float:
    from croniter import croniter
    from datetime import datetime
    return croniter(expr, datetime.fromtimestamp(desde or time.time())).get_next(float)


def proximas(expr: str, n=5, desde=None) -> list[float]:
    from croniter import croniter
    from datetime import datetime
    it = croniter(expr, datetime.fromtimestamp(desde or time.time()))
    return [it.get_next(float) for _ in range(n)]


# ------------------------------------------------------------------------------------------ JSONL
def jsonl_dump(ds: dict, ejemplos: list[dict]) -> str:
    lines = [json.dumps({"__dataset__": {"name": ds["name"], "desc": ds.get("desc") or ""}}, ensure_ascii=False)]
    for e in ejemplos:
        lines.append(json.dumps({"input": e["input"], "expected": e["expected"], "metadata": e.get("metadata") or {}},
                                ensure_ascii=False))
    return "\n".join(lines) + "\n"


def jsonl_load(texto: str) -> tuple[dict, list[dict]]:
    cab, ejemplos = {}, []
    for i, ln in enumerate(texto.splitlines(), 1):
        if not ln.strip():
            continue
        try:
            d = json.loads(ln)
        except ValueError as e:
            raise ValueError(f"línea {i}: JSON no válido ({e})") from e
        if not isinstance(d, dict):
            raise ValueError(f"línea {i}: debe ser un objeto")
        if "__dataset__" in d:
            cab = d["__dataset__"] or {}
            continue
        if not isinstance(d.get("input"), dict):
            raise ValueError(f"línea {i}: falta input (objeto)")
        ejemplos.append({"input": d["input"], "expected": d.get("expected"), "metadata": d.get("metadata") or {}})
    return cab, ejemplos


# ------------------------------------------------------------------------------------------ playground
class OllamaCaido(RuntimeError):
    pass


class _StoreLectura:
    """ctx.store en el playground: lecturas del Store real; escribir es un error."""

    def __init__(self, store):
        self._s = store

    def __getattr__(self, n):
        if n.startswith(("put", "aput", "delete", "adelete", "batch", "abatch")):
            raise RuntimeError("El playground no escribe en el Store")
        return getattr(self._s, n)


def _es_conexion(e: BaseException) -> bool:
    while e is not None:
        if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, ConnectionRefusedError)):
            return True
        e = e.__cause__ or e.__context__
    return False


async def playground(mod, node_id: str, state: dict, cfgs: list[dict], settings: dict, context: dict, store,
                     run_dir) -> list[dict]:
    """Ejecuta run() de un módulo local LLM aislado, una vez por cfg, en secuencia (§6.4)."""
    import asyncio
    import contextvars

    from compiler import _SYNC_POOL, LocalCtx

    sets = {k: v for k, v in settings.items() if k != "token"}
    out = []
    caidas = 0
    for cfg in cfgs:
        logs, toks = [], []

        def emit(tipo, data, logs=logs, toks=toks):
            if tipo == "node.log":
                logs.append(data.get("line", ""))
            elif tipo == "token":
                toks.append(data.get("text", ""))

        ctx = LocalCtx(emit, lambda: None, run_dir, node_id, sets, thread_id=None, run_id=None, context=context or {})
        ctx.store = _StoreLectura(store) if store is not None else None

        def _no_interrupt(*a, **k):
            raise RuntimeError("ctx.interrupt no está disponible en el playground")
        ctx.interrupt = _no_interrupt
        fill = {k: ctx.fill(cfg[k], state) for k in ("prompt", "sistema") if isinstance(cfg.get(k), str)}
        t0 = time.time()
        res, error = None, None
        try:
            fn = mod.run
            if asyncio.iscoroutinefunction(fn):
                res = await fn(dict(state), cfg, ctx)
            else:
                res = await asyncio.get_running_loop().run_in_executor(
                    _SYNC_POOL, contextvars.copy_context().run, fn, dict(state), cfg, ctx)
                if asyncio.iscoroutine(res):
                    res = await res
        except Exception as e:  # noqa: BLE001
            if _es_conexion(e):
                caidas += 1
            error = f"{type(e).__name__}: {e}"
        out.append({"cfg": cfg, "prompt": fill.get("prompt"), "sistema": fill.get("sistema"), "salida": res,
                    "tokens": len(toks), "texto_tokens": "".join(toks), "logs": logs,
                    "duracion": round(time.time() - t0, 3), "error": error})
    if cfgs and caidas == len(cfgs):
        raise OllamaCaido(out[0]["error"])
    return out
