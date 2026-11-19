"""D9 (DISENO.md §13.7): criterio G3 — flujo como herramienta con Ollama real (marcadores llm y ui)."""
import pytest
from test_f2_api import cadena, hilo, proyecto, py_node
from test_f2_llm import _chat
from test_ui import URL, browser, pagina  # noqa: F401  (fixture)

pytestmark = pytest.mark.llm


async def test_g3_flujo_herramienta_ollama(api, browser):  # noqa: F811
    h = proyecto(api, "Hijo", {"cuenta": py_node("return {'longitud': len(state['texto'])}", "texto", "longitud")},
                 cadena("cuenta"), desc="Cuenta cuántas letras (caracteres) tiene un texto",
                 state_schema={"texto": {"reducer": "replace", "input": True},
                               "longitud": {"reducer": "replace", "output": True}})
    assert h["slug"] == "hijo"
    nodes = {"agente": {"module": "agente_react.py", "x": 100, "y": 100,
                        "config": {"flujos": "hijo", "herramientas": "calculadora", "temperatura": 0,
                                   "sistema": "Eres un asistente útil. Responde en español. Para contar letras usa "
                                              "siempre la herramienta flujo__hijo."}}}
    p = proyecto(api, "Agente con flujos", nodes, cadena("agente"),
                 state_schema={"messages": {"reducer": "messages", "input": True, "output": True}})
    tid = hilo(api, p["slug"])
    _, run = _chat(api, tid, '¿Cuántas letras tiene "murciélago"?')
    assert run["status"] == "success", run
    msgs = api.get(f"/api/threads/{tid}/messages").json()
    assert any(c["name"] == "flujo__hijo" for m in msgs for c in m.get("tool_calls") or []), msgs
    assert any(m["role"] == "tool" and '"longitud": 10' in m["content"] for m in msgs), msgs
    assert "10" in msgs[-1]["content"], msgs[-1]
    # chat del Estudio: la llamada aparece plegable como «flujo · hijo»
    pg = await pagina(browser)
    await pg.goto(f"{URL}/#/estudio/{p['slug']}?hilo={tid}")
    await pg.wait_for_selector("#asstSel")
    if await pg.locator(".chat.hidden").count():
        await pg.click("#modoChat")
    await pg.wait_for_selector(".chat details.tool summary")
    assert "flujo · hijo" in await pg.inner_text(".chat details.tool summary")
    assert not pg.errores, pg.errores
    await pg.context.close()


