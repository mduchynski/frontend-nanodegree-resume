"""The agent loop: stream a reply, run tools, gate irreversible actions."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import anthropic

from tools import ToolError, registry

from .config import cfg
from .memory import store
from .models import DEEP, cost_usd, route
from .prompt import SYSTEM

log = logging.getLogger("jarvis.agent")

Emit = Callable[[dict], Awaitable[None]]
Confirm = Callable[[str], Awaitable[bool]]

MAX_TOOL_ROUNDS = 8


class BudgetExceeded(Exception):
    pass


class Agent:
    def __init__(self) -> None:
        if not cfg.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env.")
        self.client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)

    # ------------------------------------------------------------------

    async def respond(
        self,
        session_id: str,
        user_text: str,
        emit: Emit,
        confirm: Confirm,
    ) -> str:
        """Handle one user turn. Streams events through `emit`; returns the
        final spoken text."""
        spent = store.spend_this_month()
        if spent >= cfg.monthly_budget_usd:
            raise BudgetExceeded(
                f"Monthly budget of ${cfg.monthly_budget_usd:.2f} is used up "
                f"(${spent:.2f} spent). Raise JARVIS_MONTHLY_BUDGET_USD to continue."
            )

        store.append_turn(session_id, "user", user_text)
        messages = store.history(session_id)
        tools = registry.specs()

        tier = route(user_text)
        turn_cost = 0.0
        final_text = ""

        for round_index in range(MAX_TOOL_ROUNDS):
            # A long tool chain means a genuinely involved request; let the
            # better model finish it.
            if round_index >= 3 and tier is not DEEP:
                tier = DEEP
                await emit({"type": "status", "state": "thinking", "detail": "escalating"})

            await emit({"type": "status", "state": "thinking", "detail": tier.name})

            response, text = await self._stream_once(tier, tools, messages, emit)
            turn_cost += cost_usd(tier.model, response.usage)
            store.record_spend(tier.model, response.usage, cost_usd(tier.model, response.usage))

            if text:
                final_text = text

            if response.stop_reason == "refusal":
                detail = getattr(response, "stop_details", None)
                reason = getattr(detail, "explanation", None) or "I can't help with that one."
                store.append_turn(session_id, "assistant", [{"type": "text", "text": reason}])
                await emit({"type": "delta", "text": reason})
                final_text = reason
                break

            # Persist the assistant turn exactly as returned, so tool_use ids
            # line up with the tool_result blocks we are about to append.
            content = [b.model_dump() for b in response.content]
            messages.append({"role": "assistant", "content": content})
            store.append_turn(session_id, "assistant", content)

            if response.stop_reason != "tool_use":
                break

            results = await self._run_tools(response, emit, confirm)
            messages.append({"role": "user", "content": results})
            store.append_turn(session_id, "user", results)
        else:
            note = "I've hit my limit on steps for this one. Want me to keep going?"
            await emit({"type": "delta", "text": note})
            final_text = final_text or note

        await emit(
            {
                "type": "done",
                "text": final_text,
                "model": tier.model,
                "cost": round(turn_cost, 6),
                "month_usd": round(store.spend_this_month(), 4),
            }
        )
        return final_text

    # ------------------------------------------------------------------

    async def _stream_once(self, tier, tools, messages, emit: Emit):
        """One API call, streaming text deltas to the client as they arrive."""
        kwargs = tier.request_kwargs()
        text_parts: list[str] = []

        try:
            async with self.client.messages.stream(
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM,
                        # Caches the tools + system prefix. Everything above
                        # this point is byte-stable across turns.
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=tools,
                messages=messages,
                **kwargs,
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        text_parts.append(event.delta.text)
                        await emit({"type": "delta", "text": event.delta.text})
                response = await stream.get_final_message()
        except anthropic.RateLimitError as exc:
            retry_after = exc.response.headers.get("retry-after", "30")
            raise RuntimeError(f"Rate limited by the API. Try again in {retry_after}s.") from exc
        except anthropic.AuthenticationError as exc:
            raise RuntimeError("ANTHROPIC_API_KEY is invalid.") from exc
        except anthropic.APIStatusError as exc:
            raise RuntimeError(f"API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise RuntimeError("Could not reach the Claude API. Check your connection.") from exc

        return response, "".join(text_parts).strip()

    # ------------------------------------------------------------------

    async def _run_tools(self, response, emit: Emit, confirm: Confirm) -> list[dict]:
        """Execute every tool_use block in the response.

        Independent calls run concurrently; confirmation-gated ones are
        serialised so the user is never asked two questions at once. All
        results come back in one user message, in the original block order.
        """
        blocks = [b for b in response.content if b.type == "tool_use"]
        results: list[dict | None] = [None] * len(blocks)

        gated = [(i, b) for i, b in enumerate(blocks) if self._needs_confirm(b)]
        free = [(i, b) for i, b in enumerate(blocks) if not self._needs_confirm(b)]

        if free:
            done = await asyncio.gather(
                *(self._one_tool(b, emit) for _, b in free), return_exceptions=False
            )
            for (i, _), res in zip(free, done):
                results[i] = res

        for i, block in gated:
            tool = registry.get(block.name)
            preview = tool.preview(dict(block.input)) if tool else block.name
            await emit({"type": "status", "state": "awaiting", "detail": "confirmation"})
            approved = await confirm(preview)
            if not approved:
                await emit({"type": "tool", "phase": "end", "name": block.name, "detail": "declined"})
                results[i] = {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "The user declined this action. Do not retry it. "
                    "Ask what they would like changed.",
                }
                continue
            results[i] = await self._one_tool(block, emit)

        return [r for r in results if r is not None]

    @staticmethod
    def _needs_confirm(block) -> bool:
        tool = registry.get(block.name)
        return bool(tool and tool.confirm)

    async def _one_tool(self, block, emit: Emit) -> dict:
        tool = registry.get(block.name)
        args = dict(block.input) if isinstance(block.input, dict) else {}

        await emit(
            {
                "type": "tool",
                "phase": "start",
                "name": block.name,
                "detail": _humanise(block.name, args),
            }
        )

        if tool is None:
            return _error_result(block.id, f"No such tool: {block.name}")

        try:
            output = await tool.invoke(args)
        except ToolError as exc:
            await emit({"type": "tool", "phase": "end", "name": block.name, "detail": "failed"})
            return _error_result(block.id, str(exc))
        except TypeError as exc:
            # Bad arguments from the model -- recoverable, tell it what broke.
            await emit({"type": "tool", "phase": "end", "name": block.name, "detail": "bad args"})
            return _error_result(block.id, f"Invalid arguments for {block.name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface, never crash the turn
            log.exception("tool %s failed", block.name)
            await emit({"type": "tool", "phase": "end", "name": block.name, "detail": "error"})
            return _error_result(block.id, f"{type(exc).__name__}: {exc}")

        await emit({"type": "tool", "phase": "end", "name": block.name, "detail": "ok"})
        return {"type": "tool_result", "tool_use_id": block.id, "content": output}


def _error_result(tool_use_id: str, message: str) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": message,
        "is_error": True,
    }


_VERBS = {
    "web_search": "Searching",
    "read_webpage": "Reading",
    "search_email": "Checking mail",
    "read_email": "Reading mail",
    "send_email": "Sending mail",
    "draft_email": "Drafting",
    "list_calendar_events": "Checking calendar",
    "find_free_time": "Finding a gap",
    "create_calendar_event": "Booking",
    "cancel_calendar_event": "Cancelling",
    "get_current_time": "Checking the time",
    "remember": "Noting that",
    "recall": "Recalling",
    "forget": "Forgetting",
    "send_text": "Texting",
}


def _humanise(name: str, args: dict) -> str:
    """Short status line for the HUD, e.g. 'Searching: brave search pricing'."""
    verb = _VERBS.get(name, name)
    hint = args.get("query") or args.get("subject") or args.get("url") or args.get("key") or ""
    hint = str(hint)
    if len(hint) > 48:
        hint = hint[:48] + "..."
    return f"{verb}: {hint}" if hint else verb
