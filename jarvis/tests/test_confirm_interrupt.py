"""Interrupting a pending confirmation must not strand the tool call."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
import core.server as server

fails = []
def check(c, l):
    print(f"  {'ok  ' if c else 'FAIL'} {l}")
    if not c: fails.append(l)

CAPTURED = {}

class GatedAgent:
    """Mimics the real flow: store the assistant turn, then await confirmation."""
    async def respond(self, session_id, text, emit, confirm):
        await emit({"type": "status", "state": "thinking", "detail": "fast"})
        approved = await confirm("Send work email\nTo: dana@corp.com")
        CAPTURED["approved"] = approved
        final = "Sent." if approved else "Cancelled."
        await emit({"type": "done", "text": final, "model": "stub", "cost": 0, "month_usd": 0})
        return final

server._agent = GatedAgent()
client = TestClient(server.app)

print("=== STOP during a confirmation declines it, turn completes ===")
with client.websocket_connect("/ws") as ws:
    ws.receive_json()
    ws.send_json({"type": "message", "text": "email dana"})
    cid = None
    for _ in range(10):
        m = ws.receive_json()
        if m["type"] == "confirm":
            cid = m["id"]
            ws.send_json({"type": "interrupt"})     # the STOP path
            break
    check(cid is not None, "confirmation was requested")
    done = None
    for _ in range(10):
        m = ws.receive_json()
        if m["type"] == "done":
            done = m
            break
    check(done is not None, "the turn finished rather than hanging")
    check(CAPTURED.get("approved") is False, "interrupt answered the confirmation as 'no'")
    check(done and done["text"] == "Cancelled.", "outcome reflects the decline")

print("\n=== a second message also declines before cancelling ===")
CAPTURED.clear()
with client.websocket_connect("/ws") as ws:
    ws.receive_json()
    ws.send_json({"type": "message", "text": "email dana"})
    for _ in range(10):
        m = ws.receive_json()
        if m["type"] == "confirm":
            ws.send_json({"type": "message", "text": "actually never mind"})
            break
    ws.send_json({"type": "interrupt"})
    got = ws.receive_json()
    check(got["type"] in ("status", "confirm", "done"), "socket still healthy")
    check(CAPTURED.get("approved") is False, "pending confirmation was resolved, not stranded")

print("\n" + ("ALL CONFIRM-INTERRUPT CHECKS PASSED" if not fails else f"{len(fails)} FAILURES"))
sys.exit(1 if fails else 0)
