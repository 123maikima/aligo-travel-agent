#!/usr/bin/env python
"""
Web API route registration tests.
"""
from __future__ import annotations

import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from travel_agent.web_api import app


def test_web_api_app_builds():
    assert app is not None
    routes = {getattr(route, "path", "") for route in app.routes}
    assert "/health" in routes
    assert "/api/v1/auth/token" in routes
    assert "/api/v1/chat" in routes
    assert "/api/v1/chat/sync" in routes
