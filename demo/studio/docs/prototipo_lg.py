# Prototipo ejecutado el 2026-11-18 con .venv-studio (Python 3.12, langgraph 1.2.22). Ejecutar: cd docs && ../.venv-studio/bin/python prototipo_lg.py
import asyncio, operator
from typing import Annotated, Any, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command, RetryPolicy, CachePolicy, Send
from langgraph.config import get_stream_writer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.runtime import RunControl
from langgraph.errors import GraphDrained

def merge(a: dict, b: dict) -> dict:
    out = dict(a or {}); out.update(b or {}); return out
class S(TypedDict):
    data: Annotated[dict, merge]

def mk(name):
    async def node(state: S):
        w = get_stream_writer(); w({"node": name, "progress": 0.5})
        await asyncio.sleep(0.05)
        return {"data": {name: True, "last": name}}
    return node
async def review(state: S):
    v = interrupt({"campo": "texto", "valor": state["data"].get("texto", "")})
    return {"data": {"texto": v["valor"], "aprobado": v.get("aprobado", True)}}

g = StateGraph(S)
for n in ["a","voz","retrato","lipsync"]: g.add_node(n, mk(n), retry_policy=RetryPolicy(max_attempts=2), timeout=30)
g.add_node("rev", review)
g.add_edge(START, "a"); g.add_edge("a", "rev"); g.add_edge("rev","voz"); g.add_edge("a","retrato")
g.add_edge(["voz","retrato"], "lipsync"); g.add_conditional_edges("lipsync", lambda s: END if s["data"].get("aprobado") else "a")

async def main():
    async with AsyncSqliteSaver.from_conn_string("ck.sqlite") as cp:
        app = g.compile(checkpointer=cp)
        cfg = {"configurable": {"thread_id": "t1"}, "max_concurrency": 1, "recursion_limit": 25}
        async for mode, ch in app.astream({"data": {"texto": "hola"}}, cfg, stream_mode=["updates","custom","tasks"]):
            print(mode, str(ch)[:110])
        st = await app.aget_state(cfg); print("NEXT", st.next, "INTERRUPTS", st.interrupts)
        async for mode, ch in app.astream(Command(resume={"valor": "editado", "aprobado": True}), cfg, stream_mode=["updates"]):
            print(mode, str(ch)[:110])
        hist = [h async for h in app.aget_state_history(cfg)]
        print("HISTORIA", len(hist), [ (h.metadata.get("step"), h.next) for h in hist][:8])
        # fork: desde el checkpoint previo a 'rev', cambia el texto
        target = next(h for h in hist if "rev" in h.next)
        newcfg = await app.aupdate_state(target.config, {"data": {"texto": "fork"}})
        st2 = await app.aget_state(newcfg); print("FORK next", st2.next, st2.values["data"]["texto"])
        # drain
        rc = RunControl(); cfg2 = {"configurable": {"thread_id": "t2"}, "max_concurrency": 1}
        try:
            async for mode, ch in app.astream({"data": {"aprobado": True}}, cfg2, stream_mode=["updates"], control=rc):
                print("t2", str(ch)[:80]); rc.request_drain()
        except GraphDrained as e: print("DRAINED ok")
        print("t2 next", (await app.aget_state(cfg2)).next)
        print("mermaid:", app.get_graph().draw_mermaid()[:120].replace("\n"," | "))
asyncio.run(main())
