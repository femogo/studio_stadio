"""Paso 2a: comprobación en ejecución de las APIs de LangGraph que usa F2 (DISENO.md §6.3, §13.3)."""
import asyncio
import operator
import time
from typing import Annotated, TypedDict

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.cache.sqlite import SqliteCache
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime
from langgraph.store.sqlite.aio import AsyncSqliteStore
from langgraph.types import CachePolicy, Command, RetryPolicy, Send, interrupt

pytestmark = pytest.mark.spike


class S(TypedDict, total=False):
    log: Annotated[list, operator.add]
    x: str
    error: dict
    item: str


@pytest.fixture
async def cp(tmp_path):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "ck.sqlite")) as saver:
        yield saver


async def correr(app, inp, cfg, modes=("updates", "custom"), **kw):
    out = []
    async for m, ch in app.astream(inp, cfg, stream_mode=list(modes), **kw):
        out.append((m, ch))
    return out


# ---------------------------------------------------------------------------------------------- caché
async def test_cache_acierto_no_llama_al_nodo_y_updates_trae_metadata_cached(cp, tmp_path):
    calls = {"n": 0}

    async def lento(state):
        calls["n"] += 1
        return {"log": ["lento"]}

    def key(state):
        return state.get("x") or ""

    g = StateGraph(S)
    g.add_node("lento", lento, cache_policy=CachePolicy(key_func=key, ttl=60))
    g.add_edge(START, "lento"); g.add_edge("lento", END)
    app = g.compile(checkpointer=cp, cache=SqliteCache(path=str(tmp_path / "c.sqlite")))
    await correr(app, {"x": "a"}, {"configurable": {"thread_id": "1"}})
    ev = await correr(app, {"x": "a"}, {"configurable": {"thread_id": "2"}})
    assert calls["n"] == 1
    ups = [ch for m, ch in ev if m == "updates" and "lento" in ch]
    assert ups and ups[0].get("__metadata__", {}).get("cached") is True
    st = await app.aget_state({"configurable": {"thread_id": "2"}})
    assert st.values["log"] == ["lento"]
    await correr(app, {"x": "b"}, {"configurable": {"thread_id": "3"}})
    assert calls["n"] == 2
    # vaciar por namespace (= nombre del nodo)
    await app.cache.aclear()
    await correr(app, {"x": "a"}, {"configurable": {"thread_id": "4"}})
    assert calls["n"] == 3


# ---------------------------------------------------------------------------------------------- defer
async def test_defer_espera_a_las_ramas_y_corre_una_vez(cp):
    marcas = []

    def mk(n, d):
        async def f(state):
            marcas.append((n, "start", time.monotonic()))
            await asyncio.sleep(d)
            marcas.append((n, "end", time.monotonic()))
            return {"log": [n]}
        return f

    g = StateGraph(S)
    g.add_node("a", mk("a", 0.05)); g.add_node("b1", mk("b1", 0.1)); g.add_node("b2", mk("b2", 0.1))
    g.add_node("b3", mk("b3", 0.1))
    g.add_node("c", mk("c", 0), defer=True)
    g.add_edge(START, "a"); g.add_edge(START, "b1"); g.add_edge("b1", "b2"); g.add_edge("b2", "b3")
    g.add_edge("a", "c"); g.add_edge("b3", "c"); g.add_edge("c", END)
    app = g.compile(checkpointer=cp)
    await correr(app, {}, {"configurable": {"thread_id": "1"}})
    starts_c = [t for n, k, t in marcas if n == "c" and k == "start"]
    end_b3 = [t for n, k, t in marcas if n == "b3" and k == "end"]
    assert len(starts_c) == 1 and starts_c[0] >= end_b3[0]


# ---------------------------------------------------------------------------------------------- Send
async def test_send_task_id_distinto_por_rama(cp):
    vistos = []

    def mapa(state):
        return [Send("hijo", {"item": i}) for i in ("p", "q", "r")]

    async def hijo(state, runtime: Runtime):
        vistos.append(runtime.execution_info.task_id)
        return {"log": [state["item"]]}

    g = StateGraph(S)
    g.add_node("hijo", hijo)
    g.add_conditional_edges(START, mapa, ["hijo"])
    g.add_edge("hijo", END)
    app = g.compile(checkpointer=cp)
    await correr(app, {}, {"configurable": {"thread_id": "1"}})
    st = await app.aget_state({"configurable": {"thread_id": "1"}})
    assert sorted(st.values["log"]) == ["p", "q", "r"]
    assert len(set(vistos)) == 3


async def test_send_devuelto_por_un_nodo_via_command(cp):
    """Un nodo (no una arista) que devuelve Command(goto=[Send…]): así se compila mapa_send."""
    def mapa(state):
        return Command(goto=[Send("hijo", {"item": i}) for i in ("p", "q")])

    async def hijo(state):
        return {"log": [state["item"]]}

    g = StateGraph(S)
    g.add_node("mapa", mapa, destinations=("hijo",))
    g.add_node("hijo", hijo)
    g.add_edge(START, "mapa"); g.add_edge("hijo", END)
    app = g.compile(checkpointer=cp)
    await correr(app, {}, {"configurable": {"thread_id": "1"}})
    st = await app.aget_state({"configurable": {"thread_id": "1"}})
    assert sorted(st.values["log"]) == ["p", "q"]


# ---------------------------------------------------------------------------------------------- subgrafo
async def test_subgrafo_sin_checkpointer_con_interrupt_y_ns(cp):
    hijo = StateGraph(S)

    async def pregunta(state):
        v = interrupt({"q": "ok?"})
        return {"log": [f"resp:{v}"]}

    hijo.add_node("pregunta", pregunta)
    hijo.add_edge(START, "pregunta"); hijo.add_edge("pregunta", END)
    sub = hijo.compile(checkpointer=None)

    g = StateGraph(S)
    g.add_node("sub", sub)
    g.add_node("fin", lambda s: {"log": ["fin"]})
    g.add_edge(START, "sub"); g.add_edge("sub", "fin"); g.add_edge("fin", END)
    app = g.compile(checkpointer=cp)
    cfg = {"configurable": {"thread_id": "1"}}
    nss = set()
    async for ns, m, ch in app.astream({"log": []}, cfg, stream_mode=["updates"], subgraphs=True):
        nss.add(ns)
    assert any(ns and ns[0].startswith("sub:") for ns in nss)
    st = await app.aget_state(cfg)
    assert st.interrupts and st.next == ("sub",)
    iid = st.interrupts[0].id
    async for _ in app.astream(Command(resume={iid: "si"}), cfg, stream_mode=["updates"], subgraphs=True):
        pass
    st = await app.aget_state(cfg)
    assert st.values["log"] == ["resp:si", "fin"]
    assert "sub" in app.get_graph(xray=1).draw_mermaid()
    assert "pregunta" in app.get_graph(xray=1).draw_mermaid()


# ---------------------------------------------------------------------------------------------- error_handler
async def test_error_handler_command_goto(cp):
    calls = {"n": 0}

    async def falla(state):
        calls["n"] += 1
        raise ConnectionError("caída")

    async def handler(state, error: NodeError):
        return Command(goto="recuperar", update={"error": {"node": error.node, "msg": repr(error.error)}})

    g = StateGraph(S)
    g.add_node("falla", falla, retry_policy=RetryPolicy(max_attempts=2, initial_interval=0.01, jitter=False),
               error_handler=handler, destinations=("recuperar",))
    g.add_node("recuperar", lambda s: {"log": ["recuperado"]})
    g.add_edge(START, "falla"); g.add_edge("falla", END); g.add_edge("recuperar", END)
    app = g.compile(checkpointer=cp)
    ev = await correr(app, {}, {"configurable": {"thread_id": "1"}})
    st = await app.aget_state({"configurable": {"thread_id": "1"}})
    assert calls["n"] == 2
    assert st.values["error"]["node"] == "falla" and st.values["log"] == ["recuperado"]
    nombres = {k for m, ch in ev if m == "updates" for k in ch}
    assert any(k.startswith("__error_handler__") for k in nombres) or "recuperar" in nombres


# ---------------------------------------------------------------------------------------------- store
async def _fake_embed(textos):
    out = []
    for t in textos:
        t = t.lower()
        out.append([1.0 if w in t else 0.0 for w in ("pizza", "comida", "perro", "toby")] + [0.01] * 4)
    return out


async def test_store_sqlite_con_indice(tmp_path):
    async with AsyncSqliteStore.from_conn_string(str(tmp_path / "s.sqlite"),
                                                 index={"dims": 8, "embed": _fake_embed, "fields": ["$"]}) as st:
        await st.setup()
        await st.aput(("usuarios", "ana"), "m1", {"texto": "Me gusta la pizza, mi comida favorita"})
        await st.aput(("usuarios", "ana"), "m2", {"texto": "Mi perro se llama Toby"})
        await st.aput(("usuarios", "ana"), "m3", {"texto": "sin índice"}, index=False)
        res = await st.asearch(("usuarios",), query="comida", limit=5)
        assert res[0].key == "m1" and res[0].score is not None
        sin = await st.asearch(("usuarios", "ana"), limit=10)
        assert {r.key for r in sin} == {"m1", "m2", "m3"}
        ns = await st.alist_namespaces(prefix=("usuarios",))
        assert ("usuarios", "ana") in ns
        await st.adelete(("usuarios", "ana"), "m2")
        assert await st.aget(("usuarios", "ana"), "m2") is None


@pytest.mark.llm
async def test_store_con_embeddings_de_ollama(tmp_path):
    async def embed(textos):
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post("http://localhost:11434/api/embed", json={"model": "nomic-embed-text", "input": textos})
            r.raise_for_status()
            return r.json()["embeddings"]

    async with AsyncSqliteStore.from_conn_string(str(tmp_path / "s.sqlite"),
                                                 index={"dims": 768, "embed": embed, "fields": ["$"]}) as st:
        await st.setup()
        await st.aput(("u",), "a", {"texto": "Mi plato preferido es la paella"})
        await st.aput(("u",), "b", {"texto": "Trabajo como fontanero en Valencia"})
        res = await st.asearch(("u",), query="¿qué comida le gusta?", limit=2)
        assert res[0].key == "a"


# ---------------------------------------------------------------------------------------------- messages
class M(TypedDict, total=False):
    messages: Annotated[list, add_messages]


async def test_stream_messages_desde_un_nodo_con_wrapper(cp):
    llm = GenericFakeChatModel(messages=iter([AIMessage(content="hola que tal estas hoy")]))

    async def chat(state, runtime: Runtime):
        # se llama sin pasar config: el contexto (contextvars, 3.12) propaga los callbacks
        partes = []
        async for ch in llm.astream(state["messages"]):
            partes.append(ch)
        msg = partes[0]
        for p in partes[1:]:
            msg = msg + p
        return {"messages": [AIMessage(content=msg.content, id=msg.id)]}

    g = StateGraph(M)
    g.add_node("chat", chat)
    g.add_edge(START, "chat"); g.add_edge("chat", END)
    app = g.compile(checkpointer=cp)
    toks = []
    async for ns, m, ch in app.astream({"messages": [HumanMessage("hola")]}, {"configurable": {"thread_id": "1"}},
                                       stream_mode=["messages", "updates"], subgraphs=True):
        if m == "messages":
            chunk, meta = ch
            toks.append((chunk.content, meta.get("langgraph_node")))
    assert len(toks) >= 3 and all(n == "chat" for _, n in toks)
    st = await app.aget_state({"configurable": {"thread_id": "1"}})
    assert st.values["messages"][-1].content == "hola que tal estas hoy"


@pytest.mark.llm
async def test_chat_ollama_tool_calling(cp):
    from langchain_core.tools import tool
    from langchain_ollama import ChatOllama
    from langgraph.prebuilt import ToolNode, tools_condition

    @tool
    def multiplicar(a: int, b: int) -> int:
        """Multiplica dos enteros."""
        return a * b

    llm = ChatOllama(model="qwen2.5:7b", temperature=0).bind_tools([multiplicar])

    async def agente(state):
        return {"messages": [await llm.ainvoke(state["messages"])]}

    g = StateGraph(M)
    g.add_node("agente", agente)
    g.add_node("herramientas", ToolNode([multiplicar]))
    g.add_edge(START, "agente")
    g.add_conditional_edges("agente", tools_condition, {"tools": "herramientas", END: END})
    g.add_edge("herramientas", "agente")
    app = g.compile(checkpointer=cp)
    toks = 0
    async for m, ch in app.astream({"messages": [HumanMessage("¿Cuánto es 37 por 91? Usa la herramienta.")]},
                                   {"configurable": {"thread_id": "1"}}, stream_mode=["messages"]):
        toks += 1
    st = await app.aget_state({"configurable": {"thread_id": "1"}})
    assert "3367" in st.values["messages"][-1].content.replace(".", "").replace(",", "")
    assert toks >= 5
