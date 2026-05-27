"""Evaluate the local RAG knowledge base against a small golden set."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agentscope.message import Msg

from travel_agent.agents.rag_knowledge_agent import RAGKnowledgeAgent
from travel_agent.llm import create_model_factory


DEFAULT_GOLDEN_SET = PROJECT_ROOT / "tests" / "fixtures" / "rag_qa_golden.json"
DEFAULT_KNOWLEDGE_BASE = (
    PROJECT_ROOT / ".claude" / "skills" / "ask-question" / "data" / "rag_knowledge"
)


def load_cases(path: Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, list):
        raise ValueError("Golden set must be a JSON array")
    return [case for case in payload if isinstance(case, dict)]


def contains_expected_terms(text: str, terms: list[str]) -> bool:
    return all(term in text for term in terms)


async def evaluate(args: argparse.Namespace) -> int:
    model = create_model_factory()("rag_knowledge") if args.with_llm else None
    agent = RAGKnowledgeAgent(
        name="RAGKnowledgeAgent",
        model=model,
        knowledge_base_path=str(args.knowledge_base),
        collection_name=args.collection,
        top_k=args.top_k,
    )

    if not agent.initialized:
        print("RAG agent is not initialized. Run travel_agent/scripts/init_knowledge_base.py first.")
        return 2

    cases = load_cases(args.golden_set)
    passed = 0
    try:
        for case in cases:
            question = str(case.get("question", ""))
            expected_terms = [str(term) for term in case.get("expected_terms", [])]

            docs = agent.search_knowledge(question, top_k=args.top_k)
            retrieved_text = "\n".join(str(doc.get("content", "")) for doc in docs)
            retrieval_ok = bool(docs) and contains_expected_terms(retrieved_text, expected_terms)

            answer_ok = True
            answer = ""
            if args.with_llm:
                msg = Msg(name="User", content=question, role="user")
                result = await agent.reply(msg)
                result_data = json.loads(result.content)
                answer = str(result_data.get("answer", ""))
                answer_ok = contains_expected_terms(answer, expected_terms)

            ok = retrieval_ok and answer_ok
            passed += int(ok)
            status = "PASS" if ok else "FAIL"
            print(f"[{status}] {case.get('id', question)}")
            print(f"  question: {question}")
            print(f"  retrieved: {len(docs)}")
            if args.with_llm:
                print(f"  answer: {answer[:160]}")

        total = len(cases)
        print(f"\nRAG QA evaluation: {passed}/{total} passed")
        return 0 if passed == total else 1
    finally:
        agent.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("--knowledge-base", type=Path, default=DEFAULT_KNOWLEDGE_BASE)
    parser.add_argument("--collection", default="business_travel_knowledge")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--with-llm", action="store_true", help="Generate answers and check answer terms")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return asyncio.run(evaluate(args))


if __name__ == "__main__":
    sys.exit(main())
