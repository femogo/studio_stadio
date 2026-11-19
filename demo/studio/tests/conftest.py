import json
import shutil
import sys
from pathlib import Path

import pytest

STUDIO = Path(__file__).resolve().parent.parent
BASE = STUDIO.parent
sys.path.insert(0, str(STUDIO))
PY_ESTUDIO = str(STUDIO / ".venv-studio/bin/python")
PY_PESADO = str(BASE / ".venv/bin/python")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """STUDIO_HOME temporal con copia de modules/ y templates/."""
    h = tmp_path / "home"
    h.mkdir()
    shutil.copytree(STUDIO / "modules", h / "modules", ignore=shutil.ignore_patterns(".historial", "__pycache__"))
    if (STUDIO / "templates").exists():
        shutil.copytree(STUDIO / "templates", h / "templates")
    monkeypatch.setenv("STUDIO_HOME", str(h))
    return h


import os
import signal
import socket
import subprocess
import time

import httpx

TOKEN = "token-de-pruebas-0123456789"
PORT = 8799


def preparar_home(h: Path):
    h.mkdir(parents=True, exist_ok=True)
    if not (h / "modules").exists():
        shutil.copytree(STUDIO / "modules", h / "modules", ignore=shutil.ignore_patterns(".historial", "__pycache__"))
        shutil.copytree(STUDIO / "templates", h / "templates")
    if not (h / "settings.json").exists():
        (h / "settings.json").write_text(json.dumps({"token": TOKEN, "port": PORT}))


def arrancar(h: Path, port=PORT):
    env = dict(os.environ, STUDIO_HOME=str(h), LANGSMITH_TRACING="false")
    log = open(h / "server.log", "ab")
    p = subprocess.Popen([PY_ESTUDIO, str(STUDIO / "server.py"), "--host", "127.0.0.1", "--port", str(port)],
                         env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return p
        except OSError:
            if p.poll() is not None:
                raise RuntimeError((h / "server.log").read_text())
            time.sleep(0.1)
    raise RuntimeError("el servidor no arrancó")


def parar(p, sig=signal.SIGTERM):
    if p.poll() is None:
        os.killpg(p.pid, sig)
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    h = tmp_path_factory.mktemp("srv") / "home"
    preparar_home(h)
    p = arrancar(h)
    yield h
    parar(p)


@pytest.fixture
def api(server):
    with httpx.Client(base_url=f"http://127.0.0.1:{PORT}", headers={"X-Token": TOKEN}, timeout=30) as c:
        yield c


def esperar_run(api, run_id, estados=("success", "error", "stopped", "drained", "interrupted"), timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = api.get(f"/api/runs/{run_id}").json()
        if r["status"] in estados:
            return r
        time.sleep(0.2)
    raise TimeoutError(r)
