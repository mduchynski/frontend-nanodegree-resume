"""FastAPI app: serves the HUD and runs one agent per WebSocket connection."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tools import registry

from .agent import Agent, BudgetExceeded
from .config import cfg
from .memory import store

log = logging.getLogger("jarvis.server")
WEB = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Jarvis")
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
# Generated images and models, so the HUD can display what Jarvis just made.
# StaticFiles refuses paths that escape the directory.
app.mount("/files", StaticFiles(directory=str(cfg.images_dir.parent)), name="files")

_agent: Agent | None = None


def agent() -> Agent:
    global _agent
    if _agent is None:
        _agent = Agent()
    return _agent


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/api/status")
async def status() -> JSONResponse:
    return JSONResponse(
        {
            "user": cfg.user_name,
            "timezone": cfg.timezone,
            "fast_model": cfg.fast_model,
            "deep_model": cfg.deep_model,
            "capabilities": {
                "email": cfg.google_enabled,
                "calendar": cfg.microsoft_enabled,
                "search": True,
                "brave": bool(cfg.brave_api_key),
                "images": cfg.images_enabled,
                "threed": cfg.tripo_enabled,
            },
            "tools": registry.describe(),
            "spend": {
                "month_usd": round(store.spend_this_month(), 4),
                "budget_usd": cfg.monthly_budget_usd,
                "by_model": store.spend_breakdown(),
            },
        }
    )


class Session:
    """One browser tab. Owns the socket and the pending-confirmation table."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.id = uuid.uuid4().hex[:12]
        self.pending: dict[str, asyncio.Future[bool]] = {}
        self.task: asyncio.Task | None = None

    async def emit(self, payload: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(payload))
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def confirm(self, preview: str) -> bool:
        """Ask the browser, block until the user answers."""
        request_id = uuid.uuid4().hex[:8]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self.pending[request_id] = future
        await self.emit({"type": "confirm", "id": request_id, "preview": preview})
        try:
            # A prompt nobody answers should not wedge the session forever.
            return await asyncio.wait_for(future, timeout=180)
        except asyncio.TimeoutError:
            await self.emit({"type": "confirm_timeout", "id": request_id})
            return False
        finally:
            self.pending.pop(request_id, None)

    def resolve(self, request_id: str, approved: bool) -> None:
        future = self.pending.get(request_id)
        if future and not future.done():
            future.set_result(approved)

    def cancel(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
        for future in self.pending.values():
            if not future.done():
                future.set_result(False)


@app.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    await ws.accept()
    session = Session(ws)
    await session.emit({"type": "ready", "session": session.id, "user": cfg.user_name})

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            kind = msg.get("type")

            if kind == "message":
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                if session.task and not session.task.done():
                    session.task.cancel()
                session.task = asyncio.create_task(_handle(session, text))

            elif kind == "confirm_response":
                session.resolve(msg.get("id", ""), bool(msg.get("approved")))

            elif kind == "interrupt":
                session.cancel()
                await session.emit({"type": "status", "state": "idle", "detail": "stopped"})

            elif kind == "reset":
                store.clear_session(session.id)
                await session.emit({"type": "status", "state": "idle", "detail": "memory cleared"})

    except WebSocketDisconnect:
        pass
    finally:
        session.cancel()


async def _handle(session: Session, text: str) -> None:
    try:
        await agent().respond(session.id, text, session.emit, session.confirm)
    except asyncio.CancelledError:
        raise
    except BudgetExceeded as exc:
        await session.emit({"type": "error", "message": str(exc)})
    except Exception as exc:  # noqa: BLE001 - the socket must survive
        log.exception("turn failed")
        await session.emit({"type": "error", "message": str(exc)})
