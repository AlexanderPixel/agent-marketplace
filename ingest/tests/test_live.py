"""Smoke test against the real sources: `uv run pytest -m live -k <name>`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tenderhub import registry
from tenderhub.runner import Runner
from tenderhub.store import Store

pytestmark = pytest.mark.live


@pytest.mark.parametrize("name", list(registry.all_connectors()))
async def test_live_sync(name, tmp_path):
    cls = registry.get(name)
    store = Store(tmp_path / "live.db")
    runner = Runner(store)
    if cls.missing_env(runner.env):
        pytest.skip(f"needs {cls.required_env}")
    try:
        res = await runner.sync(name, since=datetime.now(UTC) - timedelta(days=4), max_notices=60)
    finally:
        await runner.aclose()
    assert res.status == "ok", res.error
    assert res.seen > 0, "no notices in the last 4 days — wrong endpoint or parser?"
    assert res.invalid == 0, res.problems
    rows = store.db.execute("SELECT COUNT(DISTINCT source_id) c, COUNT(*) n FROM notices").fetchone()
    assert rows["c"] == rows["n"] > 0
