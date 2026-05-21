"""
兼容导出 RAGKnowledgeAgent。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_script_path = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "ask-question" / "script" / "agent.py"
_spec = importlib.util.spec_from_file_location("_rag_knowledge_skill_agent", _script_path)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise ImportError(f"Cannot load RAGKnowledgeAgent from {_script_path}")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

RAGKnowledgeAgent = _module.RAGKnowledgeAgent

__all__ = ["RAGKnowledgeAgent"]
