from __future__ import annotations

import pytest


MODULE_MARKERS = {
    "test_event_collection_agent.py": [pytest.mark.llm],
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        for marker in MODULE_MARKERS.get(item.path.name, []):
            item.add_marker(marker)
