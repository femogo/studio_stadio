"""Descubrimiento, validación (por AST, sin ejecutar) e instantáneas de módulos (DISENO.md §3.1, §4.2)."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FILE_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}\.py$")
KINDS_F1 = {"task", "input", "output", "interrupt"}
KINDS_F2 = {"router", "subgraph", "tools", "agent"}
EXECS = {"local", "proceso"}
GPUS = {False, "shared", "exclusive"}
INTERPRETES = {"pesado", "estudio"}
FIELD_TYPES = {"text", "textarea", "code", "number", "select", "checkbox", "json", "password", "path", "model",
               "state_key", "keys"}
PROHIBIDOS_LOCAL = {"torch", "diffusers", "kokoro"}

META_DEFAULTS: dict[str, Any] = {
    "name": "", "category": "General", "icon": "▣", "desc": "", "kind": "task", "exec": "local",
    "interprete": "estudio", "gpu": False, "llm": False, "inputs": [], "outputs": [],
    "outputs_from_config": [], "inputs_from_config": [], "weight": 1, "defaults": {},
    "command": False,            # [F2] el módulo enruta con Command/Send: sus aristas salientes son destinos
    "store_reads": [],           # [F2] namespaces del Store precargados (exec proceso)
}


class ModuloError(Exception):
    def __init__(self, msg: str, line: int | None = None):
        super().__init__(msg)
        self.line = line


@dataclass
class ModInfo:
    file: str
    path: Path
    source: str
    sha: str
    meta: dict = field(default_factory=dict)
    config: list = field(default_factory=list)
    error: str | None = None
    line: int | None = None
    avisos: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"file": self.file, "meta": self.meta, "config": self.config, "error": self.error, "line": self.line,
                "sha": self.sha, "avisos": self.avisos}


def sha_of(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


def parse_source(source: str, file: str = "<mod>") -> tuple[dict, list]:
    """Valida un módulo sin ejecutarlo. Devuelve (meta completo, config). Lanza ModuloError (validación dura)."""
    try:
        tree = ast.parse(source, filename=file)
    except SyntaxError as e:
        raise ModuloError(f"Error de sintaxis: {e.msg}", e.lineno) from None
    meta = config = None
    tiene_run = False
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("META", "CONFIG"):
                try:
                    val = ast.literal_eval(node.value)
                except Exception:
                    raise ModuloError(f"{name} debe ser un literal (sin llamadas ni variables)", node.lineno) from None
                if name == "META":
                    meta = val
                else:
                    config = val
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run":
            tiene_run = True
    if meta is None or not isinstance(meta, dict):
        raise ModuloError("Falta META (dict literal)", 1)
    if config is None:
        config = []
    if not isinstance(config, list) or not all(isinstance(c, dict) and "key" in c for c in config):
        raise ModuloError("CONFIG debe ser una lista de dicts con 'key'", 1)
    if not tiene_run:
        raise ModuloError("Falta la función run(state, cfg, ctx)", 1)
    full = {**META_DEFAULTS, **meta}
    if full["kind"] not in KINDS_F1 | KINDS_F2:
        raise ModuloError(f"kind desconocido: {full['kind']!r}", 1)
    if full["exec"] not in EXECS:
        raise ModuloError(f"exec desconocido: {full['exec']!r}", 1)
    if full["gpu"] not in GPUS:
        raise ModuloError(f"gpu desconocido: {full['gpu']!r} (false | shared | exclusive)", 1)
    if full["kind"] in KINDS_F2 and full["exec"] != "local":
        raise ModuloError(f"Un módulo kind {full['kind']!r} debe ser exec: local", 1)
    if full.get("command") and full["exec"] != "local":
        raise ModuloError("command: True (Command/Send) solo en exec: local", 1)
    if full["exec"] == "proceso" and full["interprete"] not in INTERPRETES:
        raise ModuloError(f"interprete desconocido: {full['interprete']!r}", 1)
    for c in config:
        if c.get("type", "text") not in FIELD_TYPES:
            raise ModuloError(f"Tipo de campo desconocido en CONFIG: {c.get('type')!r}", 1)
    if full["exec"] == "local":
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                if m.split(".")[0] in PROHIBIDOS_LOCAL:
                    raise ModuloError(f"Un módulo local no puede importar {m.split('.')[0]} (usa exec: proceso)",
                                      node.lineno)
    if not full["name"]:
        full["name"] = Path(file).stem
    return full, config


def keys_from_value(v: Any) -> list[str]:
    """Valor de un campo *_from_config → lista de claves (texto con comas, lista, dict o lista de {clave})."""
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [k.strip() for k in v.split(",") if k.strip()]
    if isinstance(v, dict):
        return [str(k) for k in v]
    if isinstance(v, list):
        out = []
        for it in v:
            if isinstance(it, dict):
                if it.get("clave"):
                    out.append(str(it["clave"]))
            elif it:
                out.append(str(it))
        return out
    return []


class Registry:
    """Módulos de una carpeta. Se relee del disco en cada consulta (cambios externos sin reiniciar)."""

    def __init__(self, dir: Path, snapdir: Path):
        self.dir = Path(dir)
        self.snapdir = Path(snapdir)
        self.hist = self.dir / ".historial"
        self._cache: dict[str, ModInfo] = {}
        self._loaded: dict[str, Any] = {}

    # ------------------------------------------------------------- lectura
    def path(self, file: str) -> Path:
        if not FILE_RE.match(file):
            raise ModuloError(f"Nombre de módulo no válido: {file!r}")
        return self.dir / file

    def get(self, file: str) -> ModInfo | None:
        p = self.path(file)
        if not p.exists():
            return None
        src = p.read_text(encoding="utf-8")
        sha = sha_of(src)
        c = self._cache.get(file)
        if c and c.sha == sha:
            return c
        info = ModInfo(file=file, path=p, source=src, sha=sha)
        try:
            info.meta, info.config = parse_source(src, file)
        except ModuloError as e:
            info.error, info.line = str(e), e.line
            info.meta = {**META_DEFAULTS, "name": Path(file).stem}
        self._cache[file] = info
        return info

    def all(self) -> dict[str, ModInfo]:
        out = {}
        for p in sorted(self.dir.glob("*.py")):
            if p.name.startswith("_") or not FILE_RE.match(p.name):
                continue
            out[p.name] = self.get(p.name)
        return out

    # ------------------------------------------------------------- escritura
    def _backup(self, file: str):
        p = self.path(file)
        if p.exists():
            self.hist.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            dst = self.hist / f"{file}.{ts}"
            n = 1
            while dst.exists():
                n += 1
                dst = self.hist / f"{file}.{ts}-{n}"
            shutil.copy2(p, dst)

    def save(self, file: str, source: str, expected_sha: str | None) -> ModInfo:
        p = self.path(file)
        if p.exists() and expected_sha is not None and sha_of(p.read_text(encoding="utf-8")) != expected_sha:
            raise ConflictError("El módulo cambió desde que lo cargaste")
        parse_source(source, file)          # lanza ModuloError (→ 422)
        self._backup(file)
        p.write_text(source, encoding="utf-8")
        return self.get(file)

    def create(self, name: str, from_file: str | None = None) -> str:
        base = re.sub(r"[^a-z0-9_]+", "_", _ascii(name).lower()).strip("_") or "modulo"
        if not base[0].isalpha():
            base = "m_" + base
        base = base[:44]
        file = f"{base}.py"
        n = 2
        while (self.dir / file).exists():
            file = f"{base}_{n}.py"
            n += 1
        if from_file:
            src = self.path(from_file).read_text(encoding="utf-8")
            src = re.sub(r'("name"\s*:\s*)"([^"]*)"', lambda m: f'{m.group(1)}"{name}"', src, count=1)
        else:
            src = SKELETON.replace("__NOMBRE__", name.replace('"', "'"))
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / file).write_text(src, encoding="utf-8")
        return file

    def delete(self, file: str):
        self._backup(file)
        self.path(file).unlink()
        self._cache.pop(file, None)

    def history(self, file: str) -> list[dict]:
        self.path(file)
        if not self.hist.exists():
            return []
        out = []
        for p in sorted(self.hist.glob(f"{file}.*"), reverse=True):
            ts = p.name[len(file) + 1:]
            out.append({"ts": ts, "size": p.stat().st_size, "source": p.read_text(encoding="utf-8")})
        return out

    def restore(self, file: str, ts: str) -> ModInfo:
        if not re.match(r"^[0-9-]+$", ts):
            raise ModuloError("ts no válido")
        src = (self.hist / f"{file}.{ts}").read_text(encoding="utf-8")
        cur = self.path(file)
        return self.save(file, src, None if not cur.exists() else sha_of(cur.read_text(encoding="utf-8")))

    # ------------------------------------------------------------- instantáneas
    def snapshot(self, file: str) -> tuple[ModInfo, Path]:
        info = self.get(file)
        if info is None:
            raise ModuloError(f"No existe el módulo {file}")
        if info.error:
            raise ModuloError(f"{file}: {info.error}", info.line)
        self.snapdir.mkdir(parents=True, exist_ok=True)
        snap = self.snapdir / f"{info.sha}.py"
        if not snap.exists():
            snap.write_text(info.source, encoding="utf-8")
        return info, snap

    def load(self, file: str):
        """Carga (local) la instantánea del código actual: módulo Python con nombre m_<sha>."""
        info, snap = self.snapshot(file)
        mod = self._loaded.get(info.sha)
        if mod is None:
            name = f"m_{info.sha}"
            spec = importlib.util.spec_from_file_location(name, snap)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
            self._loaded[info.sha] = mod
        return info, mod


class ConflictError(Exception):
    pass


def _ascii(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


SKELETON = '''"""__NOMBRE__ — módulo del Estudio de agentes.

Un módulo es un archivo .py con META (literal), CONFIG (lista de campos) y run(state, cfg, ctx).
- state: dict plano con las claves del estado (en exec "proceso" solo llegan las de META["inputs"]).
- cfg:   configuración del nodo ya resuelta (defaults de CONFIG → proyecto → ajustes).
- ctx:   ctx.log(...), ctx.progress(0..1), ctx.update({...}) (vista previa), ctx.fill("{clave}", state),
         ctx.path("ruta/relativa"), ctx.stream(cmd, cwd, on_line), ctx.run_dir, ctx.settings, ctx.node_id…
Devuelve un dict con las claves que cambian (deben estar en META["outputs"] o en el esquema del proyecto) o None.
"""

META = {
    "name": "__NOMBRE__", "category": "General", "icon": "▣", "desc": "Describe aquí qué hace.",
    "kind": "task",          # task | input | output | interrupt
    "exec": "local",         # local (en el servidor) | proceso (subproceso aparte)
    "interprete": "estudio", # solo exec proceso: estudio (3.12) | pesado (3.10 con torch)
    "gpu": False,            # False | "shared" | "exclusive"
    "inputs": ["texto"],
    "outputs": ["resultado"],
    "weight": 1,
}

CONFIG = [
    {"key": "prefijo", "label": "Prefijo", "type": "text", "default": "", "help": "Texto que se antepone"},
]


def run(state, cfg, ctx):
    texto = state.get("texto") or ""
    ctx.log("Procesando", len(texto), "caracteres")
    ctx.progress(0.5)
    return {"resultado": cfg["prefijo"] + texto}
'''
