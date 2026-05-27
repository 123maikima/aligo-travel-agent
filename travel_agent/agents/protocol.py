"""Shared Agent input/output helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agentscope.message import Msg


@dataclass
class AgentContext:
    """Normalized request context for Agent reply handlers."""

    query: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    user_preferences: dict[str, Any] = field(default_factory=dict)
    recent_dialogue: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_input(cls, value: Any) -> "AgentContext":
        if isinstance(value, list):
            raw_content = value[-1].content if value else ""
        elif hasattr(value, "content"):
            raw_content = value.content
        else:
            raw_content = value

        payload: dict[str, Any]
        if isinstance(raw_content, dict):
            payload = raw_content
        elif isinstance(raw_content, str):
            try:
                parsed = json.loads(raw_content)
                payload = parsed if isinstance(parsed, dict) else {"query": raw_content}
            except (json.JSONDecodeError, TypeError):
                payload = {"query": raw_content}
        else:
            payload = {"query": str(raw_content)}

        context = payload.get("context", {})
        if not isinstance(context, dict):
            context = {}

        query = (
            context.get("rewritten_query")
            or payload.get("rewritten_query")
            or payload.get("query")
            or ""
        )
        if not query:
            recent_dialogue = context.get("recent_dialogue", [])
            if isinstance(recent_dialogue, list):
                for message in reversed(recent_dialogue):
                    if isinstance(message, dict) and message.get("role") == "user":
                        query = str(message.get("content", ""))
                        break

        preferences = context.get("user_preferences", {})
        if not isinstance(preferences, dict):
            preferences = {}

        recent_dialogue = context.get("recent_dialogue", [])
        if not isinstance(recent_dialogue, list):
            recent_dialogue = []

        return cls(
            query=str(query),
            payload=payload,
            context=context,
            user_preferences=preferences,
            recent_dialogue=recent_dialogue,
        )


@dataclass
class AgentResult:
    """Serializable Agent result with legacy field compatibility."""

    status: str
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    error: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload = {"status": self.status, **self.data}
        if self.message:
            payload["message"] = self.message
        if self.error:
            payload["error"] = self.error
        return payload

    def to_msg(self, name: str) -> Msg:
        return Msg(name=name, content=json.dumps(self.to_payload(), ensure_ascii=False), role="assistant")

    @classmethod
    def success(cls, **data: Any) -> "AgentResult":
        return cls(status="success", data=data)

    @classmethod
    def failure(cls, message: str, **data: Any) -> "AgentResult":
        return cls(status="error", data=data, message=message, error=message)
