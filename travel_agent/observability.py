"""Lightweight full-chain observability for Agent execution.

The module writes structured JSONL events that can later be shipped to an
external tracing system. It intentionally avoids external dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import hashlib
import json
import re
import threading
import time
import uuid

from travel_agent.config import OBSERVABILITY_CONFIG


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def truncate_value(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars] + "...[truncated]"
    if isinstance(value, list):
        return [truncate_value(item, max_chars) for item in value[:20]]
    if isinstance(value, dict):
        return {str(k): truncate_value(v, max_chars) for k, v in list(value.items())[:80]}
    return value


_SECRET_PATTERN = re.compile(r"\b[A-Za-z0-9_-]{20,}\b")


def _mask_query(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    prefix = value[:64]
    suffix = "...[masked]" if len(value) > 64 else "[masked]"
    return f"{prefix}{suffix} sha256={digest}"


def _redact_error(value: str) -> str:
    return _SECRET_PATTERN.sub("***", value)


def mask_pii(value: Any) -> Any:
    if not OBSERVABILITY_CONFIG.get("mask_pii", True):
        return value
    if isinstance(value, list):
        return [mask_pii(item) for item in value]
    if isinstance(value, dict):
        masked = {}
        for key, item in value.items():
            key_str = str(key)
            if key_str == "query" and isinstance(item, str):
                masked[key_str] = _mask_query(item)
            elif key_str == "error" and isinstance(item, str):
                masked[key_str] = _redact_error(item)
            else:
                masked[key_str] = mask_pii(item)
        return masked
    return value


class JsonlTraceSink:
    """Append-only JSONL trace sink."""

    def __init__(self, event_log: str, metrics_log: str, enabled: bool = True, max_field_chars: int = 1200):
        self.event_log = Path(event_log)
        self.metrics_log = Path(metrics_log)
        self.enabled = enabled
        self.max_field_chars = max_field_chars
        self._lock = threading.Lock()
        if self.enabled:
            self.event_log.parent.mkdir(parents=True, exist_ok=True)
            self.metrics_log.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_config(cls) -> "JsonlTraceSink":
        return cls(
            event_log=OBSERVABILITY_CONFIG["event_log"],
            metrics_log=OBSERVABILITY_CONFIG["metrics_log"],
            enabled=OBSERVABILITY_CONFIG["enabled"],
            max_field_chars=OBSERVABILITY_CONFIG["max_field_chars"],
        )

    def write_event(self, event: Dict[str, Any]):
        if not self.enabled:
            return
        payload = truncate_value(event, self.max_field_chars)
        with self._lock:
            with open(self.event_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def write_metrics(self, metrics: Dict[str, Any]):
        if not self.enabled:
            return
        payload = truncate_value(metrics, self.max_field_chars)
        with self._lock:
            with open(self.metrics_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


@dataclass
class TraceContext:
    """Per-request trace context."""

    user_id: str
    session_id: str
    query: str
    sink: JsonlTraceSink = field(default_factory=JsonlTraceSink.from_config)
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: float = field(default_factory=time.perf_counter)
    agent_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)

    def emit(self, event_type: str, stage: str, data: Optional[Dict[str, Any]] = None):
        safe_data = mask_pii(data or {})
        event = {
            "timestamp": utc_now(),
            "trace_id": self.trace_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "event_type": event_type,
            "stage": stage,
            "data": safe_data,
        }
        self.events.append(event)
        self.sink.write_event(event)

    def record_agent_event(self, event_type: str, data: Dict[str, Any]):
        agent = data.get("agent") or "unknown"
        stats = self.agent_stats.setdefault(
            agent,
            {
                "calls": 0,
                "success": 0,
                "errors": 0,
                "timeouts": 0,
                "total_duration_ms": 0,
            },
        )

        if event_type == "agent_start":
            stats["calls"] += 1
        elif event_type == "agent_done":
            stats["success"] += 1
            stats["total_duration_ms"] += int(data.get("duration_ms") or 0)
        elif event_type == "agent_error":
            stats["errors"] += 1
            stats["total_duration_ms"] += int(data.get("duration_ms") or 0)
            if "超时" in str(data.get("error", "")) or "timeout" in str(data.get("error", "")).lower():
                stats["timeouts"] += 1

        self.emit(event_type, "agent", data)

    def finish(
        self,
        status: str,
        *,
        intention_data: Optional[Dict[str, Any]] = None,
        result_data: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        duration_ms = int((time.perf_counter() - self.started_at) * 1000)
        agent_metrics = {}
        for agent, stats in self.agent_stats.items():
            calls = stats.get("calls", 0) or 0
            total_duration = stats.get("total_duration_ms", 0) or 0
            agent_metrics[agent] = {
                **stats,
                "avg_duration_ms": round(total_duration / calls, 2) if calls else 0,
            }

        intents = []
        if isinstance(intention_data, dict):
            intents = [
                item.get("type")
                for item in intention_data.get("intents", [])
                if isinstance(item, dict) and item.get("type")
            ]

        result_status = result_data.get("status") if isinstance(result_data, dict) else None
        metrics = {
            "timestamp": utc_now(),
            "trace_id": self.trace_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "status": status,
            "result_status": result_status,
            "duration_ms": duration_ms,
            "intents": intents,
            "agent_metrics": agent_metrics,
            "agent_count": len(agent_metrics),
            "error": _redact_error(error) if error and OBSERVABILITY_CONFIG.get("mask_pii", True) else error,
        }
        self.sink.write_metrics(metrics)
        self.emit("trace_end", "trace", metrics)
        return metrics
