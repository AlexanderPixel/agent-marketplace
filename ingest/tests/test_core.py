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


# ---------- cross-source duplicates ----------

def _cluster(store, source, sid):
    return store.db.execute("SELECT cluster_id, is_primary FROM notices WHERE source=? AND source_id=?",
                            (source, sid)).fetchone()


def test_same_tender_on_two_sources_is_one_cluster_via_shared_id(store):
    uuid = "0004005b-167c-467c-9b68-5a64838d1f7d"
    store.upsert(notice("745279-2026", source="ted", refs={"procedure": uuid}))
    store.upsert(notice("x1", source="oeffentlichevergabe", refs={"procedure": uuid.upper()}))
    a, b = _cluster(store, "ted", "745279-2026"), _cluster(store, "oeffentlichevergabe", "x1")
    assert a["cluster_id"] == b["cluster_id"]
    assert a["is_primary"] + b["is_primary"] == 1


def test_ted_number_links_national_copy(store):
    store.upsert(notice("745650-2026", source="ted"))
    store.upsert(notice("446357", source="tenderned", refs={"ted": "00745650-2026"}))
    assert _cluster(store, "ted", "745650-2026")["cluster_id"] == _cluster(store, "tenderned", "446357")["cluster_id"]


def test_fingerprint_links_platform_and_bulletin(store):
    deadline = NOW + timedelta(days=5)
    t = "Dostawa opon do pojazdów osobowych i dostawczych"
    store.upsert(notice("p1", source="platformazakupowa", title=t, buyer_country="PL", deadline_at=deadline))
    store.upsert(notice("b1", source="bzp", title=t.upper() + ".", buyer_country="PL", deadline_at=deadline))
    store.upsert(notice("b2", source="bzp", title=t, buyer_country="PL", deadline_at=deadline + timedelta(days=3)))
    assert _cluster(store, "platformazakupowa", "p1")["cluster_id"] == _cluster(store, "bzp", "b1")["cluster_id"]
    assert _cluster(store, "bzp", "b2")["cluster_id"] != _cluster(store, "bzp", "b1")["cluster_id"]


def test_bridge_notice_merges_two_existing_clusters_and_relink_matches(store):
    store.upsert(notice("a", source="s1", refs={"ocid": "ocds-1"}))
    store.upsert(notice("b", source="s2", refs={"ted": "1-2026"}))
    assert _cluster(store, "s1", "a")["cluster_id"] != _cluster(store, "s2", "b")["cluster_id"]
    store.upsert(notice("c", source="s3", refs={"ocid": "ocds-1", "ted": "1-2026"}))
    ids = {_cluster(store, s, i)["cluster_id"] for s, i in (("s1", "a"), ("s2", "b"), ("s3", "c"))}
    assert len(ids) == 1
    assert store.db.execute("SELECT SUM(is_primary) FROM notices").fetchone()[0] == 1
    assert store.relink_all() == (3, 1)


def test_short_generic_titles_are_not_fingerprinted(store):
    store.upsert(notice("1", source="s1", title="Dostawa paliwa", buyer_country="PL"))
    store.upsert(notice("2", source="s2", title="Dostawa paliwa", buyer_country="PL"))
    assert _cluster(store, "s1", "1")["cluster_id"] != _cluster(store, "s2", "2")["cluster_id"]
