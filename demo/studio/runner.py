#!/usr/bin/env python
"""Ejecuta UN nodo en un subproceso (DISENO.md §3.1.1). Funciona con Python 3.10 (pesado) y 3.12 (estudio).

Uso (lo lanza el estudio): python -u runner.py <ruta del .py> <carpeta de la tarea> <node_id>
Entrada: <carpeta>/_in.json = {"state", "cfg", "settings", "node", "thread_id", "run_id", "context", "store"}
Protocolo por stdout (una línea cada mensaje):
  @@P <0..1>   progreso          @@S <json>   vista previa (no toca el estado)
  @@R <json>   resultado: exactamente una vez; lo único que se aplica al estado
  cualquier otra línea = log
"""
import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

STUDIO = Path(__file__).resolve().parent                 # /opt/historico/demo/studio
BASE = STUDIO.parent                                     # /opt/historico/demo


class SafeDict(dict):
    """Para plantillas: '{clave}' que no exista en el estado se sustituye por ''."""

    def __missing__(self, key):
        return ""


class CtxBase:
    """Parte común del ctx en modo local y en proceso."""

    def __init__(self, run_dir, node_id, settings, thread_id=None, run_id=None, context=None):
        self.run_dir = Path(run_dir)
        self.node_id = node_id
        self.settings = settings or {}
        self.thread_id = thread_id
        self.run_id = run_id
        self.context = context or {}
        self.base = BASE
        self.studio = STUDIO

    def fill(self, texto, state):
        """Sustituye '{clave}' por state[clave]; clave ausente (o None) → ''."""
        vals = SafeDict({k: ("" if v is None else v) for k, v in (state or {}).items()})
        try:
            return str(texto).format_map(vals)
        except (ValueError, IndexError, AttributeError):
            # llaves sueltas (p. ej. JSON en el prompt): sustitución sencilla solo de {clave}
            return re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: str(vals[m.group(1)]), str(texto))

    def path(self, p):
        p = Path(p)
        return p if p.is_absolute() else self.base / p

    def stream(self, cmd, cwd=None, on_line=None):
        """Subproceso con su salida al log línea a línea (también barras tqdm con \\r). Devuelve el código de salida."""
        self.log("$ " + " ".join(map(str, cmd)))
        try:
            proc = subprocess.Popen(list(map(str, cmd)), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as e:
            self.log(f"No se pudo lanzar: {e}")
            return 127
        buf = b""
        while True:
            ch = proc.stdout.read1(4096)
            if not ch:
                break
            buf += ch
            partes = re.split(rb"[\r\n]", buf)
            buf = partes.pop()
            for raw in partes:
                self._line(raw, on_line)
        if buf:
            self._line(buf, on_line)
        return proc.wait()

    def _line(self, raw, on_line):
        line = raw.decode("utf-8", "replace").strip()
        if line:
            self.log(line)
            if on_line:
                on_line(line)

    # a implementar
    def log(self, *a):
        raise NotImplementedError

    def progress(self, x):
        raise NotImplementedError

    def update(self, d):
        raise NotImplementedError


class Ctx(CtxBase):
    """ctx en el subproceso: escribe el protocolo por stdout."""

    def __init__(self, *a, out=None, **kw):
        super().__init__(*a, **kw)
        self._out = out or sys.stdout

    def _emit(self, s):
        self._out.write(s + "\n")
        self._out.flush()

    def progress(self, x):
        try:
            x = max(0.0, min(1.0, float(x)))
        except (TypeError, ValueError):
            return
        self._emit(f"@@P {x:.4f}")

    def log(self, *a):
        txt = " ".join(str(x) for x in a)
        for line in txt.splitlines() or [""]:
            if line.startswith("@@"):
                line = " " + line            # nunca corromper el protocolo
            self._emit(line)

    def update(self, d):
        self._emit("@@S " + json.dumps(d, ensure_ascii=False, default=str))

    def token(self, *_):
        raise RuntimeError("ctx.token solo existe en modo local")

    def interrupt(self, *_a, **_k):
        raise RuntimeError("ctx.interrupt solo existe en modo local")


class ProcStore:
    """ctx.store en un subproceso [F2]: lecturas precargadas (META['store_reads']); escrituras diferidas."""

    def __init__(self, items):
        self._items = {}
        for it in items or []:
            self._items[(tuple(it["namespace"]), it["key"])] = it["value"]
        self.puts = []

    def get(self, namespace, key):
        return self._items.get((tuple(namespace), key))

    def search(self, namespace, limit=50):
        ns = tuple(namespace)
        out = [{"namespace": list(n), "key": k, "value": v} for (n, k), v in self._items.items() if n[:len(ns)] == ns]
        return out[:limit]

    def put(self, namespace, key, value, index=True):
        """value=None borra. index=False = sin embedding. Se aplica al Store cuando el nodo termina bien."""
        ns = list(namespace)
        if value is None:
            self._items.pop((tuple(ns), key), None)
        else:
            self._items[(tuple(ns), key)] = value
        self.puts.append([ns, key, value, bool(index)])

    def delete(self, namespace, key):
        self.put(namespace, key, None)


def _gpu_lock(meta):
    gpu = (meta or {}).get("gpu")
    if gpu not in ("shared", "exclusive"):
        return None
    home = Path(os.environ.get("STUDIO_HOME") or STUDIO)
    lock = home / "data" / "gpu.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock, "a+")
    fcntl.flock(f, fcntl.LOCK_EX if gpu == "exclusive" else fcntl.LOCK_SH)
    return f


def main():
    modfile, run_dir, node_id = sys.argv[1:4]
    real_out = sys.stdout
    inp = json.loads(Path(run_dir, "_in.json").read_text(encoding="utf-8"))
    ctx = Ctx(run_dir, node_id, inp.get("settings", {}), thread_id=inp.get("thread_id"), run_id=inp.get("run_id"),
              context=inp.get("context"), out=real_out)
    ctx.store = ProcStore(inp.get("store"))
    try:
        spec = importlib.util.spec_from_file_location("mod_" + Path(modfile).stem, modfile)
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(Path(modfile).parent))
        spec.loader.exec_module(mod)
    except Exception:
        traceback.print_exc(file=real_out)
        sys.exit(1)
    lock = _gpu_lock(getattr(mod, "META", {}))
    try:
        result = mod.run(dict(inp.get("state") or {}), inp.get("cfg") or {}, ctx)
    except SystemExit as e:
        if e.code not in (0, None):
            ctx.log(f"ERROR: {e.code}")
            sys.exit(1)
        result = None
    except Exception:
        traceback.print_exc(file=real_out)
        real_out.flush()
        sys.exit(1)
    finally:
        if lock:
            lock.close()
    if result is not None and not isinstance(result, dict):
        kind = type(result).__name__
        extra = " (Command, Send y corrutinas solo en modo local)" if kind in ("Command", "Send", "coroutine", "list") else ""
        ctx.log(f"ERROR: run() debe devolver un dict o None, devolvió {kind}{extra}")
        sys.exit(1)
    if ctx.store.puts:
        result = dict(result or {})
        result["__store_put__"] = ctx.store.puts
    try:
        payload = json.dumps(result or {}, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        ctx.log(f"ERROR: el resultado no es serializable a JSON: {e}")
        sys.exit(1)
    ctx._emit("@@R " + payload)


if __name__ == "__main__":
    main()
