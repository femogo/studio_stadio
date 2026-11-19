"""Asistentes versionados [F2] (DISENO.md §3.5, §6.3).

Un asistente pertenece a un proyecto y tiene versiones inmutables {context, node_overrides}; una está activa.
"""
from __future__ import annotations

import json
import time
import uuid

from compiler import resolve_cfg
from db import DB


class AsistenteError(ValueError):
    pass


def validar(project: dict, registry, context, node_overrides, settings: dict) -> tuple[dict, dict]:
    """Comprueba context contra project.context_schema y node_overrides contra la CONFIG de cada módulo."""
    context = context or {}
    node_overrides = node_overrides or {}
    if not isinstance(context, dict):
        raise AsistenteError("context debe ser un objeto")
    if not isinstance(node_overrides, dict):
        raise AsistenteError("node_overrides debe ser un objeto {nodo: {campo: valor}}")
    schema = project.get("context_schema") or []
    claves = {f["key"] for f in schema if isinstance(f, dict) and f.get("key")}
    for k in context:
        if k not in claves:
            raise AsistenteError(f"context: la clave '{k}' no está en el context_schema del proyecto"
                                 + (f" (válidas: {', '.join(sorted(claves))})" if claves else " (está vacío)"))
    nodes = project.get("nodes") or {}
    out_ov = {}
    for nid, ov in node_overrides.items():
        if nid not in nodes:
            raise AsistenteError(f"node_overrides: no existe el nodo '{nid}'")
        if not isinstance(ov, dict):
            raise AsistenteError(f"node_overrides.{nid} debe ser un objeto")
        info = registry.get(nodes[nid].get("module"))
        campos = {f["key"] for f in (info.config if info else [])}
        for k in ov:
            if k not in campos:
                raise AsistenteError(f"node_overrides.{nid}: el módulo {nodes[nid].get('module')} no tiene el campo "
                                     f"'{k}'")
        if ov:
            out_ov[nid] = ov
    return context, out_ov


def contexto_efectivo(project: dict, context: dict | None, settings: dict) -> dict:
    schema = [f for f in (project.get("context_schema") or []) if isinstance(f, dict) and f.get("key")]
    return resolve_cfg(schema, context or {}, None, settings)


class Assistants:
    def __init__(self, db: DB):
        self.db = db

    async def _out(self, row: dict, version: int | None = None) -> dict:
        v = version or row["active_version"]
        ver = await self.db.one("SELECT * FROM assistant_versions WHERE assistant_id=? AND version=?",
                                (row["assistant_id"], v))
        n = await self.db.one("SELECT COUNT(*) n FROM assistant_versions WHERE assistant_id=?", (row["assistant_id"],))
        return {**row, "n_versions": n["n"], "version": v,
                "context": json.loads(ver["context"] or "{}") if ver else {},
                "node_overrides": json.loads(ver["node_overrides"] or "{}") if ver else {}}

    async def list(self, project: str | None = None) -> list[dict]:
        sql = ("SELECT a.*, (SELECT COUNT(*) FROM assistant_versions v WHERE v.assistant_id=a.assistant_id) n_versions "
               "FROM assistants a")
        if project is None:
            return await self.db.all(sql + " ORDER BY COALESCE(updated, created) DESC")
        return await self.db.all(sql + " WHERE project=? ORDER BY created", (project,))

    async def get(self, assistant_id: str, version: int | None = None) -> dict | None:
        row = await self.db.one("SELECT * FROM assistants WHERE assistant_id=?", (assistant_id,))
        if not row:
            return None
        if version is not None:
            ok = await self.db.one("SELECT 1 FROM assistant_versions WHERE assistant_id=? AND version=?",
                                   (assistant_id, int(version)))
            if not ok:
                return None
        return await self._out(row, int(version) if version is not None else None)

    async def create(self, project: str, name: str, context: dict, node_overrides: dict) -> dict:
        aid = str(uuid.uuid4())
        now = time.time()
        await self.db.exec("INSERT INTO assistants(assistant_id, project, name, active_version, created, updated) "
                           "VALUES (?,?,?,?,?,?)", (aid, project, name, 1, now, now))
        await self.db.exec("INSERT INTO assistant_versions VALUES (?,?,?,?,?)",
                           (aid, 1, json.dumps(context, ensure_ascii=False),
                            json.dumps(node_overrides, ensure_ascii=False), now))
        return await self.get(aid)

    async def patch(self, assistant_id: str, name=None, context=None, node_overrides=None) -> dict:
        cur = await self.get(assistant_id)
        now = time.time()
        if name is not None:
            await self.db.exec("UPDATE assistants SET name=?, updated=? WHERE assistant_id=?", (name, now, assistant_id))
        if context is not None or node_overrides is not None:
            m = await self.db.one("SELECT MAX(version) v FROM assistant_versions WHERE assistant_id=?", (assistant_id,))
            v = (m["v"] or 0) + 1
            await self.db.exec("INSERT INTO assistant_versions VALUES (?,?,?,?,?)", (
                assistant_id, v, json.dumps(cur["context"] if context is None else context, ensure_ascii=False),
                json.dumps(cur["node_overrides"] if node_overrides is None else node_overrides, ensure_ascii=False),
                now))
            await self.db.exec("UPDATE assistants SET active_version=?, updated=? WHERE assistant_id=?",
                               (v, now, assistant_id))
        return await self.get(assistant_id)

    async def versions(self, assistant_id: str) -> list[dict]:
        rows = await self.db.all("SELECT version, context, node_overrides, created FROM assistant_versions "
                                 "WHERE assistant_id=? ORDER BY version DESC", (assistant_id,))
        return [{**r, "context": json.loads(r["context"] or "{}"),
                 "node_overrides": json.loads(r["node_overrides"] or "{}")} for r in rows]

    async def activate(self, assistant_id: str, version: int) -> dict | None:
        ok = await self.db.one("SELECT 1 FROM assistant_versions WHERE assistant_id=? AND version=?",
                               (assistant_id, version))
        if not ok:
            return None
        await self.db.exec("UPDATE assistants SET active_version=?, updated=? WHERE assistant_id=?",
                           (version, time.time(), assistant_id))
        return await self.get(assistant_id)

    async def delete(self, assistant_id: str):
        await self.db.exec("DELETE FROM assistants WHERE assistant_id=?", (assistant_id,))

    async def delete_project(self, project: str):
        await self.db.exec("DELETE FROM assistants WHERE project=?", (project,))
