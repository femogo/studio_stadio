"""Paso 1b: modules.py (AST, validación, instantáneas) y protocolo de runner.py."""
import json
import os
import subprocess
from pathlib import Path

import pytest
from conftest import PY_ESTUDIO, PY_PESADO, STUDIO

from modules import ConflictError, ModuloError, Registry, keys_from_value, parse_source

OK = 'META = {"name": "X", "inputs": ["a"], "outputs": ["b"]}\nCONFIG = []\ndef run(state, cfg, ctx):\n    return {"b": 1}\n'


def test_parse_ok_y_defaults():
    meta, cfg = parse_source(OK)
    assert meta["kind"] == "task" and meta["exec"] == "local" and meta["gpu"] is False and cfg == []


@pytest.mark.parametrize("src,msg,line", [
    ("def run(:\n", "sintaxis", 1),
    ("META = dict(a=1)\ndef run(s,c,x): pass\n", "literal", 1),
    ('META = {"name": "x"}\n', "run", 1),
    ('META = {"kind": "raro"}\ndef run(s,c,x): pass\n', "kind", 1),
    ('META = {"exec": "remoto"}\ndef run(s,c,x): pass\n', "exec", 1),
    ('META = {"gpu": "mucha"}\ndef run(s,c,x): pass\n', "gpu", 1),
    ('import os\nMETA = {}\ndef run(s,c,x):\n    import torch\n', "torch", 4),
    ('META = {}\nCONFIG = [{"key": "a", "type": "color"}]\ndef run(s,c,x): pass\n', "Tipo de campo", 1),
])
def test_validacion_dura(src, msg, line):
    with pytest.raises(ModuloError) as e:
        parse_source(src)
    assert msg in str(e.value) and e.value.line == line


def test_import_torch_permitido_en_proceso():
    parse_source('META = {"exec": "proceso", "interprete": "pesado"}\ndef run(s,c,x):\n    import torch\n')


def test_keys_from_value():
    assert keys_from_value("a, b,") == ["a", "b"]
    assert keys_from_value({"x": 1, "y": 2}) == ["x", "y"]
    assert keys_from_value([{"clave": "v", "como": "video"}, "t"]) == ["v", "t"]
    assert keys_from_value(None) == []


def test_registry_guardar_historial_conflicto_instantanea(home):
    reg = Registry(home / "modules", home / "data/modsnap")
    mods = reg.all()
    assert {"entrada.py", "salida.py", "codigo_python.py", "prueba_lenta.py"} <= set(mods)
    assert all(m.error is None for m in mods.values()), {k: m.error for k, m in mods.items() if m.error}
    f = reg.create("Mi módulo")
    assert f == "mi_modulo.py" and reg.get(f).error is None
    info1, mod1 = reg.load(f)
    with pytest.raises(ModuloError) as e:
        reg.save(f, "def run(:\n", info1.sha)
    assert e.value.line == 1
    with pytest.raises(ConflictError):
        reg.save(f, OK, "sha-viejo")
    info2 = reg.save(f, OK, info1.sha)
    assert info2.sha != info1.sha and len(reg.history(f)) == 1
    _, mod2 = reg.load(f)
    assert mod2 is not mod1 and mod2.__name__ == f"m_{info2.sha}"
    assert (home / f"data/modsnap/{info2.sha}.py").exists()
    reg.restore(f, reg.history(f)[0]["ts"])
    assert reg.get(f).sha == info1.sha
    dup = reg.create("Copia", from_file=f)
    assert '"name": "Copia"' in reg.get(dup).source
    reg.delete(dup)
    assert reg.get(dup) is None


def lanzar(py, mod, tmp_path, state, cfg, home=None):
    d = tmp_path / "t"
    d.mkdir(exist_ok=True)
    (d / "_in.json").write_text(json.dumps({"state": state, "cfg": cfg, "settings": {}, "node": "n1",
                                            "thread_id": "th", "run_id": "r", "context": {}}))
    env = dict(os.environ)
    if home:
        env["STUDIO_HOME"] = str(home)
    p = subprocess.run([py, "-u", str(STUDIO / "runner.py"), str(mod), str(d), "n1"], capture_output=True, text=True,
                       env=env, cwd=str(STUDIO.parent), timeout=60)
    return p.returncode, p.stdout.splitlines()


@pytest.mark.parametrize("py", [PY_ESTUDIO, PY_PESADO])
def test_runner_protocolo(tmp_path, home, py):
    rc, lines = lanzar(py, home / "modules/prueba_lenta.py", tmp_path, {}, {"segundos": 0.3, "clave_salida": "z"}, home)
    assert rc == 0
    assert any(l.startswith("@@P ") for l in lines)
    r = [l for l in lines if l.startswith("@@R ")]
    assert len(r) == 1 and json.loads(r[0][4:]) == {"z": "n1 ok"}
    assert (home / "data/gpu.lock").exists() is False   # gpu False → sin lock


def test_runner_errores(tmp_path, home):
    rc, lines = lanzar(PY_ESTUDIO, home / "modules/prueba_lenta.py", tmp_path, {}, {"segundos": 0.1, "fallar": True})
    assert rc == 1 and any("Fallo pedido" in l for l in lines) and not any(l.startswith("@@R") for l in lines)
    m = tmp_path / "m.py"
    m.write_text('META={"exec":"proceso"}\ndef run(s,c,x):\n    return [1]\n')
    rc, lines = lanzar(PY_ESTUDIO, m, tmp_path, {}, {})
    assert rc == 1 and any("debe devolver un dict" in l for l in lines)
    m.write_text('import sys\nMETA={"exec":"proceso"}\ndef run(s,c,x):\n    x.log("@@R falso")\n    sys.exit()\n')
    rc, lines = lanzar(PY_ESTUDIO, m, tmp_path, {}, {})
    assert rc == 0 and [l for l in lines if l.startswith("@@R")] == ["@@R {}"]
    m.write_text('META={"exec":"proceso"}\ndef run(s,c,x):\n    return {"a": object()}\n')
    rc, lines = lanzar(PY_ESTUDIO, m, tmp_path, {}, {})
    assert rc == 1 and any("serializable" in l for l in lines)


def test_codigo_python(tmp_path, home):
    rc, lines = lanzar(PY_ESTUDIO, home / "modules/codigo_python.py", tmp_path, {"texto": "hola"},
                       {"codigo": 'ctx.log("x")\nreturn {"resultado": len(state["texto"])}'})
    assert rc == 0 and "@@R {\"resultado\": 4}" in lines


def test_gpu_lock_exclusivo(tmp_path, home):
    m = tmp_path / "g.py"
    m.write_text('META={"exec":"proceso","gpu":"exclusive"}\ndef run(s,c,x):\n    return {"ok": 1}\n')
    rc, _ = lanzar(PY_ESTUDIO, m, tmp_path, {}, {}, home)
    assert rc == 0 and (home / "data/gpu.lock").exists()
