import sys, types
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from core.models import cost_usd, route
from core.memory import Store, _repair

print("=== routing ===")
cases = [
    ("hey", "fast"), ("what time is it", "fast"), ("thanks", "fast"),
    ("what's on my calendar today", "fast"),
    ("research the best ergonomic keyboards", "deep"),
    ("help me think through whether to take the job", "deep"),
    ("why does the deploy keep failing", "deep"),
    ("draft an email to Sarah about the delay", "deep"),
    ("email sarah", "fast"),
    (" ".join(["word"]*50), "deep"),
]
bad = 0
for text, want in cases:
    got = route(text).name
    flag = "ok " if got == want else "FAIL"
    if got != want: bad += 1
    print(f"  {flag} {want:5s} <- {text[:44]!r}")

print("\n=== cost ===")
u = types.SimpleNamespace(input_tokens=1000, output_tokens=500,
                          cache_read_input_tokens=8000, cache_creation_input_tokens=0)
h = cost_usd("claude-haiku-4-5", u)
s = cost_usd("claude-sonnet-5", u)
print(f"  haiku  turn: ${h:.6f}")
print(f"  sonnet turn: ${s:.6f}")
print(f"  100 haiku turns/day * 30: ${h*100*30:.2f}/mo")

print("\n=== history repair ===")
tests = {
 "leading assistant": [{"role":"assistant","content":"x"},{"role":"user","content":"a"}],
 "orphan tool_result": [{"role":"user","content":[{"type":"tool_result","tool_use_id":"1","content":"r"}]},
                        {"role":"assistant","content":"ok"},{"role":"user","content":"hi"}],
 "dangling tool_use": [{"role":"user","content":"hi"},
                       {"role":"assistant","content":[{"type":"tool_use","id":"1","name":"x","input":{}}]}],
 "clean": [{"role":"user","content":"hi"},{"role":"assistant","content":"yo"}],
}
for name, msgs in tests.items():
    out = _repair([dict(m) for m in msgs])
    ok = (not out) or (out[0]["role"] == "user")
    dangling = out and out[-1]["role"]=="assistant" and isinstance(out[-1]["content"], list) and any(
        b.get("type")=="tool_use" for b in out[-1]["content"] if isinstance(b,dict))
    print(f"  {'ok ' if ok and not dangling else 'FAIL'} {name:22s} -> {len(out)} msgs")
    if not ok or dangling: bad += 1

print("\n=== store ===")
import tempfile, os
db = tempfile.mktemp(suffix=".sqlite3")
st = Store(db)
st.remember("manager", "Dana Reyes", "people")
st.remember("standup", "9:15 daily", "schedule")
st.remember("manager", "Dana Reyes (VP Eng)", "people")   # upsert
print("  recall 'dana':", st.recall("dana"))
print("  facts total:", len(st.recall()))
st.append_turn("s1","user","hello"); st.append_turn("s1","assistant","hi")
print("  history:", st.history("s1"))
st.record_spend("claude-haiku-4-5", u, h)
print(f"  month spend: ${st.spend_this_month():.6f}")
print("  forget:", st.forget("standup"), st.forget("nope"))
os.unlink(db)

print("\n" + ("ALL CHECKS PASSED" if bad==0 else f"{bad} FAILURES"))
sys.exit(1 if bad else 0)
