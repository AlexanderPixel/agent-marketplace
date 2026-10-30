"""Every discovered connector must satisfy the Connector contract and ship its own fixture test."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from tenderhub import registry

CONNECTORS = registry.all_connectors()
TESTS = Path(__file__).parent / "connectors"
COUNTRY = re.compile(r"^([A-Z]{2}|EU|NATO|INT)$")


def test_no_import_errors():
    assert registry.import_errors == {}


@pytest.mark.parametrize("name", list(CONNECTORS))
def test_connector_contract(name):
    cls = CONNECTORS[name]
    assert re.fullmatch(r"[a-z][a-z0-9_]*", cls.name)
    assert cls.title and cls.homepage.startswith("http")
    assert cls.countries and all(COUNTRY.match(c) for c in cls.countries)
    assert cls.kind in {"portal", "platform", "defence_agency", "aggregator"}
    assert inspect.isasyncgenfunction(cls.sync), "sync must be an async generator (use `yield`)"
    assert cls.requests_per_second <= 5, "be polite: at most 5 requests/second per source"
    if cls.supports_refresh:
        assert cls.refresh is not registry.Connector.refresh, "supports_refresh=True but refresh not implemented"


@pytest.mark.parametrize("name", list(CONNECTORS))
def test_connector_has_fixture_test(name):
    module = CONNECTORS[name].__module__.rsplit(".", 1)[-1]
    assert (TESTS / f"test_{module}.py").exists(), f"missing tests/connectors/test_{module}.py"
