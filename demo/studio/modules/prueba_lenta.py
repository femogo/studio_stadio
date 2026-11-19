"""Prueba lenta: duerme emitiendo progreso. Para probar pausa, parada y timeouts sin GPU."""
import sys
import time

META = {
    "name": "Prueba lenta", "category": "Utilidades", "icon": "⏱", "kind": "task", "exec": "proceso",
    "interprete": "estudio", "gpu": False, "desc": "Duerme N segundos con progreso cada 0,1 s.",
    "inputs": [], "outputs": [], "outputs_from_config": ["clave_salida"], "weight": 1,
}

CONFIG = [
    {"key": "segundos", "label": "Segundos", "type": "number", "default": 10},
    {"key": "fallar", "label": "Fallar al final", "type": "checkbox", "default": False},
    {"key": "clave_salida", "label": "Clave de salida", "type": "state_key", "default": "prueba"},
]


def run(state, cfg, ctx):
    seg = float(cfg.get("segundos") or 0)
    n = max(1, int(seg * 10))
    ctx.log(f"Durmiendo {seg} s")
    for i in range(n):
        time.sleep(seg / n)
        ctx.progress((i + 1) / n)
    if cfg.get("fallar"):
        sys.exit("Fallo pedido por la configuración")
    return {cfg.get("clave_salida") or "prueba": f"{ctx.node_id} ok"}
