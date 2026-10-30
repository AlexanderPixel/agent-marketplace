"""The contract every source connector implements.

A connector is one module in `tenderhub/connectors/` defining a `Connector` subclass. It is discovered
automatically (no registration list to edit). The runner owns scheduling, storage, change detection
and retries; a connector only knows how to talk to its source.

Minimal connector:

    class Example(Connector):
        name = "example"
        title = "Example portal"
        countries = ("PL",)
        homepage = "https://example.gov.pl"

        async def sync(self, since, state):
            page = 1
            while True:
                data = await self.http.get_json(API, params={"from": since.date().isoformat(), "page": page})
                for item in data["items"]:
                    yield self.to_notice(item)
                yield Checkpoint({"page": page})
                if not data["next"]:
                    break
                page += 1
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from .http import Http
from .models import Notice


@dataclass(slots=True)
class Checkpoint:
    """Yielded by `sync` when it is safe to resume from here. `state` is persisted and passed back next run."""

    state: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StoredRef:
    """What the runner knows about a stored notice, passed to `refresh`."""

    source_id: str
    url: str
    refs: dict[str, str]
    raw: dict[str, Any]


@dataclass
class Context:
    http: Http
    log: logging.Logger
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))

    def now(self) -> datetime:
        return datetime.now(UTC)


class Connector(ABC):
    # --- identity (required) ---
    name: ClassVar[str]                    # stable snake_case id; stored as notices.source
    title: ClassVar[str]                   # human name
    countries: ClassVar[tuple[str, ...]]   # ISO alpha-2 codes covered; ("EU",) for EU-wide
    homepage: ClassVar[str]

    # --- behaviour (override as needed) ---
    kind: ClassVar[str] = "portal"         # portal | platform | defence_agency | aggregator
    poll_interval: ClassVar[timedelta] = timedelta(hours=1)    # how often the scheduler re-syncs
    overlap: ClassVar[timedelta] = timedelta(days=1)           # re-read window before the high-water mark
    first_sync_lookback: ClassVar[timedelta] = timedelta(days=7)
    requests_per_second: ClassVar[float] = 2.0
    supports_refresh: ClassVar[bool] = False                   # implements `refresh` for open notices
    required_env: ClassVar[tuple[str, ...]] = ()               # e.g. ("SAM_API_KEY",); missing -> skipped

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.http = ctx.http
        self.log = ctx.log

    @abstractmethod
    def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        """Yield every notice published or changed at/after `since` (tz-aware UTC).

        `state` is whatever the last yielded Checkpoint held (empty on first run); use it for cursors
        that are more precise than `since`. Yielding the same notice twice is fine — storage dedups
        on (source, source_id) and only records a new version when the content changed.
        Must be an async generator. Should not swallow errors: raise and the runner retries later.
        """

    async def refresh(self, ref: StoredRef) -> Notice | None:
        """Re-fetch one stored notice (e.g. still-open tender) to pick up deadline changes, Q&A, documents.
        Return None if it can no longer be found. Only called when `supports_refresh` is True."""
        return None

    @classmethod
    def missing_env(cls, env: dict[str, str]) -> list[str]:
        return [k for k in cls.required_env if not env.get(k)]
