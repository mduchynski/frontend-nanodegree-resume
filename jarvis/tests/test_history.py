"""Conversation-history integrity.

Regression suite for the 400 that killed a session:
  messages.NN: `tool_use` ids were found without `tool_result` blocks

A turn cancelled while a confirmation was on screen stored the assistant's
tool_use but never its tool_result, and every later request reloaded the
broken history and was rejected.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core.agent import _pair_every_call  # noqa: E402
from core.memory import Store, _repair  # noqa: E402

fails = []


def check(cond, label):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        fails.append(label)


def call(cid, name="send_work_email"):
    return {"type": "tool_use", "id": cid, "name": name, "input": {}}


def result(cid, text="ok"):
    return {"type": "tool_result", "tool_use_id": cid, "content": text}


def valid(msgs) -> bool:
    """The rule the API enforces: every tool_use answered by the next message."""
    for i, m in enumerate(msgs):
        ids = {b.get("id") for b in m.get("content", [])
               if isinstance(b, dict) and b.get("type") == "tool_use"}
        if m.get("role") == "assistant" and ids:
            nxt = msgs[i + 1] if i + 1 < len(msgs) else None
            got = set()
            if nxt and nxt.get("role") == "user" and isinstance(nxt.get("content"), list):
                got = {b.get("tool_use_id") for b in nxt["content"]
                       if isinstance(b, dict) and b.get("type") == "tool_result"}
            if not ids <= got:
                return False
    if msgs and msgs[0].get("role") != "user":
        return False
    return True


print("=== the reported failure, reproduced ===")
# Jarvis proposed send_work_email; the turn was cancelled while the
# confirmation was on screen; the user then said something else.
poisoned = [
    {"role": "user", "content": "email dana about thursday"},
    {"role": "assistant", "content": [call("toolu_018GM9hYYf4df2wugM99ZDCh")]},
    {"role": "user", "content": "actually never mind"},
    {"role": "assistant", "content": "Understood."},
]
check(not valid(poisoned), "the stored history is genuinely invalid")
fixed = _repair([dict(m) for m in poisoned])
check(valid(fixed), "repair makes it valid")
check(len(fixed) == 3 and fixed[0]["content"] == "email dana about thursday",
      "the unanswered call is dropped, the conversation survives")
check(all("toolu_018GM9" not in str(m) for m in fixed), "no trace of the dangling id")

print("\n=== every shape of damage ===")
cases = {
    "dangling call mid-history": poisoned,
    "dangling call at the end": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [call("t1")]},
    ],
    "orphan results at the front (window trimmed)": [
        {"role": "user", "content": [result("t0")]},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "thanks"},
    ],
    "results that match nothing": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [call("t1")]},
        {"role": "user", "content": [result("WRONG")]},
    ],
    "partially answered parallel calls": [
        {"role": "user", "content": "check both"},
        {"role": "assistant", "content": [call("t1"), call("t2")]},
        {"role": "user", "content": [result("t1")]},
    ],
    "leading assistant turn": [
        {"role": "assistant", "content": "stray"},
        {"role": "user", "content": "hi"},
    ],
    "two damaged turns in a row": [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": [call("t1")]},
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": [call("t2")]},
        {"role": "user", "content": "c"},
    ],
    "empty": [],
}
for name, msgs in cases.items():
    out = _repair([dict(m) for m in msgs])
    check(valid(out), f"{name} -> valid")

print("\n=== healthy conversations are left alone ===")
good = [
    {"role": "user", "content": "what's on my calendar"},
    {"role": "assistant", "content": [call("t1", "list_calendar_events")]},
    {"role": "user", "content": [result("t1", "3 events")]},
    {"role": "assistant", "content": "Three things, Duke."},
    {"role": "user", "content": "thanks"},
]
out = _repair([dict(m) for m in good])
check(out == good, "a valid history is unchanged")

parallel = [
    {"role": "user", "content": "check both"},
    {"role": "assistant", "content": [call("t1"), call("t2")]},
    {"role": "user", "content": [result("t1"), result("t2")]},
]
check(_repair([dict(m) for m in parallel]) == parallel, "parallel calls preserved")

print("\n=== interrupted turns are paired at the source ===")
content = [call("t1"), call("t2")]
paired = _pair_every_call(content, [])          # cancelled before anything ran
check(len(paired) == 2, "every call still answered")
check(all(p["is_error"] for p in paired), "marked as errors")
check("Interrupted" in paired[0]["content"], "says what happened")
check([p["tool_use_id"] for p in paired] == ["t1", "t2"], "ids and order preserved")

paired = _pair_every_call(content, [result("t1", "sent")])   # cancelled halfway
check(len(paired) == 2 and paired[0]["content"] == "sent", "real result kept")
check(paired[1].get("is_error"), "the one that never ran is flagged")
check("Do not assume it happened" in paired[1]["content"],
      "the model is warned not to claim the send succeeded")

check(_pair_every_call([{"type": "text", "text": "hi"}], []) == [],
      "a turn with no calls needs no results")

print("\n=== end to end through the store ===")
db = tempfile.mktemp(suffix=".sqlite3")
store = Store(db)
store.append_turn("s1", "user", "email dana")
store.append_turn("s1", "assistant", [call("t9")])
# cancelled here -- no result written, exactly as the bug did
store.append_turn("s1", "user", "never mind")
check(valid(store.history("s1")), "history() returns something the API will accept")

store2 = Store(db)
store2.append_turn("s2", "user", "hi")
store2.append_turn("s2", "assistant", [call("t10")])
store2.append_turn("s2", "user", [result("t10")])
store2.append_turn("s2", "assistant", "done")
check(valid(store2.history("s2")), "a clean session still round-trips")
check(len(store2.history("s2")) == 4, "and keeps all four turns")
pathlib.Path(db).unlink()

print("\n" + ("ALL HISTORY CHECKS PASSED" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
