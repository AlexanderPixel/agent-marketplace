"""Doffin connector against recorded search pages and notice details (api.doffin.no webclient API)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from tenderhub.connector import Context, StoredRef
from tenderhub.connectors import doffin
from tenderhub.connectors.doffin import NOTICE_API, SEARCH_API, Doffin
from tenderhub.http import HttpError
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_json, make_connector, run_sync

SINCE = datetime(2026, 10, 28, 23, 0, tzinfo=UTC)  # = 2026-10-29 00:00 Europe/Oslo
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)    # "today" = 2026-10-30 in Oslo
EMPTY = {"numHitsTotal": 0, "numHitsAccessible": 0, "hits": [], "facets": {}}
SEARCH_PAGES = {
    ("2026-10-29", 1): "search_2026-10-29_p1.json",
    ("2026-10-29", 2): "search_2026-10-29_p2.json",
    ("2026-10-30", 1): "search_2026-10-30_p1.json",
}
DETAILS = ["2026-116778", "2026-116824", "2026-116856"]


def _search(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    key = (body["facets"]["publicationDate"]["from"], body["page"])
    name = SEARCH_PAGES.get(key)
    return httpx.Response(200, json=load_json("doffin", name) if name else EMPTY)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(Context, "now", lambda self: NOW)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(doffin, "PAGE_SIZE", 2)  # fixtures were recorded with small pages
    with respx.mock(assert_all_called=False) as mock:
        search = mock.post(SEARCH_API).mock(side_effect=_search)
        for nid in DETAILS:
            mock.get(NOTICE_API.format(id=nid)).mock(
                return_value=httpx.Response(200, json=load_json("doffin", f"notice_{nid}.json")))
        # one notice whose detail endpoint fails: mapped from the search hit alone
        mock.get(NOTICE_API.format(id="2026-116830")).mock(return_value=httpx.Response(404, json={}))
        yield search


async def test_sync_pages_per_day_and_maps_fields(api):
    notices, checkpoints = await run_sync(Doffin, SINCE)

    bodies = [json.loads(c.request.content) for c in api.calls]
    # days are fetched concurrently, so request order is not fixed; each (day, page) is asked once
    by_key = {(b["facets"]["publicationDate"]["from"], b["page"]): b for b in bodies}
    assert len(by_key) == len(bodies)
    # 10-29 has 3 hits -> 2 pages, stop after the short page; 10-30 (today) has 1 hit -> 1 page
    assert sorted(by_key) == [("2026-10-29", 1), ("2026-10-29", 2), ("2026-10-30", 1)]
    first = by_key[("2026-10-29", 1)]
    assert first["facets"]["publicationDate"] == {"from": "2026-10-29", "to": "2026-10-29"}
    assert first["numHitsPerPage"] == 2 and first["sortBy"] == "PUBLICATION_DATE_DESC"
    assert by_key[("2026-10-30", 1)]["facets"]["publicationDate"]["to"] is None  # today is open-ended
    # one checkpoint per day, in day order, each after that day's notices
    assert [c.state for c in checkpoints] == [{"last_day": "2026-10-29"}, {"last_day": "2026-10-30"}]
    assert [n.source_id for n in notices][-1] == "2026-116856"  # the only 10-30 notice comes last

    assert [p for n in notices for p in validate_notice(n)] == []
    by_id = {n.source_id: n for n in notices}
    assert set(by_id) == {"2026-116778", "2026-116824", "2026-116830", "2026-116856"}

    fruit = by_id["2026-116778"]  # Forsvarsmateriell framework tender
    assert fruit.source == "doffin"
    assert fruit.url == "https://doffin.no/notices/2026-116778"
    assert fruit.title == "Frukt og grønt"
    assert fruit.notice_type is NoticeType.TENDER and fruit.status is Status.OPEN
    assert fruit.buyer_name == "FORSVARSMATERIELL"
    assert fruit.buyer_id == "916075855"
    assert fruit.buyer_country == "NO" and fruit.buyer_city == "Oslo"
    assert fruit.cpv_codes == ["15300000", "15000000", "15310000", "15330000"]
    assert fruit.deadline_at == datetime(2026, 12, 2, 11, 0, tzinfo=UTC)
    assert fruit.published_at.date().isoformat() == "2026-10-29"
    assert fruit.published_at.utcoffset() is not None
    assert fruit.value_amount == 740_000_000.0 and fruit.value_currency == "NOK"
    assert fruit.legal_basis == "32014L0024"
    assert fruit.platform_url == "https://permalink.mercell.com/293304427.aspx"
    assert fruit.documents[0].url == fruit.platform_url
    assert fruit.refs["ted"] == "750197-2026"
    assert fruit.refs["procedure"] == "c97b54df-ac3f-4a5c-8bca-7d0c7a5418c9"
    assert fruit.language == "no"
    assert "eform" not in fruit.raw["detail"]

    seahawk = by_id["2026-116856"]  # defence award
    assert seahawk.notice_type is NoticeType.AWARD and seahawk.status is Status.AWARDED
    assert seahawk.title == "Arbeidsplattformer for MH-60R Seahawk"
    assert seahawk.cpv_codes == ["34951000", "44212317"]
    assert seahawk.procedure_type.startswith("Konkurranse med forhandling")
    assert seahawk.deadline_at is None
    assert seahawk.refs["ted"] == "754968-2026"
    assert seahawk.legal_basis == "32014L0024"

    cancelled = by_id["2026-116824"]
    assert cancelled.notice_type is NoticeType.CANCELLATION and cancelled.status is Status.CANCELLED
    assert cancelled.buyer_name == "Løten Kommune" and cancelled.buyer_id == "964950679"
    assert cancelled.procedure_type == "Åpen"
    assert cancelled.value_amount is None  # Doffin reports 0.0 NOK for "no value"
    assert "ted" not in cancelled.refs
    assert cancelled.legal_basis is None  # no directive in the eForm; regulationUrl is not trusted

    award = by_id["2026-116830"]  # detail 404 -> search hit only
    assert award.notice_type is NoticeType.AWARD
    assert award.title == "Drift av Sørtun bofellesskap"
    assert award.buyer_name == "Melhus Kommune" and award.buyer_id == "938726027"
    assert award.buyer_country == "NO"
    assert award.cpv_codes == []


async def test_refresh_refetches_detail(api):
    conn, client = make_connector(Doffin)
    try:
        n = await conn.refresh(StoredRef("2026-116778", "https://doffin.no/notices/2026-116778", {}, {}))
        gone = await conn.refresh(StoredRef("2026-116830", "https://doffin.no/notices/2026-116830", {}, {}))
    finally:
        await client.aclose()
    assert n is not None and n.title == "Frukt og grønt" and validate_notice(n) == []
    assert gone is None


@respx.mock
async def test_empty_days_still_checkpoint():
    respx.post(SEARCH_API).mock(return_value=httpx.Response(200, json=EMPTY))
    notices, checkpoints = await run_sync(Doffin, datetime.now(UTC))
    assert notices == [] and len(checkpoints) >= 1


async def test_details_are_fetched_concurrently(monkeypatch):
    monkeypatch.setattr(doffin, "PAGE_SIZE", 100)
    ids = [f"2026-9{i:05d}" for i in range(20)]
    page = {"numHitsTotal": len(ids), "numHitsAccessible": len(ids),
            "hits": [{"id": i, "heading": f"T {i}", "type": "COMPETITION", "buyer": [{"name": "B"}]} for i in ids]}
    in_flight = peak = 0

    async def detail(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return httpx.Response(404, json={})

    def search(request: httpx.Request) -> httpx.Response:
        day = json.loads(request.content)["facets"]["publicationDate"]["from"]
        return httpx.Response(200, json=page if day == "2026-10-30" else EMPTY)

    with respx.mock:
        respx.post(SEARCH_API).mock(side_effect=search)
        respx.get(url__startswith=NOTICE_API.format(id="")).mock(side_effect=detail)
        notices, checkpoints = await run_sync(Doffin, SINCE)

    assert [n.source_id for n in notices] == ids  # order preserved despite concurrency
    assert 1 < peak <= doffin.DETAIL_CONCURRENCY
    assert len(checkpoints) == 2


async def test_detail_error_propagates_unwrapped(monkeypatch):
    with respx.mock:
        respx.post(SEARCH_API).mock(side_effect=_search)
        respx.get(url__startswith=NOTICE_API.format(id="")).mock(return_value=httpx.Response(400, text="bad"))
        with pytest.raises(HttpError):
            await run_sync(Doffin, SINCE)
