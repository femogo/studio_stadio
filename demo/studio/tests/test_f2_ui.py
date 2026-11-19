"""Paso 2i: GUI de F2 con Playwright (T3: rutas F2 sin errores ni scroll; Asistentes, Memoria, Trazas, Chat, xray)."""
import pytest
from conftest import esperar_run
from test_f2_api import MOD_CHAT_FALSO, cadena, crear_modulo, hilo, lanzar, lentos, proyecto
from test_ui import URL, browser, pagina  # noqa: F401  (fixture)

pytestmark = pytest.mark.ui


def preparar(api):
    """Datos para recorrer todas las vistas F2."""
    mod = crear_modulo(api, "Chat falso", MOD_CHAT_FALSO)
    chat = proyecto(api, "UI chat", {"chat": {"module": mod, "x": 100, "y": 100}}, cadena("chat"),
                    state_schema={"messages": {"reducer": "messages", "input": True, "output": True}})
    tc = hilo(api, chat["slug"])
    esperar_run(api, lanzar(api, tc, input={"messages": [{"role": "user", "content": "hola"}]})["run_id"])
    hijo = proyecto(api, "UI hijo", lentos(("x", 0.1), ("y", 0.1)), cadena("x", "y"))
    padre = proyecto(api, "UI padre", {"sub": {"module": "subgrafo.py", "x": 100, "y": 100,
                                               "config": {"proyecto": hijo["slug"]}},
                                       **lentos(("z", 0.1))}, cadena("sub", "z"))
    nodes = lentos(("a", 0.1), ("b", 0.1))
    nodes["a"]["cache"] = {"ttl": 60}
    p = proyecto(api, "UI f2", nodes, cadena("a", "b"),
                 context_schema=[{"key": "tono", "label": "Tono", "type": "text", "default": "neutro"}])
    tp = hilo(api, p["slug"])
    r = esperar_run(api, lanzar(api, tp, input={})["run_id"])
    tpa = hilo(api, padre["slug"])
    esperar_run(api, lanzar(api, tpa, input={})["run_id"])
    api.post(f"/api/projects/{p['slug']}/assistants", json={"name": "Serio", "context": {"tono": "serio"}})
    api.put("/api/store/items", json={"namespace": ["ui", "ana"], "key": "k1", "value": {"texto": "hola"}, "index": False})
    return {"chat": (chat["slug"], tc), "padre": (padre["slug"], tpa), "p": (p["slug"], tp), "run": r["run_id"]}


async def test_rutas_f2_sin_errores(api, browser):  # noqa: F811
    d = preparar(api)
    rutas = ["asistentes", f"asistentes?proyecto={d['p'][0]}", "memoria", "memoria?ns=ui%2Fana", "trazas",
             f"trazas?run={d['run']}", f"estudio/{d['chat'][0]}?hilo={d['chat'][1]}",
             f"estudio/{d['padre'][0]}?hilo={d['padre'][1]}", f"estudio/{d['p'][0]}?hilo={d['p'][1]}"]
    for w, hh in ((1440, 900), (1024, 768)):
        pg = await pagina(browser, w, hh)
        for r in rutas:
            await pg.goto(f"{URL}/#/{r}")
            await pg.wait_for_timeout(900)
            hs = await pg.evaluate("document.documentElement.scrollWidth > window.innerWidth || "
                                   "document.body.scrollWidth > window.innerWidth")
            assert not hs, f"scroll horizontal en {r} a {w}x{hh}"
            assert not pg.errores, f"{r} a {w}x{hh}: {pg.errores}"
        await pg.context.close()


async def test_interacciones_f2(api, browser):  # noqa: F811
    d = preparar(api)
    pg = await pagina(browser)
    # Asistentes: crear con el formulario del context_schema
    await pg.goto(f"{URL}/#/asistentes?proyecto={d['p'][0]}")
    await pg.wait_for_selector(".alist .it")
    await pg.click("#nuevoAsistente")
    await pg.fill("#asNombre", "Desde la GUI")
    await pg.fill("#asOverrides", '{"a": {"segundos": 0.2}}')
    await pg.click(".modal footer button.primary")
    await pg.wait_for_selector(".alist .it:has-text('Desde la GUI')")
    assert len(api.get(f"/api/projects/{d['p'][0]}/assistants").json()) == 2
    # Memoria
    await pg.goto(f"{URL}/#/memoria?ns=ui%2Fana")
    await pg.wait_for_selector("tr[data-key=k1]")
    # Trazas: cascada y log del nodo
    await pg.goto(f"{URL}/#/trazas?run={d['run']}")
    await pg.wait_for_selector(".wrow")
    await pg.click(".wrow[data-span=a]")
    await pg.wait_for_timeout(400)
    assert "Durmiendo" in await pg.inner_text("#trazaDet")
    # Estudio: ⚡ tras una ejecución cacheada, re-ejecutar nodo y xray
    slug, tid = d["p"]
    esperar_run(api, lanzar(api, hilo(api, slug), input={})["run_id"])
    await pg.goto(f"{URL}/#/estudio/{slug}")
    await pg.wait_for_timeout(800)
    assert await pg.locator(".cachedmark").count() == 1
    await pg.goto(f"{URL}/#/estudio/{slug}?hilo={tid}")
    await pg.wait_for_timeout(800)
    await pg.click(".gnode[data-node=b]")
    await pg.click("#rerunBtn")
    await pg.wait_for_timeout(2500)
    runs = api.get(f"/api/runs?thread={tid}").json()
    assert runs[0]["kind"] == "replay" and runs[0]["status"] == "success"
    ps, tpa = d["padre"]
    await pg.goto(f"{URL}/#/estudio/{ps}?hilo={tpa}")
    await pg.wait_for_timeout(800)
    if not await pg.evaluate("document.getElementById('xrayBtn').classList.contains('primary')"):
        await pg.click("#xrayBtn")
    await pg.wait_for_timeout(300)
    assert await pg.locator(".gnode[data-node=sub] .xray rect.success").count() == 2
    # Chat: enviar un mensaje y ver la respuesta
    cs, tc = d["chat"]
    await pg.goto(f"{URL}/#/estudio/{cs}?hilo={tc}")
    await pg.wait_for_timeout(800)
    if await pg.locator(".chat.hidden").count():
        await pg.click("#modoChat")
    await pg.wait_for_selector("#chatIn")
    await pg.fill("#chatIn", "segunda")
    await pg.click("#chatSend")
    await pg.wait_for_function("document.querySelectorAll('.chat .msg.assistant:not(.live)').length >= 2", timeout=10000)
    assert await pg.locator(".chat .msg.user").count() == 2
    assert not pg.errores, pg.errores
    await pg.context.close()
