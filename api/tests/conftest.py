"""Shared test fixtures.

By default every test runs with auth bypassed — require_user is overridden to
return a fixed username so the existing endpoint tests don't need to log in.
Auth-flow tests (test_auth.py) pop this override to exercise the real guard.
"""
import os

# AccaBonus and Cards/Corners are hidden in production (coupon-tracker-only
# mode, PLAN.md v5) — their API routers only mount when these flags are set.
# The tests still cover that kept code, so enable both flags before `app.main`
# is imported (routers are mounted at import time).
os.environ.setdefault("ENABLE_ACCABONUS", "true")
os.environ.setdefault("ENABLE_CARDS", "true")

import pytest

from app.main import app
from app.security import require_user


@pytest.fixture(autouse=True)
def bypass_auth():
    app.dependency_overrides[require_user] = lambda: "testuser"
    yield
    app.dependency_overrides.pop(require_user, None)
