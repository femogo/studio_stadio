"""Pasos 1g–1h: GUI con Playwright (Chromium existente). Criterios 7, 8, 9 y 11."""
import asyncio
import time
from pathlib import Path

import pytest
from conftest import PORT, TOKEN, esperar_run

pytestmark = pytest.mark.ui
CH = "/root/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell"
URL = f"http://127.0.0.1:{PORT}"


@pytest.fixture
async def browser():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CH if Path(CH).exists() else None)  # si no, el de `playwright install`
        yield b
        await b.close()


async def pagina(browser, w=1440, hh=900, token=True):
    ctx = await browser.new_context(viewport={"width": w, "height": hh})
    if token:
        await ctx.add_cookies([{"name": "studio_token", "value": TOKEN, "url": URL}])
    pg = await ctx.new_page()
    pg.errores = []
    pg.on("console", lambda m: m.type == "error" and pg.errores.append(m.text))
    pg.on("pageerror", lambda e: pg.errores.append(str(e)))
    return pg


async def test_rutas_sin_errores_ni_scroll(api, browser):
    p = api.post("/api/projects", json={"name": "UI rutas", "template": "clip_personaje"}).json()
    tid = api.post("/api/threads", json={"project": p["slug"]}).json()["thread_id"]
    v = api.post("/api/projects", json={"name": "UI vacío", "template": "vacio"}).json()
    t2 = api.post("/api/threads", json={"project": v["slug"]}).json()["thread_id"]
    esperar_run(api, api.post(f"/api/threads/{t2}/runs", json={"input": {"texto": "hola"}}).json()["run_id"])
    rutas = ["inicio", f"estudio/{p['slug']}?hilo={tid}", f"estudio/{v['slug']}?hilo={t2}", "estudio", "hilos", "salidas", "videos",
             "proyectos", "modulos", "modulos/llm_ollama.py", "config"]
    for w, hh in ((1440, 900), (1024, 768)):
        pg = await pagina(browser, w, hh)
        for r in rutas:
            await pg.goto(f"{URL}/#/{r}")
            await pg.wait_for_timeout(900)
            if r.startswith("estudio/"):
                for tab in ("historia", "estado", "interrupts", "eventos", "salida"):
                    await pg.click(f"[data-btab={tab}]")
                    await pg.wait_for_timeout(150)
                await pg.click(".gnode:not(.term)")
                for tab in ("Proceso", "Estado", "Código", "Configuración"):
                    await pg.click(f".rpanel .tabs button:has-text('{tab}')")
                    await pg.wait_for_timeout(200)
            hs = await pg.evaluate("document.documentElement.scrollWidth > window.innerWidth || "
                                   "document.body.scrollWidth > window.innerWidth")
            assert not hs, f"scroll horizontal en {r} a {w}x{hh}"
            assert not pg.errores, f"{r} a {w}x{hh}: {pg.errores}"
        # diálogos principales
        await pg.goto(f"{URL}/#/inicio")
        await pg.click("#nuevoProyectoBtn")                     # la plantilla de vídeo abre su asistente
        await pg.click(".modal .card[data-t=clip_personaje]")
        await pg.click(".modal footer button.primary")
        await pg.wait_for_selector("#qvProyecto")
        await pg.keyboard.press("Escape")
        await pg.click("#menuToggle")
        await pg.wait_for_timeout(300)
        assert await pg.evaluate("document.getElementById('app').classList.contains('menu-c')")
        await pg.click("#menuToggle")
        assert not pg.errores, pg.errores
        await pg.context.close()


async def test_token_en_la_gui(browser):
    pg = await pagina(browser, token=False)
    await pg.goto(f"{URL}/#/proyectos")
    await pg.wait_for_selector(".modal input[type=password]")
    await pg.fill(".modal input[type=password]", TOKEN)
    await pg.keyboard.press("Enter")
    await pg.wait_for_selector("h2:has-text('Flujos')")  # D9: #/proyectos → #/flujos
    await pg.wait_for_timeout(800)
    assert await pg.locator(".card").count() >= 1
    await pg.context.close()


async def centro(pg, sel):
    b = await pg.locator(sel).bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


async def test_lienzo_editar_persistir_deshacer(api, browser):
    p = api.post("/api/projects", json={"name": "Lienzo", "template": "vacio"}).json()
    pg = await pagina(browser)
    await pg.goto(f"{URL}/#/estudio/{p['slug']}")
    await pg.wait_for_selector("[data-node=entrada]")
    # añadir un módulo desde la paleta
    await pg.click("text=+ Añadir módulo")
    await pg.click(".palette [data-mod='prueba_lenta.py']")
    await pg.wait_for_selector("[data-node=prueba_lenta]")
    # mover 'salida'
    x, y = await centro(pg, "[data-node=salida] .box")
    await pg.mouse.move(x, y)
    await pg.mouse.down()
    await pg.mouse.move(x + 40, y - 100, steps=8)
    await pg.mouse.up()
    # conectar entrada → prueba_lenta arrastrando de la salida a la entrada
    x1, y1 = await centro(pg, "[data-node=entrada] .port.out")
    x2, y2 = await centro(pg, "[data-node=prueba_lenta] .box")
    await pg.mouse.move(x1, y1)
    await pg.mouse.down()
    await pg.mouse.move(x2, y2, steps=10)
    await pg.mouse.up()
    await pg.wait_for_selector(".rpanel:not(.c) >> text=Arista")
    # copiar/pegar prueba_lenta y deshacer el pegado
    x, y = await centro(pg, "[data-node=prueba_lenta] .box")
    await pg.mouse.click(x, y)
    await pg.keyboard.press("Control+c")
    await pg.keyboard.press("Control+v")
    await pg.wait_for_selector("[data-node=prueba_lenta_2]")
    await pg.mouse.click(5 + (await pg.locator("svg.canvas").bounding_box())["x"], 300)
    await pg.keyboard.press("Control+z")
    await pg.wait_for_timeout(200)
    assert await pg.locator("[data-node=prueba_lenta_2]").count() == 0
    # borrar la arista entrada→salida (clic + Supr)
    await pg.locator("[data-edge=e2] .gedge-hit").first.dispatch_event("mousedown", {"button": 0})
    await pg.keyboard.press("Delete")
    await pg.wait_for_timeout(1500)                      # autoguardado (800 ms)
    assert not pg.errores, pg.errores
    await pg.reload()
    await pg.wait_for_selector("[data-node=prueba_lenta]")
    pr = api.get(f"/api/projects/{p['slug']}").json()
    assert "prueba_lenta" in pr["nodes"] and "prueba_lenta_2" not in pr["nodes"]
    assert pr["nodes"]["salida"]["x"] != 340 or pr["nodes"]["salida"]["y"] != 160
    assert any(e["src"] == "entrada" and e["dst"] == "prueba_lenta" for e in pr["edges"])
    assert not any(e["id"] == "e2" for e in pr["edges"])
    assert await pg.locator("[data-node=prueba_lenta]").count() == 1
    await pg.context.close()


async def test_editor_de_modulo_422_y_codigo_nuevo_sin_reiniciar(api, browser):
    f = api.post("/api/modules", json={"name": "Versionado"}).json()["file"]
    pg = await pagina(browser)
    await pg.goto(f"{URL}/#/modulos/{f}")
    await pg.wait_for_selector(".CodeMirror")
    await pg.wait_for_timeout(300)
    src = api.get(f"/api/modules/{f}/source").json()["source"]
    malo = src.replace("def run(state, cfg, ctx):", "def run(state, cfg, ctx)\n")
    await pg.evaluate("s => document.querySelector('.CodeMirror').CodeMirror.setValue(s)", malo)
    await pg.click(".CodeMirror")
    await pg.keyboard.press("Control+s")
    await pg.wait_for_selector(".toast.err >> text=línea")
    assert await pg.locator(".cm-errline").count() == 1
    bueno = src.replace('return {"resultado": cfg["prefijo"] + texto}', 'return {"resultado": "codigo-v2"}')
    await pg.evaluate("s => document.querySelector('.CodeMirror').CodeMirror.setValue(s)", bueno)
    await pg.keyboard.press("Control+s")
    await pg.wait_for_selector(".toast.ok")
    assert await pg.locator(".cm-errline").count() == 0
    await pg.context.close()
    p = api.post("/api/projects", json={"name": "Usa versionado", "template": "vacio"}).json()
    p["nodes"]["v"] = {"module": f, "x": 200, "y": 300, "config": {}}
    p["edges"] = [{"id": "e1", "src": "__start__", "dst": "entrada"}, {"id": "e2", "src": "entrada", "dst": "v"},
                  {"id": "e3", "src": "v", "dst": "__end__"}]
    api.put(f"/api/projects/{p['slug']}", json=p)
    tid = api.post("/api/threads", json={"project": p["slug"]}).json()["thread_id"]
    r = esperar_run(api, api.post(f"/api/threads/{tid}/runs", json={"input": {}}).json()["run_id"])
    assert r["status"] == "success"
    assert api.get(f"/api/threads/{tid}/state").json()["values"]["resultado"] == "codigo-v2"


HUMANO = "revision_humana.py"


async def test_responder_interrupt_desde_la_gui(api, browser):
    p = api.post("/api/projects", json={"name": "Revisión UI", "template": "vacio"}).json()
    p["nodes"]["rev"] = {"module": HUMANO, "x": 200, "y": 300, "config": {"campo": "texto"}}
    p["edges"] = [{"id": "e1", "src": "__start__", "dst": "entrada"}, {"id": "e2", "src": "entrada", "dst": "rev"},
                  {"id": "e3", "src": "rev", "dst": "salida"}, {"id": "e4", "src": "salida", "dst": "__end__"}]
    api.put(f"/api/projects/{p['slug']}", json=p)
    pg = await pagina(browser)
    await pg.goto(f"{URL}/#/estudio/{p['slug']}")
    await pg.wait_for_selector("[data-node=rev]")
    await pg.click("text=▶ Iniciar")
    await pg.fill("#runInput", '{"texto": "borrador"}')
    await pg.click(".modal footer button.primary")
    await pg.wait_for_selector(".iform", timeout=15000)       # se abre sola la pestaña Interrupts
    await pg.fill(".iform textarea", "texto revisado")
    await pg.click(".iform button.primary")
    tid = None
    for _ in range(50):
        ths = api.get(f"/api/threads?project={p['slug']}").json()
        if ths and ths[0]["ultimo_run"] and ths[0]["ultimo_run"]["status"] == "success":
            tid = ths[0]["thread_id"]
            break
        await asyncio.sleep(0.2)
    assert tid and api.get(f"/api/threads/{tid}/state").json()["values"]["texto"] == "texto revisado"
    assert not pg.errores, pg.errores
    await pg.context.close()
