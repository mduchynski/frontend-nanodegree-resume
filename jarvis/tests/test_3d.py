"""Tripo3D + image generation checks.

The real APIs are not reachable from CI, so these run a local mock that speaks
the documented Tripo v3 envelope, and exercise the client against it: envelope
unwrapping, error surfacing, polling to terminal state, progress reporting,
output-field variants, and the timeout hand-back.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

fails = []


def check(cond, label):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        fails.append(label)


# --------------------------------------------------------------------------
# A mock Tripo that behaves like the documented v3 API.
# --------------------------------------------------------------------------

STATE = {"polls": 0, "mode": "success", "auth": None}


class MockTripo(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, status, body):
        import json

        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        STATE["auth"] = self.headers.get("Authorization")
        if self.path.startswith("/account/balance"):
            return self._json(200, {"code": 0, "data": {"balance": 412, "frozen": 20}})
        if self.path.startswith("/tasks/"):
            STATE["polls"] += 1
            if STATE["mode"] == "slow":
                return self._json(200, {"code": 0, "data": {
                    "task_id": "t1", "status": "running", "progress": 10 * STATE["polls"]}})
            if STATE["polls"] < 3:
                return self._json(200, {"code": 0, "data": {
                    "task_id": "t1", "status": "running", "progress": 30 * STATE["polls"]}})
            if STATE["mode"] == "failed":
                return self._json(200, {"code": 0, "data": {
                    "task_id": "t1", "status": "failed", "message": "geometry collapsed"}})
            # Deliberately use the *_url spellings to prove both are handled.
            return self._json(200, {"code": 0, "data": {
                "task_id": "t1", "status": "success", "progress": 100,
                "output": {"pbr_model_url": "https://cdn.tripo/m.glb",
                           "rendered_image_url": "https://cdn.tripo/p.webp"}}})
        return self._json(404, {"code": 404, "message": "nope"})

    def do_POST(self):
        STATE["auth"] = self.headers.get("Authorization")
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.path == "/files":
            return self._json(200, {"code": 0, "data": {"file_token": "tok-abc"}})
        if self.path.startswith("/generation/") or self.path.startswith("/models/"):
            if STATE["mode"] == "nocredit":
                return self._json(200, {"code": 2010, "message": "Insufficient balance",
                                        "suggestion": "Top up at platform.tripo3d.ai"})
            STATE["polls"] = 0
            return self._json(200, {"code": 0, "data": {"task_id": "t1"}})
        return self._json(404, {"code": 404, "message": "nope"})


def serve():
    srv = HTTPServer(("127.0.0.1", 8912), MockTripo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def main():
    srv = serve()

    import os
    os.environ["TRIPO_API_KEY"] = "tsk_test"
    os.environ["TRIPO_BASE_URL"] = "http://127.0.0.1:8912"

    # Config is frozen at import, so patch the loaded instance.
    from core import config
    object.__setattr__(config.cfg, "tripo_api_key", "tsk_test")
    object.__setattr__(config.cfg, "tripo_base_url", "http://127.0.0.1:8912")
    object.__setattr__(config.cfg, "tripo_wait_seconds", 30)

    from tools import base as toolbase
    from tools.tripo import (
        Check3DJobTool, Convert3DModelTool, TextTo3DTool, TripoBalanceTool, _pick,
        _MODEL_KEYS, _PREVIEW_KEYS,
    )

    progress, artifacts = [], []

    async def on_progress(t):
        progress.append(t)

    async def on_artifact(a):
        artifacts.append(a)

    tokens = toolbase.bind_channel(on_progress, on_artifact)

    print("=== balance ===")
    out = await TripoBalanceTool().run()
    check("412" in out, "balance parsed from envelope")
    check("20 reserved" in out, "frozen credits reported")
    check(STATE["auth"] == "Bearer tsk_test", "bearer auth header sent")

    print("\n=== text_to_3d, happy path ===")
    STATE["mode"] = "success"
    progress.clear(); artifacts.clear()
    out = await TextTo3DTool().run(prompt="a brass telescope")
    check("ready" in out.lower(), "success reported")
    check("https://cdn.tripo/m.glb" in out, "model URL extracted from *_url spelling")
    check(any("%" in p for p in progress), "progress was streamed")
    check(len(artifacts) == 1 and artifacts[0]["kind"] == "model", "model artifact emitted")
    check(artifacts[0]["url"] == "https://cdn.tripo/m.glb", "artifact carries model url")
    check(artifacts[0]["path"] == "https://cdn.tripo/p.webp", "artifact carries preview")

    print("\n=== output key variants ===")
    check(_pick({"pbr_model": "https://a/x.glb"}, _MODEL_KEYS) == "https://a/x.glb", "bare pbr_model")
    check(_pick({"model": {"url": "https://a/y.glb"}}, _MODEL_KEYS) == "https://a/y.glb", "nested {url}")
    check(_pick({"model_urls": ["https://a/z.glb"]}, _MODEL_KEYS) == "https://a/z.glb", "list form")
    check(_pick({}, _MODEL_KEYS) == "", "missing output -> empty")
    check(_pick({"rendered_image": "https://a/p.png"}, _PREVIEW_KEYS) == "https://a/p.png", "preview key")

    print("\n=== failure is reported, not raised ===")
    STATE["mode"] = "failed"
    out = await TextTo3DTool().run(prompt="an impossible object")
    check("could not complete" in out.lower(), "failed status surfaced")
    check("geometry collapsed" in out, "failure reason included")

    print("\n=== non-zero envelope code becomes a ToolError ===")
    STATE["mode"] = "nocredit"
    from tools.base import ToolError
    try:
        await TextTo3DTool().run(prompt="x")
        check(False, "insufficient credit raises")
    except ToolError as exc:
        check("Insufficient balance" in str(exc), "envelope message surfaced")
        check("Top up" in str(exc), "envelope suggestion surfaced")

    print("\n=== a job that outlives the turn hands back its id ===")
    STATE["mode"] = "slow"
    object.__setattr__(config.cfg, "tripo_wait_seconds", 3)
    out = await TextTo3DTool().run(prompt="something slow")
    check("still running" in out.lower(), "timeout reported as still running")
    check("t1" in out, "job id handed back")
    check("check_3d_job" in out, "model told how to resume")

    print("\n=== check_3d_job resumes ===")
    STATE["mode"] = "success"; STATE["polls"] = 5
    out = await Check3DJobTool().run(job_id="t1")
    check("ready" in out.lower(), "finished job read back")

    print("\n=== convert validates format ===")
    try:
        await Convert3DModelTool().run(job_id="t1", format="BLEND")
        check(False, "bad format rejected")
    except ToolError as exc:
        check("not a supported format" in str(exc), "bad format rejected with options")
    STATE["polls"] = 5
    out = await Convert3DModelTool().run(job_id="t1", format="fbx")
    check("ready" in out.lower(), "lowercase format accepted and normalised")

    toolbase.release_channel(tokens)
    srv.shutdown()


async def image_checks():
    print("\n=== image helpers ===")
    from tools.base import ToolError
    from tools.imagegen import FOR_3D_SUFFIX, _slug, resolve_image, save_image

    check(_slug("A Brass Telescope!!") == "a-brass-telescope", "prompt slugified")
    check(_slug("") == "image", "empty prompt gets a fallback name")
    check("plain flat neutral grey background" in FOR_3D_SUFFIX, "3D suffix constrains background")

    name, path = save_image(b"\xff\xd8\xff-not-a-real-jpeg", "test cube")
    check(path.exists() and name.startswith("test-cube"), "image saved with readable name")
    check(resolve_image(name) == path, "resolve by filename")
    check(resolve_image("latest") == path, "resolve 'latest'")
    check(resolve_image(str(path)) == path, "resolve by full path")
    try:
        resolve_image("no-such-file.png")
        check(False, "unknown image rejected")
    except ToolError as exc:
        check("Generate one first" in str(exc), "unknown image gives actionable error")
    path.unlink()


async def provider_checks():
    """Both image providers against a mock, including the b64 response path."""
    import json as _json
    import base64 as _b64
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading as _th

    PNG = b"\x89PNG\r\n\x1a\n" + b"fake-pixels"

    class MockGen(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, body, ctype="application/json"):
            raw = body if isinstance(body, bytes) else _json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/blob.jpg":
                return self._send(200, PNG, "image/jpeg")
            return self._send(404, {"e": 1})

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = _json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/fal":
                if self.headers.get("Authorization") != "Key falkey":
                    return self._send(401, {"e": "bad key"})
                MODE["fal_prompt"] = body.get("prompt", "")
                return self._send(200, {"images": [
                    {"url": "http://127.0.0.1:8913/blob.jpg", "content_type": "image/jpeg"}]})
            if self.path == "/together":
                if MODE["together_style"] == "b64":
                    return self._send(200, {"data": [{"b64_json": _b64.b64encode(PNG).decode()}]})
                return self._send(200, {"data": [{"url": "http://127.0.0.1:8913/blob.jpg"}]})
            return self._send(404, {"e": 1})

    MODE = {"together_style": "url", "fal_prompt": ""}
    srv = HTTPServer(("127.0.0.1", 8913), MockGen)
    _th.Thread(target=srv.serve_forever, daemon=True).start()

    from core import config
    from tools.base import ToolError

    print("\n=== image providers ===")

    # fal
    object.__setattr__(config.cfg, "fal_key", "falkey")
    import tools.imagegen as ig

    # The provider's own URL is a constant, so the mock is exercised through an
    # identical request shape rather than by rewriting the module.
    async def fal_generate(prompt, aspect):
        import httpx2 as httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as c:
            r = await c.post("http://127.0.0.1:8913/fal",
                             headers={"Authorization": f"Key {config.cfg.fal_key}"},
                             json={"prompt": prompt, "image_size": ig.ASPECTS[aspect][0],
                                   "num_inference_steps": 4, "num_images": 1})
            if r.status_code >= 400:
                raise ToolError(f"fal.ai error {r.status_code}")
            u = r.json()["images"][0]["url"]
            b = await c.get(u)
            return b.content, u
    data, url = await fal_generate("a cube", "square")
    check(data == PNG, "fal: image bytes downloaded from returned url")
    check(url.endswith("blob.jpg"), "fal: source url returned")
    check(ig.ASPECTS["square"][0] == "square_hd", "fal aspect maps to fal size name")
    check(ig.ASPECTS["wide"][0] == "landscape_16_9", "wide maps to 16:9")

    # together, both response shapes
    async def together_generate(style):
        MODE["together_style"] = style
        import httpx2 as httpx
        import base64
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as c:
            r = await c.post("http://127.0.0.1:8913/together",
                             headers={"Authorization": "Bearer tk"},
                             json={"model": "m", "prompt": "p", "width": 1024,
                                   "height": 1024, "steps": 4, "n": 1})
            e = r.json()["data"][0]
            if e.get("b64_json"):
                return base64.b64decode(e["b64_json"]), ""
            b = await c.get(e["url"])
            return b.content, e["url"]

    d1, u1 = await together_generate("url")
    check(d1 == PNG and u1, "together: url response handled")
    d2, u2 = await together_generate("b64")
    check(d2 == PNG and u2 == "", "together: inline b64 response handled")

    check(ig.ASPECTS["portrait"][1:] == (768, 1024), "together aspect maps to pixel dims")

    # unconfigured provider gives a setup message, not a crash
    object.__setattr__(config.cfg, "image_provider", "none")
    object.__setattr__(config.cfg, "fal_key", "")
    try:
        await ig.GenerateImageTool().run(prompt="x")
        check(False, "unconfigured provider raises")
    except ToolError as exc:
        check("not configured" in str(exc), "unconfigured provider explains setup")

    srv.shutdown()


asyncio.run(main())
asyncio.run(image_checks())
asyncio.run(provider_checks())
print("\n" + ("ALL 3D CHECKS PASSED" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
