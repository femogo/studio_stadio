"""Paso 3i: GUI de F3 con Playwright (T3: rutas F3 sin errores ni scroll; Datasets, Programaciones, Prompt, Exportar, MCP)."""
import time

import pytest
from conftest import esperar_run
from test_f2_api import cadena, crear_modulo, hilo, lanzar, proyecto
from test_f3_api import MOD_LLM_FALSO, esperar_exp, proyecto_longitud
from test_ui import URL, browser, pagina  # noqa: F401  (fixture)

pytestmark = pytest.mark.ui


def preparar(api):
    p = proyecto_longitud(api, "UI longitud")
    did = api.post("/api/datasets", json={"name": f"UI ds {p['slug']}", "project": p["slug"]}).json()["dataset_id"]
    api.post(f"/api/datasets/{did}/examples", json=[{"input": {"texto": t}, "expected": {"longitud": len(t)}}
                                                     for t in ("a", "bb", "ccc")])
    ev = [{"name": "igual", "type": "expr", "expr": "output.get('longitud') == expected.get('longitud')"}]
    xa = esperar_exp(api, api.post("/api/experiments", json={"dataset_id": did, "project": p["slug"],
                                                              "evaluators": ev}).json()["experiment_id"])
    xb = esperar_exp(api, api.post("/api/experiments", json={"dataset_id": did, "project": p["slug"],
                                                              "evaluators": ev}).json()["experiment_id"])
    api.post("/api/crons", json={"project": p["slug"], "name": "UI cron", "cron": "0 3 * * *", "active": False})
    mod = crear_modulo(api, "LLM falso UI", MOD_LLM_FALSO)
    pl = proyecto(api, "UI prompt", {"llm": {"module": mod, "x": 100, "y": 100, "config": {"prompt": "Hola {nombre}"}}},
                  cadena("llm"), state_schema={"nombre": {"input": True}})
    tl = hilo(api, pl["slug"])
    esperar_run(api, lanzar(api, tl, input={"nombre": "Ana"})["run_id"])
    tp = hilo(api, p["slug"])
    run = esperar_run(api, lanzar(api, tp, input={"texto": "hola"})["run_id"])
    return {"p": p["slug"], "did": did, "xa": xa["experiment_id"], "xb": xb["experiment_id"], "pl": (pl["slug"], tl),
            "run": run["run_id"]}


async def test_rutas_f3_sin_errores(api, browser):  # noqa: F811
    d = preparar(api)
    rutas = ["datasets", f"datasets/{d['did']}", f"datasets/{d['did']}?tab=experimentos", "programaciones", "config",
             f"estudio/{d['pl'][0]}?hilo={d['pl'][1]}", "hilos", f"trazas?run={d['run']}"]
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


async def test_interacciones_f3(api, browser):  # noqa: F811
    d = preparar(api)
    pg = await pagina(browser)
    # Datasets: crear ejemplo, ver experimento y comparar
    await pg.goto(f"{URL}/#/datasets/{d['did']}")
    await pg.wait_for_selector("tr[data-ex]")
    await pg.click("#nuevoEjemplo")
    await pg.fill("#ejInput", '{"texto": "dddd"}')
    await pg.fill("#ejExpected", '{"longitud": 4}')
    await pg.click(".modal footer button.primary")
    await pg.wait_for_function("document.querySelectorAll('tr[data-ex]').length === 4")
    await pg.click("button[data-dtab=experimentos]")
    await pg.wait_for_selector(f"tr[data-exp='{d['xa']}']")
    await pg.click(f"tr[data-exp='{d['xa']}'] input[type=checkbox]")
    await pg.click(f"tr[data-exp='{d['xb']}'] input[type=checkbox]")
    await pg.click("#compararBtn")
    await pg.wait_for_selector("#tablaComparar tbody tr")
    assert await pg.locator("#tablaComparar tbody tr").count() == 3
    await pg.click(".modal footer button")
    # lanzar un experimento desde la GUI
    await pg.click("#nuevoExperimento")
    await pg.wait_for_selector("#expProyecto")
    await pg.click(".modal footer button.primary")
    await pg.wait_for_timeout(500)
    xs = api.get(f"/api/experiments?dataset={d['did']}").json()
    assert len(xs) == 3
    esperar_exp(api, xs[0]["experiment_id"])
    if await pg.locator(".modal").count():
        await pg.click(".modal footer button")
    # Trazas: añadir la ejecución al dataset
    await pg.goto(f"{URL}/#/trazas?run={d['run']}")
    await pg.wait_for_selector(f"tr[data-run='{d['run']}']")
    await pg.click(f"tr[data-run='{d['run']}'] button:has-text('Dataset')")
    await pg.select_option("#dsDestino", d["did"])
    await pg.click(".modal footer button.primary")
    await pg.wait_for_timeout(500)
    assert len(api.get(f"/api/datasets/{d['did']}/examples").json()) == 5
    # Programaciones: crear con vista previa y activar
    await pg.goto(f"{URL}/#/programaciones")
    await pg.wait_for_selector("tr[data-cron]")
    await pg.click("#nuevaProg")
    await pg.wait_for_selector("#cronExpr")
    await pg.fill("#cronExpr", "*/10 * * * *")
    await pg.wait_for_function("document.getElementById('cronPrev').textContent.startsWith('Próximas')")
    await pg.fill("#cronExpr", "esto no")
    await pg.wait_for_function("!document.getElementById('cronPrev').textContent.startsWith('Próximas')")
    await pg.fill("#cronExpr", "*/10 * * * *")
    await pg.click(".modal footer button.primary")
    await pg.wait_for_function("document.querySelectorAll('tr[data-cron]').length === 2")
    cs = api.get("/api/crons").json()
    assert any(c["cron"] == "*/10 * * * *" and c["active"] for c in cs)
    for c in cs:
        api.delete(f"/api/crons/{c['cron_id']}")
    # Estudio: pestaña Prompt con 2 variantes y guardar en el nodo
    slug, tl = d["pl"]
    await pg.goto(f"{URL}/#/estudio/{slug}?hilo={tl}")
    await pg.wait_for_timeout(800)
    await pg.click(".gnode[data-node=llm]")
    await pg.click(".rpanel .tabs button:has-text('Prompt')")
    await pg.wait_for_selector("#pgProbar")
    await pg.click("button:has-text('+ Variante')")
    tas = pg.locator(".pgrid .pcol textarea")
    await tas.nth(2).fill("Adiós {nombre}")
    await pg.click("#pgProbar")
    await pg.wait_for_selector("#pgRes .pgout")
    txt = await pg.inner_text("#pgRes")
    assert "HOLA ANA" in txt and "ADIÓS ANA" in txt, txt
    await pg.locator(".pgrid .pcol").nth(1).locator("button:has-text('Guardar en el nodo')").click()
    await pg.wait_for_timeout(1500)
    assert api.get(f"/api/projects/{slug}").json()["nodes"]["llm"]["config"]["prompt"] == "Adiós {nombre}"
    # Exportar ▾
    await pg.click("#exportarBtn")
    await pg.wait_for_selector("#expPy")
    async with pg.expect_download() as dl:
        await pg.click("#expPy")
    assert (await dl.value).suggested_filename == f"export_{slug}.zip"
    await pg.click(".modal footer button")
    # Configuración: sección MCP sin servidores
    await pg.goto(f"{URL}/#/config")
    await pg.wait_for_selector("#mcpEstado")
    await pg.wait_for_function("!document.getElementById('mcpEstado').textContent.includes('Comprobando')")
    assert "No hay servidores MCP" in await pg.inner_text("#mcpEstado")
    time.sleep(0.2)
    assert not pg.errores, pg.errores
    await pg.context.close()


async def test_inicio_proyectos_y_agentes(api, browser):  # noqa: F811
    p = proyecto(api, "UI inicio flujo", {}, [])
    a = api.post(f"/api/projects/{p['slug']}/assistants", json={"name": "Agente UI"}).json()
    todos = api.get("/api/assistants").json()
    assert todos[0]["assistant_id"] == a["assistant_id"] and todos[0]["project_name"] == "UI inicio flujo"
    pg = await pagina(browser)
    await pg.goto(f"{URL}/#/inicio")
    await pg.wait_for_selector("#inicioAgentes")
    # proyectos primero, agentes debajo; sin nada de vídeo en la portada
    orden = await pg.evaluate("[...document.querySelectorAll('.sec h3')].map(x => x.textContent)")
    assert orden[:2] == ["Flujos", "Agentes"], orden  # D9
    assert await pg.locator("#cardVideo").count() == 0
    assert "vídeo" not in (await pg.inner_text("#main")).lower()
    assert await pg.locator(f"#inicioProyectos .card[data-p='{p['slug']}']").count() == 1
    # nuevo agente
    await pg.click("#nuevoAgenteBtn")
    await pg.select_option("#naProyecto", p["slug"])
    await pg.fill("#naNombre", "Otro agente")
    await pg.click(".modal footer button.primary")
    await pg.wait_for_selector(".alist .it:has-text('Otro agente')")
    assert len(api.get(f"/api/projects/{p['slug']}/assistants").json()) == 2
    # clic en un agente → Estudio con ese asistente seleccionado
    await pg.goto(f"{URL}/#/inicio")
    await pg.click(f"#inicioAgentes .card[data-a='{a['assistant_id']}'] h4")
    await pg.wait_for_timeout(800)
    assert f"#/estudio/{p['slug']}" in pg.url
    # nuevo proyecto (plantilla vacía)
    await pg.goto(f"{URL}/#/inicio")
    await pg.click("#nuevoProyectoBtn")
    await pg.fill("#npNombre", "Flujo desde inicio")
    await pg.click(".modal footer button.primary")
    await pg.wait_for_timeout(800)
    assert "#/estudio/flujo-desde-inicio" in pg.url
    # Salidas
    await pg.goto(f"{URL}/#/salidas?tipo=text")
    await pg.wait_for_selector("#salidaTipo button.on")
    assert await pg.locator("#salidaTipo button.on").get_attribute("data-tipo") == "text"
    assert not pg.errores, pg.errores
    await pg.context.close()
