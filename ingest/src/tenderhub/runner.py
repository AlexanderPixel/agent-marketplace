"""Runs connectors: incremental sync, refresh of open notices, and the always-on scheduler loop."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx

from . import registry
from .classify import classify_defence
from .connector import Checkpoint, Connector, Context, StoredRef
from .http import DEFAULT_USER_AGENT, Http, make_client
from .models import Notice, NoticeType, Status, validate_notice
from .store import Store

log = logging.getLogger("tenderhub.runner")
MAX_BACKOFF = timedelta(hours=6)


@dataclass
class RunResult:
    source: str
    kind: str
    status: str = "ok"            # ok | error | skipped
    since: datetime | None = None
    seen: int = 0
    inserted: int = 0
    updated: int = 0
    invalid: int = 0
    requests: int = 0
    error: str | None = None
    problems: list[str] = field(default_factory=list)  # first few validation problems, for debugging

    def line(self) -> str:
        s = f"{self.source:28} {self.kind:7} {self.status:7} seen={self.seen} new={self.inserted} " \
            f"changed={self.updated} invalid={self.invalid} req={self.requests}"
        return s + (f"  ERROR: {self.error[:200]}" if self.error else "")


def derive_status(n: Notice, now: datetime) -> Status:
    if n.status is not Status.UNKNOWN:
        return n.status
    match n.notice_type:
        case NoticeType.AWARD:
            return Status.AWARDED
        case NoticeType.CANCELLATION:
            return Status.CANCELLED
        case NoticeType.TENDER if n.deadline_at:
            return Status.OPEN if n.deadline_at > now else Status.CLOSED
    return Status.UNKNOWN


class Runner:
    def __init__(self, store: Store, *, client: httpx.AsyncClient | None = None, env: dict[str, str] | None = None,
                 concurrency: int = 8):
        self.store = store
        self.env = env if env is not None else dict(os.environ)
        self.client = client or make_client(self.env.get("TENDERHUB_USER_AGENT") or DEFAULT_USER_AGENT)
        self.concurrency = concurrency

    async def aclose(self) -> None:
        await self.client.aclose()

    def connector(self, name: str) -> Connector:
        cls = registry.get(name)
        clog = logging.getLogger(f"tenderhub.connectors.{name}")
        return cls(Context(http=Http(self.client, rps=cls.requests_per_second, log=clog), log=clog, env=self.env))

    def _store_notice(self, name: str, n: Notice, res: RunResult, now: datetime) -> None:
        res.seen += 1
        problems = validate_notice(n)
        if n.source != name:
            problems.append(f"notice.source {n.source!r} != connector name {name!r}")
        if problems:
            res.invalid += 1
            if len(res.problems) < 10:
                res.problems.append(f"{n.source_id}: {'; '.join(problems)}")
            return
        n.status = derive_status(n, now)
        is_def, reasons = classify_defence(n)
        outcome = self.store.upsert(n, is_def, reasons)
        res.inserted += outcome == "inserted"
        res.updated += outcome == "updated"

    async def sync(self, name: str, *, since: datetime | None = None, max_notices: int | None = None,
                   full: bool = False) -> RunResult:
        """One incremental sync. `since` overrides the stored high-water mark (backfill);
        `max_notices` stops early (smoke tests) without advancing the high-water mark."""
        cls = registry.get(name)
        res = RunResult(name, "sync")
        if missing := cls.missing_env(self.env):
            res.status, res.error = "skipped", f"missing env: {', '.join(missing)}"
            return res
        st = self.store.get_state(name)
        started = datetime.now(UTC)
        explicit = since is not None
        if since is None:
            since = st.high_water - cls.overlap if st.high_water and not full else started - cls.first_sync_lookback
        state = {} if (full or explicit) else st.state
        res.since = since
        run_id = self.store.start_run(name, "sync", since)
        conn = self.connector(name)
        complete = False
        gen = conn.sync(since, state)
        try:
            async for item in gen:
                if isinstance(item, Checkpoint):
                    self.store.save_checkpoint(name, item.state)
                    continue
                self._store_notice(name, item, res, started)
                if max_notices and res.seen >= max_notices:
                    break
            else:
                complete = True
            # only advance the high-water mark if this run covered everything back to the old one
            advance = complete and (st.high_water is None or since <= st.high_water)
            self.store.finish_sync(name, ok=True, high_water=started if advance else None,
                                   next_run_at=started + cls.poll_interval)
        except Exception as e:  # noqa: BLE001 — a failing source is recorded and retried later, never fatal
            res.status, res.error = "error", f"{type(e).__name__}: {e}"
            backoff = min(MAX_BACKOFF, timedelta(minutes=5) * 2 ** st.consecutive_failures)
            self.store.finish_sync(name, ok=False, high_water=None, next_run_at=started + backoff, error=res.error)
            log.exception("sync %s failed", name)
        finally:
            await gen.aclose()
            res.requests = conn.http.requests
            self.store.finish_run(run_id, status=res.status, seen=res.seen, inserted=res.inserted,
                                  updated=res.updated, invalid=res.invalid, requests=res.requests, error=res.error)
        if res.problems:
            log.warning("%s: %d invalid notices, e.g. %s", name, res.invalid, res.problems[:3])
        return res

    async def refresh(self, name: str, *, limit: int = 200, older_than: timedelta = timedelta(hours=6)) -> RunResult:
        """Re-fetch still-open notices so deadline changes, amendments and new documents are picked up."""
        cls = registry.get(name)
        res = RunResult(name, "refresh")
        if not cls.supports_refresh:
            res.status = "skipped"
            return res
        now = datetime.now(UTC)
        rows = self.store.refresh_candidates(name, now - older_than, limit)
        run_id = self.store.start_run(name, "refresh", None)
        conn = self.connector(name)
        try:
            for row in rows:
                ref = StoredRef(row["source_id"], row["url"], json.loads(row["refs"]), json.loads(row["raw"]))
                n = await conn.refresh(ref)
                if n is not None:
                    self._store_notice(name, n, res, now)
                self.store.mark_refreshed(name, row["source_id"])
        except Exception as e:  # noqa: BLE001
            res.status, res.error = "error", f"{type(e).__name__}: {e}"
            log.exception("refresh %s failed", name)
        finally:
            res.requests = conn.http.requests
            self.store.finish_run(run_id, status=res.status, seen=res.seen, inserted=res.inserted,
                                  updated=res.updated, invalid=res.invalid, requests=res.requests, error=res.error)
        return res

    def due(self, names: list[str]) -> list[str]:
        now = datetime.now(UTC)
        out = []
        for name in names:
            cls = registry.get(name)
            if cls.missing_env(self.env):
                continue
            nxt = self.store.get_state(name).next_run_at
            if nxt is None or nxt <= now:
                out.append(name)
        return out

    async def run_due(self, names: list[str] | None = None) -> list[RunResult]:
        names = names or list(registry.all_connectors())
        self.store.close_expired()
        sem = asyncio.Semaphore(self.concurrency)

        async def one(name: str) -> list[RunResult]:
            async with sem:
                results = [await self.sync(name)]
                if registry.get(name).supports_refresh:
                    results.append(await self.refresh(name))
                return results

        batches = await asyncio.gather(*(one(n) for n in self.due(names)))
        return [r for b in batches for r in b]

    async def daemon(self, names: list[str] | None = None, tick: float = 60.0) -> None:
        """Forever: sync every source when its poll interval is up, refresh open notices, close expired ones."""
        log.info("daemon started for %s", ", ".join(names or registry.all_connectors()))
        while True:
            for r in await self.run_due(names):
                log.info(r.line())
            await asyncio.sleep(tick)
