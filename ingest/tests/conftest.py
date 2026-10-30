"""Shared test helpers. Connector tests replay recorded responses with respx; live tests hit the network."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from tenderhub.connector import Checkpoint, Connector, Context
from tenderhub.http import Http
from tenderhub.models import Notice
from tenderhub.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_path(source: str, name: str) -> Path:
    return FIXTURES / source / name


def load_json(source: str, name: str) -> Any:
    return json.loads(fixture_path(source, name).read_text())


def load_text(source: str, name: str) -> str:
    return fixture_path(source, name).read_text()


def make_connector(cls: type[Connector], env: dict[str, str] | None = None) -> tuple[Connector, httpx.AsyncClient]:
    """Connector wired to a plain httpx client (respx intercepts it) with fast retries."""
    client = httpx.AsyncClient(follow_redirects=True)
    log = logging.getLogger(f"test.{cls.name}")
    http = Http(client, rps=1000, max_retries=1, log=log)
    return cls(Context(http=http, log=log, env=env or {})), client


async def collect(gen: AsyncIterator[Notice | Checkpoint]) -> tuple[list[Notice], list[Checkpoint]]:
    notices, checkpoints = [], []
    async for item in gen:
        (checkpoints if isinstance(item, Checkpoint) else notices).append(item)
    return notices, checkpoints


async def run_sync(cls: type[Connector], since: datetime, state: dict | None = None,
                   env: dict[str, str] | None = None) -> tuple[list[Notice], list[Checkpoint]]:
    conn, client = make_connector(cls, env)
    try:
        return await collect(conn.sync(since, state or {}))
    finally:
        await client.aclose()


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    yield s
    s.close()
