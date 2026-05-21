"""
兼容导出 EventCollectionAgent。

实际实现保留在 .claude/skills/event-collection/script/agent.py，
这里通过按文件加载的方式暴露给 agents 包，避免测试和旧代码 import 失败。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_script_path = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "event-collection" / "script" / "agent.py"
_spec = importlib.util.spec_from_file_location("_event_collection_skill_agent", _script_path)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise ImportError(f"Cannot load EventCollectionAgent from {_script_path}")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

EventCollectionAgent = _module.EventCollectionAgent

__all__ = ["EventCollectionAgent"]
