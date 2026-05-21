"""
兼容导出 MemoryQueryAgent。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_script_path = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "memory-query" / "script" / "agent.py"
_spec = importlib.util.spec_from_file_location("_memory_query_skill_agent", _script_path)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise ImportError(f"Cannot load MemoryQueryAgent from {_script_path}")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

MemoryQueryAgent = _module.MemoryQueryAgent

__all__ = ["MemoryQueryAgent"]
