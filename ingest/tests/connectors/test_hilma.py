"""Hilma connector against recorded responses of the site's anonymous search proxy (Azure Cognitive Search)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from tenderhub.connector import StoredRef
from tenderhub.connectors import hilma
from tenderhub.connectors.hilma import SEARCH, Hilma
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_json, make_connector, run_sync

SINCE = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def small_pages(monkeypatch):
    """The recorded pages hold 4 + 3 rows; shrink the page size so they exercise paging."""
    monkeypatch.setattr(hilma, "PAGE_SIZE", 4)


def _rows(n: int, start: int = 0) -> list[dict]:
    base = load_json("hilma", "page1.json")["value"][1]  # EF-60253
    t0 = datetime(2026, 10, 2, tzinfo=UTC)
    out = []
    for i in range(start, start + n):
        r = copy.deepcopy(base)
        r["id"], r["noticeId"] = f"EF-{i}", i
        r["dateModified"] = (t0 + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        out.append(r)
    return out


@respx.mock
async def test_sync_pages_with_skip_and_maps_fields(small_pages):
    route = respx.get(SEARCH).mock(side_effect=[
        httpx.Response(200, json=load_json("hilma", "page1.json")),
        httpx.Response(200, json=load_json("hilma", "page2.json")),
    ])

    notices, checkpoints = await run_sync(Hilma, SINCE)

    # @odata.count is 7: page 1 has 4 rows, page 2 the remaining 3, then stop without a third request
    assert route.call_count == 2
    p1, p2 = (c.request.url.params for c in route.calls)
    assert p1["$filter"] == "dateModified ge 2026-10-01T00:00:00.000Z"
    assert p1["$orderby"] == "dateModified asc, id asc"
    assert p1["$top"] == "4" and p1["$skip"] == "0" and p1["$count"] == "true"
    assert p2["$skip"] == "4" and p2["$filter"] == p1["$filter"]
    assert len(notices) == 7 and len(checkpoints) == 2
    assert checkpoints[-1].state == {"modified_through": "2026-10-30T13:36:42.437Z"}
    assert [p for n in notices for p in validate_notice(n)] == []

    by_id = {n.source_id: n for n in notices}

    cn = by_id["EF-60253"]  # Kela open tender, EU procedure also published on TED
    assert cn.source == "hilma"
    assert cn.url == "https://www.hankintailmoitukset.fi/fi/public/procedure/39145/enotice/60253"
    assert cn.title == "KUNTOUTUSTUKEA SAAVAN NUOREN AMMATILLINEN VALMENNUS, Etelä-Suomi"
    assert cn.notice_type is NoticeType.TENDER
    assert cn.buyer_name == "Kansaneläkelaitos"
    assert cn.buyer_id == "0246246-0"
    assert cn.buyer_country == "FI" and cn.buyer_city == "Helsinki"
    assert cn.cpv_codes == ["85000000", "85312500"]
    assert cn.procedure_type == "open"
    assert cn.published_at == datetime(2026, 10, 27, 3, 5, 4, 488000, tzinfo=UTC)
    assert cn.deadline_at == datetime(2026, 11, 30, 11, 0, tzinfo=UTC)
    assert cn.platform_url.startswith("https://tarjouspalvelu.fi/kela?id=616180")
    assert cn.documents[0].url == cn.platform_url
    assert cn.language == "fi"
    assert cn.refs["ted"] == "741923-2026"
    assert cn.refs["hilma"] == "2026-060253"
    assert cn.refs["procedure"] == "f331a776-9e04-498b-b59a-de83e59f62d7"

    corr = by_id["EF-60881"]  # national framework tender, corrigendum of EF-60593
    assert corr.notice_type is NoticeType.MODIFICATION
    assert corr.value_amount == 1840000.0 and corr.value_currency == "EUR"
    assert corr.cpv_codes[:3] == ["45000000", "45300000", "45112000"] and len(corr.cpv_codes) == 8
    assert corr.deadline_at == datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    assert corr.buyer_city == "Nivala"
    assert corr.platform_url is None  # documents URL is the notice page itself

    award = by_id["EF-60855"]
    assert award.notice_type is NoticeType.AWARD and award.status is Status.AWARDED
    assert award.value_amount == 127800.0 and award.deadline_at is None

    old = by_id["OLD-160690"]  # legacy national form, direct award: its "deadline" is not a bid deadline
    assert old.url == "https://www.hankintailmoitukset.fi/fi/public/procurement/111270/notice/160690/overview"
    assert old.notice_type is NoticeType.AWARD and old.deadline_at is None
    assert old.value_amount == 171527.0 and old.cpv_codes == ["48000000", "72000000"]
    assert old.buyer_city == "Jyväskylän yliopisto" and old.buyer_country == "FI"

    cancelled = by_id["OLD-160647"]
    assert cancelled.notice_type is NoticeType.CANCELLATION and cancelled.status is Status.CANCELLED

    plan = by_id["PLAN-1330"]
    assert plan.url == "https://www.hankintailmoitukset.fi/fi/public/plan/1330"
    assert plan.notice_type is NoticeType.PLAN
    assert plan.value_amount is None  # 1.0 EUR placeholder dropped
    assert plan.buyer_country == "FI"

    mod = by_id["EF-60528"]
    assert mod.notice_type is NoticeType.MODIFICATION and mod.refs["ted"] == "750393-2026"


@respx.mock
async def test_remaining_pages_are_fetched_concurrently_and_yielded_in_order(small_pages, monkeypatch):
    monkeypatch.setattr(hilma, "CONCURRENCY", 2)
    rows = _rows(13)

    def handler(request: httpx.Request) -> httpx.Response:
        skip, top = int(request.url.params["$skip"]), int(request.url.params["$top"])
        return httpx.Response(200, json={"@odata.count": 13, "value": rows[skip:skip + top]})

    route = respx.get(SEARCH).mock(side_effect=handler)
    notices, checkpoints = await run_sync(Hilma, SINCE)

    # count 13, page size 4: first page, then skips 4+8 together, then 12; no request past the count
    assert sorted(int(c.request.url.params["$skip"]) for c in route.calls) == [0, 4, 8, 12]
    assert [n.source_id for n in notices] == [r["id"] for r in rows]
    assert len(checkpoints) == 4 and checkpoints[-1].state == {"modified_through": rows[-1]["dateModified"]}


@respx.mock
async def test_window_restarts_from_last_modified_past_skip_limit(monkeypatch):
    monkeypatch.setattr(hilma, "PAGE_SIZE", 2)
    monkeypatch.setattr(hilma, "MAX_SKIP", 2)
    rows = _rows(6)
    filters = []

    def handler(request: httpx.Request) -> httpx.Response:
        flt, skip = request.url.params["$filter"], int(request.url.params["$skip"])
        if flt not in filters:
            filters.append(flt)
        window = rows if len(filters) == 1 else rows[3:]  # second window starts at row 3's dateModified
        return httpx.Response(200, json={"@odata.count": len(window), "value": window[skip:skip + 2]})

    respx.get(SEARCH).mock(side_effect=handler)
    notices, _ = await run_sync(Hilma, SINCE)

    assert filters == ["dateModified ge 2026-10-01T00:00:00.000Z", "dateModified ge 2026-10-02T00:03:00.000Z"]
    assert [n.source_id for n in notices] == ["EF-0", "EF-1", "EF-2", "EF-3", "EF-3", "EF-4", "EF-5"]


@respx.mock
async def test_empty_result_stops_after_one_request():
    route = respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"@odata.count": 0, "value": []}))
    notices, checkpoints = await run_sync(Hilma, SINCE)
    assert route.call_count == 1
    assert notices == [] and checkpoints == []


@respx.mock
async def test_refresh_fetches_one_notice_by_id():
    page = load_json("hilma", "page1.json")
    one = {"@odata.count": 1, "value": [x for x in page["value"] if x["id"] == "EF-60253"]}
    route = respx.get(SEARCH).mock(return_value=httpx.Response(200, json=one))
    conn, client = make_connector(Hilma)
    try:
        n = await conn.refresh(StoredRef("EF-60253", "", {}, {}))
    finally:
        await client.aclose()
    assert route.calls[0].request.url.params["$filter"] == "id eq 'EF-60253'"
    assert n is not None and n.source_id == "EF-60253" and n.deadline_at is not None
