"""Store, runner and classifier behaviour, using an in-memory fake connector."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tenderhub import registry
from tenderhub.classify import classify_defence
from tenderhub.cli import parse_since
from tenderhub.connector import Checkpoint, Connector
from tenderhub.models import Notice, NoticeType, Status, normalize_cpv, validate_notice
from tenderhub.runner import Runner, derive_status

NOW = datetime.now(UTC)


def notice(sid: str = "1", **kw) -> Notice:
    base = dict(source="fake", source_id=sid, url=f"https://x.test/{sid}", title=f"Tender {sid}",
                notice_type=NoticeType.TENDER, published_at=NOW, deadline_at=NOW + timedelta(days=10))
    return Notice(**(base | kw))


class Fake(Connector):
    name = "fake"
    title = "Fake"
    countries = ("PL",)
    homepage = "https://x.test"
    batches: list = []
    calls: list = []
    fail = False

    async def sync(self, since, state):
        Fake.calls.append((since, dict(state)))
        if Fake.fail:
            raise RuntimeError("source down")
        for item in Fake.batches:
            yield item


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(registry, "_cache", {"fake": Fake})
    Fake.batches, Fake.calls, Fake.fail = [], [], False
    return Fake


@pytest.fixture
async def runner(store):
    r = Runner(store, client=httpx.AsyncClient(), env={})
    yield r
    await r.aclose()


# ---------- store ----------

def test_upsert_insert_unchanged_update_keeps_history(store):
    assert store.upsert(notice()) == "inserted"
    assert store.upsert(notice()) == "unchanged"
    assert store.upsert(notice(title="Tender 1 (amended deadline)")) == "updated"
    row = store.db.execute("SELECT version, title FROM notices").fetchone()
    assert row["version"] == 2 and "amended" in row["title"]
    assert store.db.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 2


def test_raw_only_change_is_not_a_new_version(store):
    store.upsert(notice(raw={"a": 1}))
    assert store.upsert(notice(raw={"a": 2})) == "unchanged"


def test_fts_search_and_filters(store):
    store.upsert(notice("1", title="Dostawa amunicji 155 mm", buyer_country="PL", cpv_codes=["35330000"]), True)
    store.upsert(notice("2", title="Catering services", buyer_country="DE"))
    assert [r["source_id"] for r in store.search("amunicji")] == ["1"]
    assert [r["source_id"] for r in store.search(country="de")] == ["2"]
    assert [r["source_id"] for r in store.search(cpv_prefix="35")] == ["1"]
    assert [r["source_id"] for r in store.search(defence=True)] == ["1"]
    store.upsert(notice("2", title="Catering and laundry services", buyer_country="DE"))
    assert [r["source_id"] for r in store.search("laundry")] == ["2"]


def test_close_expired(store):
    store.upsert(notice("1", status=Status.OPEN, deadline_at=NOW - timedelta(hours=1)))
    assert store.close_expired() == 1


# ---------- runner ----------

async def test_first_sync_uses_lookback_then_high_water_minus_overlap(fake, runner, store):
    fake.batches = [notice("1"), Checkpoint({"cursor": "abc"}), notice("2")]
    r1 = await runner.sync("fake")
    assert (r1.status, r1.inserted) == ("ok", 2)
    since1, state1 = fake.calls[0]
    assert abs(since1 - (NOW - Fake.first_sync_lookback)) < timedelta(minutes=1) and state1 == {}

    fake.batches = [notice("2", title="changed")]
    r2 = await runner.sync("fake")
    assert (r2.inserted, r2.updated) == (0, 1)
    since2, state2 = fake.calls[1]
    assert state2 == {"cursor": "abc"}
    high_water = store.get_state("fake").high_water
    assert since2 < high_water and high_water - since2 >= Fake.overlap - timedelta(minutes=1)


async def test_limited_run_does_not_advance_high_water(fake, runner, store):
    fake.batches = [notice(str(i)) for i in range(5)]
    res = await runner.sync("fake", max_notices=2)
    assert res.seen == 2
    assert store.get_state("fake").high_water is None


async def test_failure_is_recorded_with_backoff(fake, runner, store):
    fake.fail = True
    res = await runner.sync("fake")
    assert res.status == "error" and "source down" in res.error
    st = store.get_state("fake")
    assert st.consecutive_failures == 1 and st.next_run_at > NOW
    assert runner.due(["fake"]) == []


async def test_invalid_notices_are_counted_not_stored(fake, runner, store):
    fake.batches = [notice("1", url="/relative"), notice("2", cpv_codes=["35-bad"]), notice("3")]
    res = await runner.sync("fake")
    assert (res.seen, res.invalid, res.inserted) == (3, 2, 1)


async def test_status_derived_and_defence_flagged(fake, runner, store):
    fake.batches = [notice("1", buyer_name="3 Regionalna Baza Logistyczna", deadline_at=NOW + timedelta(days=3))]
    await runner.sync("fake")
    row = store.db.execute("SELECT status, is_defence FROM notices").fetchone()
    assert (row["status"], row["is_defence"]) == ("open", 1)


# ---------- helpers ----------

def test_derive_status():
    assert derive_status(notice(deadline_at=NOW - timedelta(days=1)), NOW) is Status.CLOSED
    assert derive_status(notice(notice_type=NoticeType.AWARD), NOW) is Status.AWARDED
    assert derive_status(notice(status=Status.CANCELLED), NOW) is Status.CANCELLED


def test_classify_defence():
    assert classify_defence(notice(legal_basis="32009L0081"))[0]
    assert classify_defence(notice(cpv_codes=["35300000"]))[0]
    assert not classify_defence(notice(buyer_name="Gmina Kraków", cpv_codes=["45000000"]))[0]


def test_validate_and_cpv():
    assert normalize_cpv("35300000-6 (Broń)") == "35300000"
    assert validate_notice(notice()) == []
    assert validate_notice(notice(published_at=datetime(2026, 1, 1)))  # naive datetime rejected


def test_parse_since():
    assert abs(parse_since("3d") - (NOW - timedelta(days=3))) < timedelta(minutes=1)
    assert parse_since("2026-10-01") == datetime(2026, 10, 1, tzinfo=UTC)
