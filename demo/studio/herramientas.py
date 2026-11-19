"""Herramientas para agentes [F2]: funciones @tool en modules/tools/*.py (DISENO.md §8.2, §6.3 GET /tools).

El descubrimiento es por AST (no ejecuta código); la carga, con importlib en cada compilación (sin reiniciar).
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import re
import sys
from pathlib import Path

FILE_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}\.py$")


def _es_tool(dec) -> bool:
    if isinstance(dec, ast.Call):
        dec = dec.func
    return (isinstance(dec, ast.Name) and dec.id == "tool") or (isinstance(dec, ast.Attribute) and dec.attr == "tool")


def _tipo(ann) -> str:
    if ann is None:
        return "any"
    try:
        return ast.unparse(ann)
    except Exception:
        return "any"


def discover(dir: Path) -> list[dict]:
    out = []
    dir = Path(dir)
    if not dir.exists():
        return out
    for p in sorted(dir.glob("*.py")):
        if p.name.startswith("_") or not FILE_RE.match(p.name):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=p.name)
        except SyntaxError as e:
            out.append({"name": p.stem, "desc": "", "file": p.name, "args_schema": {}, "error": f"línea {e.lineno}: {e.msg}"})
            continue
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(_es_tool(d) for d in n.decorator_list):
                args = {a.arg: _tipo(a.annotation) for a in n.args.args
                        if a.arg not in ("self",) and "Injected" not in _tipo(a.annotation)}
                out.append({"name": n.name, "desc": (ast.get_docstring(n) or "").strip(), "file": p.name,
                            "args_schema": args, "error": None})
    return out


def load(dir: Path, nombres: list[str] | None = None) -> list:
    """Importa los ficheros y devuelve los objetos BaseTool (filtrados por nombre si se indica)."""
    from langchain_core.tools import BaseTool
    dir = Path(dir)
    tools = {}
    for p in sorted(dir.glob("*.py")) if dir.exists() else []:
        if p.name.startswith("_") or not FILE_RE.match(p.name):
            continue
        sha = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
        name = f"herr_{p.stem}_{sha}"
        mod = sys.modules.get(name)
        if mod is None:
            spec = importlib.util.spec_from_file_location(name, p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.modules[name] = mod
        for v in vars(mod).values():
            if isinstance(v, BaseTool):
                tools[v.name] = v
    if nombres:
        faltan = [n for n in nombres if n not in tools]
        if faltan:
            raise ValueError(f"Herramientas desconocidas: {', '.join(faltan)} (hay: {', '.join(sorted(tools))})")
        return [tools[n] for n in nombres]
    return list(tools.values())
