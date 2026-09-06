import asyncio, sys, types
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from core.agent import Agent
from tools import registry

fails = []
def check(cond, label):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond: fails.append(label)

def block(name, args, bid):
    b = types.SimpleNamespace(type="tool_use", name=name, input=args, id=bid)
    return b

class FakeResponse:
    def __init__(self, blocks): self.content = blocks

async def main():
    agent = Agent.__new__(Agent)   # skip __init__ (needs an API key)

    events = []
    async def emit(e): events.append(e)

    print("=== 1. non-gated tools run concurrently, order preserved ===")
    resp = FakeResponse([
        block("get_current_time", {}, "t1"),
        block("recall", {"query": "manager"}, "t2"),
        block("get_current_time", {"days_offset": 1}, "t3"),
    ])
    async def never(_): raise AssertionError("should not ask for confirmation")
    results = await agent._run_tools(resp, emit, never)
    check(len(results) == 3, "three results returned")
    check([r["tool_use_id"] for r in results] == ["t1","t2","t3"], "ids in original order")
    check(all(not r.get("is_error") for r in results), "no errors")

    print("\n=== 2. gated tool asks, approval runs it ===")
    events.clear()
    asked = []
    async def approve(preview):
        asked.append(preview); return True
    resp = FakeResponse([block("forget", {"key": "nothing-here"}, "g1")])
    results = await agent._run_tools(resp, emit, approve)
    check(len(asked) == 1, "confirmation was requested")
    check("nothing-here" in asked[0], "preview names the target")
    check("No stored fact" in results[0]["content"], "tool actually ran")

    print("\n=== 3. decline blocks execution ===")
    async def decline(preview): return False
    resp = FakeResponse([block("forget", {"key": "x"}, "g2")])
    results = await agent._run_tools(resp, emit, decline)
    check("declined this action" in results[0]["content"], "declined result returned")
    check(results[0]["tool_use_id"] == "g2", "tool_use_id echoed on decline")

    print("\n=== 4. mixed batch: free run, gated still asks ===")
    asked.clear()
    resp = FakeResponse([
        block("get_current_time", {}, "m1"),
        block("forget", {"key": "y"}, "m2"),
        block("recall", {}, "m3"),
    ])
    results = await agent._run_tools(resp, emit, approve)
    check(len(results) == 3 and [r["tool_use_id"] for r in results] == ["m1","m2","m3"],
          "order preserved across mixed batch")
    check(len(asked) == 1, "exactly one confirmation for one gated tool")

    print("\n=== 5. failures come back as is_error, not exceptions ===")
    resp = FakeResponse([block("does_not_exist", {}, "e1")])
    results = await agent._run_tools(resp, emit, never)
    check(results[0].get("is_error") is True, "unknown tool -> is_error")

    resp = FakeResponse([block("read_webpage", {"url": "ftp://bad"}, "e2")])
    results = await agent._run_tools(resp, emit, never)
    check(results[0].get("is_error") is True, "ToolError -> is_error")
    check("http://" in results[0]["content"], "error text is actionable")

    resp = FakeResponse([block("remember", {"wrong_arg": 1}, "e3")])
    results = await agent._run_tools(resp, emit, never)
    check(results[0].get("is_error") is True, "bad args -> is_error not crash")

    print("\n=== 6. every gated tool has a real preview ===")
    for t in registry.active():
        if not t.confirm: continue
        p = t.preview({"key":"k","to":"a@b.com","subject":"S","body":"B","message":"M","event_id":"E"})
        check(len(p) > 10 and "(" not in p.split("\n")[0], f"{t.name} preview is human-readable")

asyncio.run(main())
print("\n" + ("ALL AGENT CHECKS PASSED" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
