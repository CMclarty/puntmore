"""The 2-Up module is a one-way value feed. What was cut stays cut: these names
must not import and these routes must not exist."""
import importlib

import pytest


@pytest.mark.parametrize("name", ["app.twoup.decision", "app.hub", "app.routers.hub_ws",
                                  "app.betfinder", "app.tip_migration", "app.routers.betfinder",
                                  "app.twoup.ledger"])
def test_removed_modules_do_not_import(name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(name)


def _paths(routes, prefix=""):
    """Every route path, walking the routers FastAPI keeps un-flattened."""
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _paths(inner.routes, prefix + r.include_context.prefix)
        else:
            yield prefix + getattr(r, "path", "")


def test_no_hub_routes():
    from app.main import app

    paths = set(_paths(app.routes))
    assert "/2up/feed" in paths          # the walk sees the mounted routers
    assert not [p for p in paths if p.startswith(("/2up/ws", "/2up/hub"))]


def test_no_betfinder_routes():
    from app.main import app

    paths = set(_paths(app.routes))
    assert "/2up/feed" in paths
    assert not [p for p in paths if p.startswith("/2up/betfinder")]


def test_no_ledger_or_client_routes():
    from app.main import app

    paths = set(_paths(app.routes))
    assert "/2up/feed" in paths
    assert not [p for p in paths if p.startswith(("/2up/ledger", "/2up/clients"))]
