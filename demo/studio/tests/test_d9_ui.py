"""D9 (DISENO.md §13.7): criterio G1 — la GUI habla de Flujos y Agentes (sin «proyecto» ni «asistente» visibles)."""
import re

import pytest
from conftest import esperar_run
from test_f2_api import cadena, hilo, lanzar, proyecto, py_node
from test_ui import URL, browser, pagina  # noqa: F401  (fixture)

pytestmark = pytest.mark.ui
PROHIBIDAS = re.compile(r"proyecto|asistente", re.I)


async def test_g1_textos_flujos_y_agentes(api, browser):  # noqa: F811
    p = proyecto(api, "Cuenta letras", {"cuenta": py_node("return {'longitud': len(state['texto'])}", "texto",
                                                          "longitud")}, cadena("cuenta"),
                 state_schema={"texto": {"reducer": "replace", "input": True}})
    tid = hilo(api, p["slug"])
    esperar_run(api, lanzar(api, tid, input={"texto": "hola"})["run_id"])
    api.post(f"/api/projects/{p['slug']}/assistants", json={"name": "Contador"})
    pg = await pagina(browser)
    # menú
    await pg.goto(f"{URL}/#/inicio")
    await pg.wait_for_selector("#inicioAgentes")
    menu = await pg.inner_text("#menu nav")
    assert "Flujos" in menu and "Agentes" in menu, menu
    rutas = {"inicio": "#inicioAgentes", "flujos": ".card", f"estudio/{p['slug']}?hilo={tid}": "#asstSel",
             "hilos": "table", f"asistentes?proyecto={p['slug']}": "#nuevoAsistente", "modulos": "main",
             "trazas": "table", "datasets": "main", "programaciones": "main", "salidas": "main", "config": "main"}
    for ruta, sel in rutas.items():
        await pg.goto(f"{URL}/#/{ruta}")
        await pg.wait_for_selector(sel)
        await pg.wait_for_timeout(500)
        txt = await pg.evaluate("document.body.innerText + ' ' + [...document.querySelectorAll('option')]"
                                ".map(o => o.textContent).join(' ')")
        malas = sorted({m.group(0) for m in PROHIBIDAS.finditer(txt)})
        assert not malas, (ruta, malas, [ln for ln in txt.splitlines() if PROHIBIDAS.search(ln)][:5])
    # #/proyectos redirige a Flujos
    await pg.goto(f"{URL}/#/proyectos")
    await pg.wait_for_selector("h2:has-text('Flujos')")
    assert pg.url.endswith("#/flujos")
    # módulos renombrados
    await pg.goto(f"{URL}/#/modulos")
    await pg.wait_for_timeout(800)
    mods = await pg.inner_text("#main")
    assert "Agente con herramientas" in mods and "Subflujo" in mods
    # «+ Nuevo flujo» con «Clip de personaje» sigue abriendo el diálogo guiado
    await pg.goto(f"{URL}/#/inicio")
    await pg.click("#nuevoProyectoBtn")
    await pg.click(".modal .card[data-t=clip_personaje]")
    await pg.click(".modal footer button.primary")
    await pg.wait_for_selector("#qvProyecto")
    assert "Nuevo flujo · Clip de personaje" in await pg.inner_text(".modal")
    assert not pg.errores, pg.errores
    await pg.context.close()
