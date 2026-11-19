"""Fase 2: módulos plantilla_texto, http_peticion, rss_noticias (T1) y trazas (A9), sin GPU."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import esperar_run
from test_f2_api import cadena, crear_modulo, estado, hilo, lanzar, lentos, proyecto

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Prueba</title>
<item><title>Primera &amp; noticia</title><link>http://x/1</link><description>&lt;p&gt;Texto uno&lt;/p&gt;</description>
<pubDate>Mon, 16 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Segunda</title><link>http://x/2</link><description>Texto dos</description></item>
<item><title>Tercera</title><link>http://x/3</link><description>Texto tres</description></item>
</channel></rss>"""
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
<entry><title>Atom uno</title><link href="http://a/1"/><summary>Resumen A</summary><updated>2026-11-16</updated></entry>
</feed>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/feed.xml":
            self._send(200, RSS, "application/rss+xml")
        elif self.path == "/atom.xml":
            self._send(200, ATOM, "application/atom+xml")
        elif self.path.startswith("/datos"):
            self._send(200, json.dumps({"ruta": self.path, "ok": True}))
        elif self.path == "/lento":
            time.sleep(3)
            self._send(200, "{}")
        else:
            self._send(404, "{}")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self._send(200, json.dumps({"recibido": json.loads(self.rfile.read(n) or b"null")}))


@pytest.fixture(scope="module")
def web():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def nodo(module, **cfg):
    return {"module": module, "x": 0, "y": 0, "config": cfg}


def correr(api, nombre, nodes, ids, **inp):
    p = proyecto(api, nombre, nodes, cadena(*ids), state_schema={k: {"input": True} for k in inp})
    tid = hilo(api, p["slug"])
    run = esperar_run(api, lanzar(api, tid, input=inp)["run_id"], timeout=30)
    return run, (estado(api, tid) if run["status"] == "success" else None), p


def test_t1_rss_y_plantilla(api, web):
    nodes = {"rss": nodo("rss_noticias.py", url="{base}/feed.xml", max=2),
             "pt": nodo("plantilla_texto.py", plantilla="Titulares de {fuente}:\n{noticias}", campo_salida="guion")}
    run, v, _ = correr(api, "RSS", nodes, ["rss", "pt"], base=web, fuente="Prueba")
    assert run["status"] == "success", run
    assert [n["titulo"] for n in v["noticias"]] == ["Primera & noticia", "Segunda"]
    assert v["noticias"][0]["resumen"] == "Texto uno" and v["noticias"][0]["enlace"] == "http://x/1"
    assert v["noticias_texto"].startswith("- Primera & noticia: Texto uno")
    assert v["guion"].startswith("Titulares de Prueba:\nPrimera & noticia · http://x/1")
    run, v, _ = correr(api, "Atom", {"rss": nodo("rss_noticias.py", url="{base}/atom.xml")}, ["rss"], base=web)
    assert v["noticias"] == [{"titulo": "Atom uno", "enlace": "http://a/1", "resumen": "Resumen A",
                              "fecha": "2026-11-16"}]


def test_t1_http(api, web):
    nodes = {"h": nodo("http_peticion.py", url="{base}/datos?q={q}", timeout=5)}
    run, v, _ = correr(api, "HTTP get", nodes, ["h"], base=web, q="hola")
    assert v["respuesta"] == {"ruta": "/datos?q=hola", "ok": True}
    nodes = {"h": nodo("http_peticion.py", metodo="POST", url="{base}/eco", cuerpo='{"n": {n}}', timeout=5,
                       campo_salida="eco")}
    run, v, _ = correr(api, "HTTP post", nodes, ["h"], base=web, n=7)
    assert v["eco"] == {"recibido": {"n": 7}}
    nodes = {"h": nodo("http_peticion.py", url="{base}/lento", timeout=1)}
    nodes["h"]["retry"] = {"max_attempts": 1}
    run, _, _ = correr(api, "HTTP lento", nodes, ["h"], base=web)
    assert run["status"] == "error" and "Timeout" in run["error"]
    # sin timeout → error de validación
    nodes = {"h": nodo("http_peticion.py", url="{base}/datos", timeout=0)}
    p = proyecto(api, "HTTP sin timeout", nodes, cadena("h"))
    errs = api.get(f"/api/projects/{p['slug']}/validate").json()["errores"]
    assert any("obligatorio" in e["msg"] for e in errs)


# ------------------------------------------------------------------------------------------------ A9
MOD_GPU = '''"""Duerme con la GPU exclusiva (para probar la espera de GPU)."""
import time
META = {"name": "Lento GPU", "kind": "task", "exec": "proceso", "interprete": "estudio", "gpu": "exclusive",
        "inputs": [], "outputs": [], "outputs_from_config": ["clave"]}
CONFIG = [{"key": "clave", "type": "state_key", "default": "g"}, {"key": "segundos", "type": "number", "default": 1}]
def run(state, cfg, ctx):
    time.sleep(float(cfg["segundos"]))
    return {cfg["clave"]: "ok"}
'''


def test_a9_trazas(api):
    mod = crear_modulo(api, "Lento GPU", MOD_GPU)
    nodes = {"g1": nodo(mod, clave="k_g1"), "g2": nodo(mod, clave="k_g2"),
             **lentos(("f", 0.1, {"fallar": True}), ("c", 0.3), ("fin", 0.1))}
    nodes["f"]["retry"] = {"max_attempts": 2, "initial_interval": 0.05, "jitter": False}
    nodes["f"]["error_to"] = "fin"
    nodes["c"]["cache"] = {"ttl": 60}
    edges = [{"id": f"s{n}", "src": "__start__", "dst": n} for n in ("g1", "g2", "f", "c")]
    edges += [{"id": f"t{n}", "src": n, "dst": "__end__"} for n in ("g1", "g2", "c", "fin")]
    p = proyecto(api, "Trazas", nodes, edges)
    esperar_run(api, lanzar(api, hilo(api, p["slug"]), input={})["run_id"], timeout=60)
    tid = hilo(api, p["slug"])
    r = lanzar(api, tid, input={})
    run = esperar_run(api, r["run_id"], timeout=60)
    assert run["status"] == "success", run
    tr = api.get(f"/api/runs/{r['run_id']}/trace").json()
    sp = tr["spans"]
    por = {}
    for s in sp:
        por.setdefault(s["node"], []).append(s)
    assert len(por["g1"]) == len(por["g2"]) == len(por["c"]) == len(por["fin"]) == 1
    assert [s["attempt"] for s in por["f"]] == [1, 2] and all(s["status"] == "error" for s in por["f"])
    assert por["c"][0]["cached"] and por["c"][0]["status"] == "cached"
    g1, g2 = por["g1"][0], por["g2"][0]
    esperó = g1 if g1["t_gpu"] else g2
    otro = g2 if esperó is g1 else g1
    assert esperó["t_gpu"] and esperó["t_gpu"] > esperó["t0"] and otro["t_gpu"] is None
    assert esperó["t_gpu"] >= otro["t1"] - 0.5
    assert g1["t0"] < g2["t1"] and g2["t0"] < g1["t1"]        # solapados
    t = tr["totals"]
    assert t["n_spans"] == len(sp) and t["errores"] == 2 and t["reintentos"] == 1 and t["espera_gpu"] > 0.5
    assert abs(t["duration"] - (run["t1"] - run["t0"])) < 0.5
    filtrados = api.get("/api/runs", params={"project": p["slug"], "status": "success,error"}).json()
    assert len(filtrados) == 2 and all(x["project"] == p["slug"] for x in filtrados)
    assert api.get("/api/runs", params={"project": p["slug"], "since": time.time() + 10}).json() == []
    assert api.get("/api/runs/nada/trace").status_code == 404
