"""Long-term memory across sessions."""
from __future__ import annotations

from core.memory import store

from .base import Tool


class RememberTool(Tool):
    name = "remember"
    description = """
    Store a durable fact about the user so it survives across conversations.
    Use for stable preferences and context: people they work with, recurring
    commitments, how they like things done, ongoing projects, goals.
    Do NOT use for transient details from the current conversation.
    """
    schema = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Short stable identifier, e.g. 'manager' or 'standup_time'.",
            },
            "value": {"type": "string", "description": "The fact to remember."},
            "category": {
                "type": "string",
                "enum": ["people", "preferences", "projects", "schedule", "general"],
            },
        },
        "required": ["key", "value"],
    }

    async def run(self, key: str, value: str, category: str = "general") -> str:
        store.remember(key, value, category)
        return f"Remembered [{category}] {key}: {value}"


class RecallTool(Tool):
    name = "recall"
    description = """
    Search long-term memory for what you know about the user. Call this when a
    request depends on personal context you do not already have in this
    conversation. Omit `query` to list everything you know.
    """
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Optional keyword filter."}
        },
    }

    async def run(self, query: str | None = None) -> str:
        facts = store.recall(query)
        if not facts:
            return "Nothing in memory matches that."
        return "\n".join(f"[{f['category']}] {f['key']}: {f['value']}" for f in facts)


class ForgetTool(Tool):
    name = "forget"
    description = "Delete a stored fact by its key. Use when the user says something is no longer true."
    schema = {
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
    }
    confirm = True

    def preview(self, args: dict) -> str:
        return f"Forget the stored fact '{args.get('key')}'"

    async def run(self, key: str) -> str:
        return f"Forgot '{key}'." if store.forget(key) else f"No stored fact named '{key}'."
