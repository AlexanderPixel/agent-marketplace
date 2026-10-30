"""Prozorro connector against recorded public-api responses (feed page + tender details)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
import respx

from tenderhub.connector import Checkpoint, StoredRef
from tenderhub.connectors.prozorro import API, Prozorro
from tenderhub.http import HttpError
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_json, make_connector, run_sync

SINCE = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)
KYIV = timezone(timedelta(hours=2))
OPEN_IDS = ["3108fae8140a489c9d67473d301a8ec5", "a679f8889d0d4a18ad2a38f822875be0",
            "99dbb29ef4cb490ea2a0a5160b07acfa"]


RECENT = {"3108fae8140a489c9d67473d301a8ec5": "2026-10-30T16:20:00.225905+02:00",
          "a679f8889d0d4a18ad2a38f822875be0": "2026-10-30T16:20:07.933287+02:00",
          "99dbb29ef4cb490ea2a0a5160b07acfa": "2026-10-30T16:20:47.419973+02:00"}


def _mock_details() -> dict[str, respx.Route]:
    return {i: respx.get(f"{API}/{i}").mock(return_value=httpx.Response(200, json=load_json("prozorro", f"tender_{i}.json")))
            for i in OPEN_IDS}


@respx.mock
async def test_sync_walks_feed_fetches_only_open_tenders_and_maps_fields():
    feed = respx.get(API).mock(side_effect=[
        httpx.Response(200, json=load_json("prozorro", "feed_page1.json")),
        httpx.Response(200, json=load_json("prozorro", "feed_page2.json")),
    ])
    details = _mock_details()

    notices, checkpoints = await run_sync(Prozorro, SINCE)

    # feed: starts at `since` as a unix timestamp, then follows next_page.offset until an empty page
    assert feed.call_count == 2
    p1, p2 = (dict(c.request.url.params) for c in feed.calls)
    assert float(p1["offset"]) == SINCE.timestamp()
    assert p1["opt_fields"] == "status" and p1["limit"] == "1000"
    assert p2["offset"] == "1793369999.5.1.page2cursor"
    # only the 3 tenders still in active.tendering / active.enquiries are fetched (4 others skipped)
    assert all(r.call_count == 1 for r in details.values())
    assert len(notices) == 3
    assert [p for n in notices for p in validate_notice(n)] == []
    # resume point = last change seen, rewound 5 minutes; versions fetched inside that window are remembered
    assert checkpoints[-1].state == {"offset": "1793369802.481090", "recent": RECENT}

    by_id = {n.source_id: n for n in notices}
    steel = by_id["UA-2026-10-30-009334-a"]  # simplified procurement by a military unit
    assert steel.source == "prozorro"
    assert steel.url == "https://prozorro.gov.ua/tender/UA-2026-10-30-009334-a"
    assert steel.title == "Металопрокат"
    assert steel.notice_type is NoticeType.TENDER and steel.status is Status.OPEN
    assert steel.buyer_name == "в/ч А0297" and steel.buyer_id == "08419841"
    assert steel.buyer_country == "UA" and steel.buyer_city == "Київ"
    assert steel.cpv_codes == ["14620000"]
    assert steel.procedure_type == "belowThreshold"
    assert steel.published_at == datetime(2026, 10, 30, 16, 20, 7, 933287, tzinfo=KYIV)
    assert steel.deadline_at == datetime(2026, 11, 10, 16, 19, 57, tzinfo=KYIV)
    assert steel.value_amount == 398600.0 and steel.value_currency == "UAH"
    assert steel.language == "uk"
    assert [d.kind for d in steel.documents] == ["contractProforma", "biddingDocuments"]  # .p7s signature dropped
    assert steel.documents[0].url.startswith("https://public-docs.prozorro.gov.ua/get/")
    assert steel.refs == {"ocid": "ocds-be6bcu-UA-2026-10-30-009334-a", "procedure": "UA-2026-10-30-009334-a",
                          "prozorro_id": "a679f8889d0d4a18ad2a38f822875be0"}
    assert "contactPoint" not in steel.raw["procuringEntity"] and steel.raw["id"] == OPEN_IDS[1]

    mattress = by_id["UA-2026-10-16-011819-a"]
    assert mattress.procedure_type == "aboveThreshold"
    assert mattress.buyer_city == "Запоріжжя"
    assert mattress.cpv_codes == ["39140000"]
    assert mattress.deadline_at == datetime(2026, 11, 2, 1, 0, tzinfo=KYIV)
    assert mattress.title.startswith("код ДК 021:2015 – 39140000-5 Меблі")

    coffee = by_id["UA-2026-10-30-009409-a"]
    assert coffee.title == "Кава в зернах" and coffee.procedure_type == "priceQuotation"
    assert coffee.buyer_id == "20077720" and coffee.cpv_codes == ["15860000"]


@respx.mock
async def test_resumes_from_checkpointed_offset_and_empty_feed_still_checkpoints():
    feed = respx.get(API).mock(return_value=httpx.Response(200, json=load_json("prozorro", "feed_page2.json")))
    notices, checkpoints = await run_sync(Prozorro, SINCE, state={"offset": "1793369802.481090", "recent": RECENT})
    assert notices == []
    assert dict(feed.calls[0].request.url.params)["offset"] == "1793369802.481090"
    assert checkpoints == [checkpoints[0]]
    assert checkpoints[0].state == {"offset": "1793369802.481090", "recent": RECENT}


@respx.mock
async def test_resume_skips_versions_already_fetched_and_refetches_changed_ones():
    feed = respx.get(API).mock(side_effect=[
        httpx.Response(200, json=load_json("prozorro", "feed_page1.json")),
        httpx.Response(200, json=load_json("prozorro", "feed_page2.json")),
    ])
    details = _mock_details()
    stale = {**RECENT, "a679f8889d0d4a18ad2a38f822875be0": "2026-10-30T16:00:00+02:00"}  # changed since last run
    notices, checkpoints = await run_sync(Prozorro, SINCE, state={"offset": "1793369802.481090", "recent": stale})
    assert feed.call_count == 2
    assert [n.source_id for n in notices] == ["UA-2026-10-30-009334-a"]
    assert details["a679f8889d0d4a18ad2a38f822875be0"].call_count == 1
    assert details["3108fae8140a489c9d67473d301a8ec5"].call_count == 0
    assert details["99dbb29ef4cb490ea2a0a5160b07acfa"].call_count == 0
    # skipped ids stay in the window so a run after this one skips them too
    assert checkpoints[-1].state == {"offset": "1793369802.481090", "recent": RECENT}


@respx.mock
async def test_details_are_fetched_concurrently_and_feed_is_prefetched():
    in_flight = peak = 0

    async def slow_detail(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        tid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=load_json("prozorro", f"tender_{tid}.json"))

    respx.get(API).mock(side_effect=[
        httpx.Response(200, json=load_json("prozorro", "feed_page1.json")),
        httpx.Response(200, json=load_json("prozorro", "feed_page2.json")),
    ])
    for i in OPEN_IDS:
        respx.get(f"{API}/{i}").mock(side_effect=slow_detail)
    notices, _ = await run_sync(Prozorro, SINCE)
    assert len(notices) == 3 and peak == 3  # all open tenders of the page in flight at once


@respx.mock
async def test_detail_error_propagates_without_checkpointing_past_it():
    respx.get(API).mock(side_effect=[
        httpx.Response(200, json=load_json("prozorro", "feed_page1.json")),
        httpx.Response(200, json=load_json("prozorro", "feed_page2.json")),
    ])
    _mock_details()
    respx.get(f"{API}/{OPEN_IDS[1]}").mock(return_value=httpx.Response(403, text="forbidden"))
    conn, client = make_connector(Prozorro)
    got = []
    try:
        with pytest.raises(HttpError):
            async for item in conn.sync(SINCE, {}):
                got.append(item)
    finally:
        await client.aclose()
    assert not any(isinstance(i, Checkpoint) for i in got)


@respx.mock
async def test_refresh_refetches_by_internal_id_and_handles_removed_tender():
    _mock_details()
    respx.get(f"{API}/deadbeef").mock(return_value=httpx.Response(404, json={"status": "error"}))
    conn, client = make_connector(Prozorro)
    try:
        ref = StoredRef("UA-2026-10-30-009409-a", "https://prozorro.gov.ua/tender/UA-2026-10-30-009409-a",
                        {"prozorro_id": "99dbb29ef4cb490ea2a0a5160b07acfa"}, {})
        n = await conn.refresh(ref)
        assert n is not None and n.source_id == "UA-2026-10-30-009409-a"
        assert n.deadline_at == datetime(2026, 11, 4, 12, 0, tzinfo=KYIV)
        gone = await conn.refresh(StoredRef("UA-x", "https://prozorro.gov.ua/tender/UA-x", {"prozorro_id": "deadbeef"}, {}))
        assert gone is None
    finally:
        await client.aclose()


def test_status_mapping_for_later_lifecycle_stages():
    conn, _ = make_connector(Prozorro)
    t = load_json("prozorro", "tender_99dbb29ef4cb490ea2a0a5160b07acfa.json")["data"]
    assert conn.to_notice({**t, "status": "cancelled"}).status is Status.CANCELLED
    assert conn.to_notice({**t, "status": "complete"}).status is Status.AWARDED
    assert conn.to_notice({**t, "status": "active.auction"}).status is Status.CLOSED
    assert conn.to_notice({**t, "procurementMethodType": "reporting"}).notice_type is NoticeType.AWARD
