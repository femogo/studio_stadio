"""Persistencia: studio.sqlite (aiosqlite, WAL), ajustes (settings.json) y proyectos en disco (DISENO.md §3.2, §3.6, §3.7)."""
from __future__ import annotations

import copy
import json
import re
import secrets
import shutil
import time
import unicodedata
from pathlib import Path

import aiosqlite

DEFAULT_SETTINGS = {
    "host": "0.0.0.0", "port": 8790, "token": "",
    "nombre": "Estudio de agentes", "tema": "oscuro", "abrir_interrupts": True, "sonido": True, "notificaciones": False,
    "snap": 10,
    "ollama_url": "http://localhost:11434", "ollama_model": "qwen2.5:7b", "keep_alive": "5m",
    "embed_model": "nomic-embed-text",
    "recursion_limit": 50, "max_concurrency": None, "durability": "async", "default_timeout": 900,
    "multitask_strategy": "reject",
    "python_pesado": "", "python_estudio": "",   # vacío = demo/.venv y demo/studio/.venv-studio del clon
    "hilos_conservar": 100, "store_items_por_ns": 200, "mcp_servidores": {}, "gpu_procesos_compartidos": 2,
    "video": {"aviso": "Recreación ficticia generada con IA", "color": "0xE8C766", "voz": "ef_dora",
              "retratos": ""},   # vacío = carpeta demo/
}

SETTINGS_SCHEMA = [
    {"grupo": "General", "campos": [
        {"key": "nombre", "label": "Nombre del estudio", "type": "text"},
        {"key": "tema", "label": "Tema", "type": "select", "options": ["oscuro", "claro"]},
        {"key": "abrir_interrupts", "label": "Abrir interrupts automáticamente", "type": "checkbox"},
        {"key": "sonido", "label": "Sonido al terminar", "type": "checkbox"},
        {"key": "notificaciones", "label": "Notificaciones del navegador (solo localhost/HTTPS)", "type": "checkbox"},
        {"key": "snap", "label": "Rejilla del lienzo (px)", "type": "number"}]},
    {"grupo": "Ejecución", "campos": [
        {"key": "recursion_limit", "label": "Límite de recursión", "type": "number"},
        {"key": "max_concurrency", "label": "Concurrencia máxima (vacío = sin límite)", "type": "number"},
        {"key": "durability", "label": "Durabilidad", "type": "select", "options": ["async", "sync"]},
        {"key": "default_timeout", "label": "Timeout por defecto (s)", "type": "number"},
        {"key": "multitask_strategy", "label": "Double-texting", "type": "select",
         "options": ["reject", "enqueue", "interrupt", "rollback"],
         "help": "Qué hacer si se lanza una ejecución en un hilo ocupado: rechazar, encolar, interrumpir la actual o "
                 "deshacerla (rollback)"},
        {"key": "gpu_procesos_compartidos", "label": "Nodos GPU «shared» en subproceso a la vez",
         "type": "number", "help": "Cada uno carga su modelo en VRAM (p. ej. Kokoro ≈ 0,8 GB); 0 = sin límite"},
        {"key": "hilos_conservar", "label": "Hilos a conservar por flujo", "type": "number"},
        {"key": "python_pesado", "label": "Intérprete pesado (3.10)", "type": "path"},
        {"key": "python_estudio", "label": "Intérprete estudio (3.12)", "type": "path"}]},
    {"grupo": "Modelos", "campos": [
        {"key": "ollama_url", "label": "URL de Ollama", "type": "text"},
        {"key": "ollama_model", "label": "Modelo por defecto", "type": "model"},
        {"key": "keep_alive", "label": "keep_alive", "type": "text"},
        {"key": "embed_model", "label": "Modelo de embeddings", "type": "model"}]},
    {"grupo": "Vídeo", "campos": [
        {"key": "video.aviso", "label": "Aviso", "type": "text"},
        {"key": "video.color", "label": "Color del rótulo", "type": "text"},
        {"key": "video.voz", "label": "Voz por defecto", "type": "select", "options": ["ef_dora", "em_alex", "em_santa"]},
        {"key": "video.retratos", "label": "Carpeta de retratos", "type": "path"}]},
    {"grupo": "MCP", "campos": [
        {"key": "mcp_servidores", "label": "Servidores MCP", "type": "json",
         "help": '{"nombre": {"transport": "stdio", "command": "…", "args": ["…"]}} o '
                 '{"nombre": {"transport": "streamable_http", "url": "http://…/mcp"}}'}]},
    {"grupo": "Seguridad", "campos": [
        {"key": "token", "label": "Token", "type": "password", "reinicio": True},
        {"key": "host", "label": "Host", "type": "text", "reinicio": True},
        {"key": "port", "label": "Puerto", "type": "number", "reinicio": True}]},
]


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.values: dict = {}
        self.load()

    def load(self):
        data = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        self.values = _deep_merge(DEFAULT_SETTINGS, data)
        if not self.values.get("token"):
            self.values["token"] = secrets.token_urlsafe(16)
            self.save()
        elif not self.path.exists():
            self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.values, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def update(self, new: dict):
        v = _deep_merge(self.values, new)
        if "mcp_servidores" in new:                     # [F3] se sustituye entero (el merge no permite borrar)
            from mcp_cliente import validar_servidores
            v["mcp_servidores"] = validar_servidores(new["mcp_servidores"])
        if v.get("durability") not in ("async", "sync"):
            raise ValueError("durability solo admite async o sync")
        if v.get("multitask_strategy") not in ("reject", "enqueue", "interrupt", "rollback"):
            raise ValueError("multitask_strategy admite reject, enqueue, interrupt o rollback")
        if not v.get("token"):
            v["token"] = self.values["token"]
        for k in ("recursion_limit", "default_timeout", "hilos_conservar", "snap", "port", "store_items_por_ns",
                  "gpu_procesos_compartidos"):
            v[k] = int(v[k])
        if v.get("max_concurrency") in ("", 0, "0"):
            v["max_concurrency"] = None
        elif v.get("max_concurrency") is not None:
            v["max_concurrency"] = int(v["max_concurrency"])
        self.values = v
        self.save()

    def __getitem__(self, k):
        return self.values[k]

    def get(self, k, d=None):
        return self.values.get(k, d)

    def public(self) -> dict:
        return {k: v for k, v in self.values.items() if k != "token"}


# ------------------------------------------------------------------------------------------ studio.sqlite
MIGRATIONS = [
    """
    CREATE TABLE threads(thread_id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT, metadata TEXT DEFAULT '{}',
      created REAL, updated REAL);
    CREATE TABLE runs(run_id TEXT PRIMARY KEY, thread_id TEXT REFERENCES threads ON DELETE CASCADE, project TEXT,
      project_version INT, module_shas TEXT,
      kind TEXT CHECK(kind IN ('input','continue','resume','replay','fork')),
      input TEXT, checkpoint_id TEXT, checkpoint_id_inicio TEXT,
      interrupt_before TEXT, interrupt_after TEXT, step INT DEFAULT 0,
      status TEXT, motivo TEXT, error TEXT, traceback TEXT, pgids TEXT DEFAULT '[]', t0 REAL, t1 REAL);
    CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, thread_id TEXT, ns TEXT, type TEXT,
      node TEXT, task_id TEXT, data TEXT, ts REAL);
    CREATE INDEX ev_run ON events(run_id, id); CREATE INDEX ev_thread ON events(thread_id, id);
    CREATE TABLE outputs(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, thread_id TEXT, project TEXT, node TEXT,
      kind TEXT CHECK(kind IN ('video','audio','image','text')), clave TEXT, path TEXT, texto TEXT, ts REAL);
    CREATE INDEX runs_thread ON runs(thread_id, t0);
    """,
    # F2 (DISENO.md §3.6, §6.3)
    """
    CREATE TABLE assistants(assistant_id TEXT PRIMARY KEY, project TEXT, name TEXT, active_version INT, created REAL,
      updated REAL);
    CREATE TABLE assistant_versions(assistant_id TEXT REFERENCES assistants ON DELETE CASCADE, version INT,
      context TEXT, node_overrides TEXT, created REAL, PRIMARY KEY(assistant_id, version));
    CREATE TABLE run_queue(run_id TEXT PRIMARY KEY, thread_id TEXT, payload TEXT, created REAL);
    ALTER TABLE runs ADD COLUMN assistant_id TEXT;
    ALTER TABLE runs ADD COLUMN assistant_version INT;
    ALTER TABLE runs ADD COLUMN multitask_strategy TEXT;
    CREATE INDEX runs_project ON runs(project, t0);
    """,
    # F3 (DISENO.md §3.6, §6.4)
    """
    CREATE TABLE datasets(dataset_id TEXT PRIMARY KEY, name TEXT, desc TEXT, project TEXT, created REAL);
    CREATE TABLE examples(example_id TEXT PRIMARY KEY, dataset_id TEXT REFERENCES datasets ON DELETE CASCADE,
      input TEXT, expected TEXT, metadata TEXT DEFAULT '{}', source_run TEXT, created REAL);
    CREATE INDEX examples_ds ON examples(dataset_id, created);
    CREATE TABLE experiments(experiment_id TEXT PRIMARY KEY, dataset_id TEXT REFERENCES datasets ON DELETE CASCADE,
      project TEXT, assistant_id TEXT, evaluators TEXT, status TEXT, created REAL, name TEXT, assistant_version INT,
      concurrency INT DEFAULT 1, t0 REAL, t1 REAL, error TEXT);
    CREATE TABLE experiment_results(experiment_id TEXT REFERENCES experiments ON DELETE CASCADE, example_id TEXT,
      thread_id TEXT, output TEXT, scores TEXT, duration REAL, run_id TEXT, status TEXT, error TEXT,
      PRIMARY KEY(experiment_id, example_id));
    CREATE TABLE crons(cron_id TEXT PRIMARY KEY, project TEXT, assistant_id TEXT, thread_mode TEXT, thread_id TEXT,
      input TEXT, cron TEXT, active INT, last_run REAL, next_run REAL, name TEXT,
      multitask_strategy TEXT DEFAULT 'enqueue', created REAL, last_error TEXT);
    ALTER TABLE runs ADD COLUMN cron_id TEXT;
    ALTER TABLE runs ADD COLUMN experiment_id TEXT;
    CREATE INDEX runs_cron ON runs(cron_id, t0);
    CREATE INDEX runs_exp ON runs(experiment_id);
    """,
]


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.con: aiosqlite.Connection | None = None

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = await aiosqlite.connect(self.path)
        self.con.row_factory = aiosqlite.Row
        await self.con.execute("PRAGMA journal_mode=WAL")
        await self.con.execute("PRAGMA foreign_keys=ON")
        (v,) = await (await self.con.execute("PRAGMA user_version")).fetchone()
        for i, sql in enumerate(MIGRATIONS[v:], start=v + 1):
            await self.con.executescript(sql)
            await self.con.execute(f"PRAGMA user_version={i}")
        await self.con.commit()

    async def close(self):
        if self.con:
            await self.con.close()
            self.con = None

    async def exec(self, sql, args=()):
        cur = await self.con.execute(sql, args)
        await self.con.commit()
        return cur

    async def execmany(self, sql, rows):
        await self.con.executemany(sql, rows)
        await self.con.commit()

    async def all(self, sql, args=()) -> list[dict]:
        cur = await self.con.execute(sql, args)
        return [dict(r) for r in await cur.fetchall()]

    async def one(self, sql, args=()) -> dict | None:
        cur = await self.con.execute(sql, args)
        r = await cur.fetchone()
        return dict(r) if r else None


# ------------------------------------------------------------------------------------------ proyectos
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


class ConflictoVersion(Exception):
    def __init__(self, current):
        super().__init__("version_conflict")
        self.current = current


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40] or "proyecto"
    return s


class Projects:
    def __init__(self, dir: Path, templates: Path):
        self.dir = Path(dir)
        self.templates = Path(templates)

    def _p(self, slug) -> Path:
        if not SLUG_RE.match(slug or ""):
            raise KeyError(slug)
        return self.dir / slug / "project.json"

    def exists(self, slug) -> bool:
        try:
            return self._p(slug).exists()
        except KeyError:
            return False

    def get(self, slug) -> dict:
        p = self._p(slug)
        if not p.exists():
            raise KeyError(slug)
        return json.loads(p.read_text(encoding="utf-8"))

    def get_version(self, slug, version) -> dict | None:
        p = self.dir / slug / "versions" / f"{int(version)}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def list(self) -> list[dict]:
        out = []
        if not self.dir.exists():
            return out
        for p in sorted(self.dir.glob("*/project.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return sorted(out, key=lambda x: -x.get("updated", 0))

    def _write(self, pr: dict):
        d = self.dir / pr["slug"]
        (d / "versions").mkdir(parents=True, exist_ok=True)
        txt = json.dumps(pr, ensure_ascii=False, indent=2)
        tmp = d / "project.json.tmp"
        tmp.write_text(txt, encoding="utf-8")
        tmp.replace(d / "project.json")
        (d / "versions" / f"{pr['version']}.json").write_text(txt, encoding="utf-8")

    def _free_slug(self, name) -> str:
        base = slugify(name)
        slug, n = base, 2
        while (self.dir / slug).exists():
            slug = f"{base}-{n}"
            n += 1
        return slug

    def template(self, tid) -> dict:
        if not re.match(r"^[a-z0-9_]+$", tid or ""):
            raise KeyError(tid)
        p = self.templates / f"{tid}.json"
        if not p.exists():
            raise KeyError(tid)
        return json.loads(p.read_text(encoding="utf-8"))

    def templates_list(self) -> list[dict]:
        out = []
        for p in sorted(self.templates.glob("*.json")):
            t = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": p.stem, "name": t.get("name", p.stem), "desc": t.get("desc", ""),
                        "icon": t.get("icon", "◇"), "fase": t.get("fase", 1)})
        return out

    def create_from(self, data: dict, name: str | None = None, desc: str | None = None) -> dict:
        pr = copy.deepcopy(data)
        for k in ("slug", "created", "updated", "version"):
            pr.pop(k, None)
        subs = pr.pop("subproyectos", None) or {}
        if subs:
            # [F3] plantillas con subproyectos: se crean como <slug>-<clave> y se sustituye "{slug}-<clave>"
            # en la configuración de los nodos del proyecto principal
            nombre = name or pr.get("name") or "Proyecto"
            slug = self._free_slug(nombre)
            texto = json.dumps(pr.get("nodes") or {}, ensure_ascii=False)
            for clave, sub in subs.items():
                sp = copy.deepcopy(sub)
                sp.setdefault("template", pr.get("template") or "")
                hecho = self.create_from(sp, name=f"{nombre} · {sub.get('name') or clave}")
                hecho = self._renombrar(hecho, f"{slug}-{clave}")
                texto = texto.replace("{slug}-" + clave, hecho["slug"])
            pr["nodes"] = json.loads(texto)
        pr["name"] = name or pr.get("name") or "Proyecto"
        if desc is not None:
            pr["desc"] = desc
        pr.setdefault("desc", "")
        pr.setdefault("template", "")
        pr.setdefault("state_schema", {})
        pr.setdefault("context_schema", [])
        pr.setdefault("nodes", {})
        pr.setdefault("edges", [])
        pr.setdefault("layout", {"zoom": 1.0, "pan": [0, 0]})
        now = time.time()
        out = {"slug": self._free_slug(pr["name"]), **pr, "created": now, "updated": now, "version": 1}
        self._write(out)
        return out

    def _renombrar(self, pr: dict, deseado: str) -> dict:
        """Cambia el slug de un proyecto recién creado (sin hilos) al deseado o al primero libre a partir de él."""
        if pr["slug"] == deseado:
            return pr
        nuevo = deseado if not self._p(deseado).exists() else self._free_slug(deseado)
        if nuevo == pr["slug"]:
            return pr
        shutil.rmtree(self.dir / pr["slug"], ignore_errors=True)
        pr = dict(pr, slug=nuevo)
        self._write(pr)
        return pr

    def put(self, slug, pr: dict) -> dict:
        cur = self.get(slug)
        if int(pr.get("version", -1)) != int(cur["version"]):
            raise ConflictoVersion(cur)
        new = dict(pr, slug=slug, created=cur.get("created"), updated=time.time(), version=int(cur["version"]) + 1)
        self._write(new)
        return new

    def delete(self, slug):
        p = self._p(slug)
        shutil.rmtree(p.parent, ignore_errors=True)


def export_clean(pr: dict) -> dict:
    return {k: v for k, v in pr.items() if k not in ("slug", "created", "updated", "version")}
