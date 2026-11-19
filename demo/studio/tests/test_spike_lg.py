"""Paso 1a: comprobación en ejecución de las APIs de LangGraph 1.2.x que usa el diseño (DISENO.md §1, §5)."""
import asyncio, operator, sqlite3
from typing import Annotated, TypedDict

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphDrained, NodeError, NodeTimeoutError
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import RunControl, Runtime
from langgraph.types import Command, RetryPolicy, TimeoutPolicy, interrupt

pytestmark = pytest.mark.spike
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


class S(TypedDict, total=False):
    log: Annotated[list, operator.add]
    x: str


def mk(calls, name, delay=0.0, intr=False):
    async def node(state):
        calls[name] = calls.get(name, 0) + 1
        if intr:
            v = interrupt({"q": name}, response_schema=SCHEMA)
            return {"log": [f"{name}:{v['ok']}"]}
        await asyncio.sleep(delay)
        return {"log": [name]}
    return node


@pytest.fixture
async def cp(tmp_path):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "ck.sqlite")) as saver:
        saver._path = tmp_path / "ck.sqlite"
        yield saver


def grafo_paralelo(calls, cp, b_delay=0.3, b_name="b", intr=True):
    g = StateGraph(S)
    g.add_node("a", mk(calls, "a"))
    g.add_node("h", mk(calls, "h", intr=intr, delay=0.05))
    g.add_node(b_name, mk(calls, b_name, b_delay))
    g.add_node("c", mk(calls, "c"))
    g.add_edge(START, "a"); g.add_edge("a", "h"); g.add_edge("a", b_name)
    g.add_edge(["h", b_name], "c"); g.add_edge("c", END)
    return g.compile(checkpointer=cp)


async def test_drain_con_interrupt_en_superstep_termina_interrupted(cp):
    calls = {}
    app = grafo_paralelo(calls, cp)
    rc, cfg, drained = RunControl(), {"configurable": {"thread_id": "t1"}}, False
    try:
        async for mode, ch in app.astream({"log": []}, cfg, stream_mode=["updates", "tasks"], control=rc):
            if mode == "tasks" and ch.get("name") == "h" and "input" in ch:
                rc.request_drain()
    except GraphDrained:
        drained = True
    st = await app.aget_state(cfg)
    assert not drained                       # no lanza GraphDrained: gana el interrupt
    assert st.next == ("h",) and len(st.interrupts) == 1
    # response_schema aparece en aget_state().interrupts
    assert st.interrupts[0].response_schema == SCHEMA
    # reanudar por id; 'b' (ya terminado en el superstep) no se repite
    iid = st.interrupts[0].id
    async for _ in app.astream(Command(resume={iid: {"ok": True}}), cfg, stream_mode=["updates"]):
        pass
    assert calls == {"a": 1, "b": 1, "h": 2, "c": 1}
    assert (await app.aget_state(cfg)).values["log"][-1] == "c"


async def test_drain_sin_interrupt_lanza_graphdrained(cp):
    calls = {}
    app = grafo_paralelo(calls, cp, intr=False)
    rc, cfg = RunControl(), {"configurable": {"thread_id": "td"}}
    with pytest.raises(GraphDrained):
        async for mode, ch in app.astream({"log": []}, cfg, stream_mode=["tasks"], control=rc):
            if ch.get("name") == "b" and "input" in ch:
                rc.request_drain()
    st = await app.aget_state(cfg)
    assert st.next == ("c",)                 # el superstep (h, b) terminó entero
    async for _ in app.astream(None, cfg):
        pass
    assert calls == {"a": 1, "h": 1, "b": 1, "c": 1}


@pytest.mark.parametrize("durability", ["async", "sync"])
async def test_cancelar_con_tarea_paralela_terminada_no_se_repite(cp, durability):
    calls = {}
    app = grafo_paralelo(calls, cp, b_delay=5, b_name="slow", intr=False)
    cfg = {"configurable": {"thread_id": "t2" + durability}}

    async def consume():
        async for _ in app.astream({"log": []}, cfg, durability=durability):
            pass
    t = asyncio.create_task(consume())
    await asyncio.sleep(0.6)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    st = await app.aget_state(cfg)
    assert st.next == ("slow",)              # h terminó: su escritura pendiente está guardada
    assert {tk.name: tk.result for tk in st.tasks}["h"] == {"log": ["h"]}
    updates, tasks = [], []
    async for mode, ch in app.astream(None, cfg, stream_mode=["updates", "tasks"]):
        (updates if mode == "updates" else tasks).append(ch)
    assert calls == {"a": 1, "h": 1, "slow": 2, "c": 1}
    # la escritura reutilizada llega en 'updates' marcada como cached; en 'tasks' solo aparece su INICIO
    # (sin resultado): el motor no debe contarla como ejecución ni dejarla «ejecutando»
    assert {"h": {"log": ["h"]}, "__metadata__": {"cached": True}} in updates
    th = [t for t in tasks if t.get("name") == "h"]
    print("tasks h:", th)
    assert th and all("result" not in t for t in th)


async def test_input_sobre_hilo_con_next_pendiente_reinicia_desde_start(cp):
    calls = {}
    app = grafo_paralelo(calls, cp, intr=False)
    cfg = {"configurable": {"thread_id": "t3"}}
    async for _ in app.astream({"log": ["init"]}, cfg, interrupt_before=["c"]):
        pass
    assert (await app.aget_state(cfg)).next == ("c",)
    async for _ in app.astream({"log": ["new"]}, cfg):
        pass
    st = await app.aget_state(cfg)
    # el 'next' pendiente se abandona; la entrada se aplica sobre el estado acumulado y se arranca desde START
    assert st.next == () and st.values["log"][:2] == ["init", "a"] and "new" in st.values["log"]
    assert calls["a"] == 2


async def test_copy_ignora_values_y_editar_con_as_node_del_escritor(cp):
    g = StateGraph(S)
    g.add_node("a", mk({}, "a")); g.add_node("b", mk({}, "b"))
    g.add_edge(START, "a"); g.add_edge("a", "b"); g.add_edge("b", END)
    app = g.compile(checkpointer=cp)
    cfg = {"configurable": {"thread_id": "t4"}}
    async for _ in app.astream({"log": [], "x": "orig"}, cfg):
        pass
    tgt = next([h async for h in app.aget_state_history(cfg) if h.next == ("b",)].__iter__())
    # __copy__ = bifurcar SIN cambios: ignora values, mismo next, metadata.source == 'fork', mismo padre
    nc = await app.aupdate_state(tgt.config, {"x": "edit"}, as_node="__copy__")
    s = await app.aget_state(nc)
    assert s.values["x"] == "orig" and s.next == ("b",) and s.metadata["source"] == "fork"
    assert s.parent_config["configurable"]["checkpoint_id"] == tgt.parent_config["configurable"]["checkpoint_id"]
    # editar = aupdate_state con as_node = nodo que escribió el checkpoint: next se recalcula con sus aristas
    nc2 = await app.aupdate_state(tgt.config, {"x": "edit"}, as_node="a")
    s2 = await app.aget_state(nc2)
    assert s2.values["x"] == "edit" and s2.next == ("b",)
    assert s2.parent_config["configurable"]["checkpoint_id"] == tgt.config["configurable"]["checkpoint_id"]


async def test_timeoutpolicy_idle_con_heartbeat():
    async def hb(state, runtime: Runtime):
        for _ in range(8):
            await asyncio.sleep(0.1)
            runtime.heartbeat()
        return {"x": "ok"}

    async def mudo(state, runtime: Runtime):
        await asyncio.sleep(1.0)
        return {"x": "nunca"}

    def g(fn, pol):
        gr = StateGraph(S); gr.add_node("n", fn, timeout=pol); gr.add_edge(START, "n")
        return gr.compile()
    idle = TimeoutPolicy(idle_timeout=0.3, refresh_on="heartbeat")
    assert (await g(hb, idle).ainvoke({"log": []}))["x"] == "ok"
    with pytest.raises(NodeTimeoutError) as e:
        await g(mudo, idle).ainvoke({"log": []})
    assert e.value.kind == "idle"
    with pytest.raises(NodeTimeoutError) as e:
        await g(hb, TimeoutPolicy(run_timeout=0.3)).ainvoke({"log": []})
    assert e.value.kind == "run"            # run_timeout no se refresca con heartbeat


async def test_error_handler_se_llama_tras_agotar_reintentos():
    att, visto = [], {}

    async def malo(state):
        att.append(1)
        raise RuntimeError("boom")

    async def handler(state, error: NodeError):
        visto.update(node=error.node, err=repr(error.error), intentos=len(att))
        return Command(goto="fix", update={"x": "handled"})
    g = StateGraph(S)
    g.add_node("malo", malo, retry_policy=RetryPolicy(max_attempts=3, initial_interval=0.01, retry_on=RuntimeError),
               error_handler=handler, destinations=("fix",))
    g.add_node("fix", mk({}, "fix")); g.add_edge(START, "malo"); g.add_edge("fix", END)
    app = g.compile()
    r = await app.ainvoke({"log": []})
    assert r["x"] == "handled" and visto == {"node": "malo", "err": "RuntimeError('boom')", "intentos": 3}
    assert "__error_handler__malo" in app.get_graph().nodes


async def test_columnas_checkpoints_y_copiar_hilo_por_sql(cp):
    calls = {}
    app = grafo_paralelo(calls, cp, intr=False)
    cfg = {"configurable": {"thread_id": "orig"}}
    async for _ in app.astream({"log": []}, cfg, interrupt_before=["c"]):
        pass
    con = sqlite3.connect(cp._path)
    cols = {t: [r[1] for r in con.execute(f"PRAGMA table_info({t})")] for t in ("checkpoints", "writes")}
    assert cols["checkpoints"] == ["thread_id", "checkpoint_ns", "checkpoint_id", "parent_checkpoint_id", "type",
                                   "checkpoint", "metadata"]
    assert cols["writes"] == ["thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "task_path", "idx",
                              "channel", "type", "value"]
    with con:
        con.execute("INSERT INTO checkpoints SELECT 'copia', checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, "
                    "checkpoint, metadata FROM checkpoints WHERE thread_id='orig'")
        con.execute("INSERT INTO writes SELECT 'copia', checkpoint_ns, checkpoint_id, task_id, task_path, idx, channel, "
                    "type, value FROM writes WHERE thread_id='orig'")
    con.close()
    c2 = {"configurable": {"thread_id": "copia"}}
    assert (await app.aget_state(c2)).next == ("c",)
    async for _ in app.astream(None, c2):
        pass
    assert (await app.aget_state(c2)).values["log"][-1] == "c"
    assert (await app.aget_state(cfg)).next == ("c",)     # el original no cambia
    assert len([h async for h in app.aget_state_history(c2)]) > len([h async for h in app.aget_state_history(cfg)])


async def test_stream_v1_subgraphs_y_checkpoints(cp):
    calls = {}
    app = grafo_paralelo(calls, cp, intr=False)
    cfg = {"configurable": {"thread_id": "t5"}}
    modos = set()
    async for item in app.astream({"log": []}, cfg, stream_mode=["updates", "custom", "tasks", "checkpoints"],
                                  subgraphs=True, version="v1"):
        ns, mode, ch = item                  # con subgraphs=True: (ns, modo, dato)
        assert ns == ()
        modos.add(mode)
        if mode == "checkpoints":
            assert {"config", "metadata", "values", "next", "parent_config", "tasks"} <= set(ch)
    assert {"updates", "tasks", "checkpoints"} <= modos
