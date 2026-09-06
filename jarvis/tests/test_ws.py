import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
import core.server as server

fails = []
def check(c, l):
    print(f"  {'ok  ' if c else 'FAIL'} {l}")
    if not c: fails.append(l)

class StubAgent:
    """Exercises the socket the way the real agent does: stream deltas, run a
    tool, ask for confirmation, finish."""
    async def respond(self, session_id, text, emit, confirm):
        await emit({"type":"status","state":"thinking","detail":"fast"})
        await emit({"type":"tool","phase":"start","name":"web_search","detail":"Searching: x"})
        await emit({"type":"tool","phase":"end","name":"web_search","detail":"ok"})
        approved = await confirm("Send email\nTo: a@b.com\nSubject: Hi")
        for chunk in ["Sent." if approved else "Cancelled.", " Anything else?"]:
            await emit({"type":"delta","text":chunk})
        final = ("Sent." if approved else "Cancelled.") + " Anything else?"
        await emit({"type":"done","text":final,"model":"stub","cost":0.0,"month_usd":0.0})
        return final

server._agent = StubAgent()
client = TestClient(server.app)

for approve in (True, False):
    print(f"\n=== confirmation round-trip: approve={approve} ===")
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        check(hello["type"] == "ready", "server sends ready")
        check("session" in hello, "session id issued")

        ws.send_json({"type":"message","text":"email bob"})

        got, confirm_id, deltas = [], None, ""
        for _ in range(20):
            msg = ws.receive_json()
            got.append(msg["type"])
            if msg["type"] == "confirm":
                confirm_id = msg["id"]
                check("Subject: Hi" in msg["preview"], "preview reached the browser")
                ws.send_json({"type":"confirm_response","id":confirm_id,"approved":approve})
            if msg["type"] == "delta":
                deltas += msg["text"]
            if msg["type"] == "done":
                check(msg["text"] == deltas, "done text matches streamed deltas")
                break

        check("status" in got, "status event delivered")
        check(got.count("tool") == 2, "tool start+end delivered")
        check(confirm_id is not None, "confirm requested")
        expected = "Sent." if approve else "Cancelled."
        check(deltas.startswith(expected), f"outcome reflects approval ({expected})")

print("\n=== interrupt and reset are accepted ===")
with client.websocket_connect("/ws") as ws:
    ws.receive_json()
    ws.send_json({"type":"interrupt"})
    m = ws.receive_json()
    check(m["type"] == "status" and m["detail"] == "stopped", "interrupt acknowledged")
    ws.send_json({"type":"reset"})
    m = ws.receive_json()
    check("cleared" in m["detail"], "reset acknowledged")
    ws.send_json({"type":"garbage-not-json"})
    ws.send_json({"type":"unknown_kind"})
    ws.send_json({"type":"message","text":"   "})
    ws.send_json({"type":"interrupt"})
    m = ws.receive_json()
    check(m["type"] == "status", "socket survives malformed and empty input")

print("\n=== errors surface without killing the socket ===")
class BoomAgent:
    async def respond(self, *a, **k): raise RuntimeError("upstream exploded")
server._agent = BoomAgent()
with client.websocket_connect("/ws") as ws:
    ws.receive_json()
    ws.send_json({"type":"message","text":"hi"})
    m = ws.receive_json()
    check(m["type"] == "error" and "exploded" in m["message"], "error event sent")
    ws.send_json({"type":"interrupt"})
    m = ws.receive_json()
    check(m["type"] == "status", "socket still alive after an error")

print("\n" + ("ALL WEBSOCKET CHECKS PASSED" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
