"""platformazakupowa.pl connector against recorded platformaofertowa.pl API responses.

The search fixtures are real responses (provider=pz, sorted by deadline). A small fake server applies the
API's deadline window / offset / limit semantics to those recorded tenders, so the keyset paging, the
same-deadline offset paging and the active/inactive split are exercised with real records.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx
from dateutil.parser import isoparse

from tenderhub.connector import StoredRef
from tenderhub.connectors import platformazakupowa as pz
from tenderhub.connectors.platformazakupowa import ATTACHMENTS, SEARCH, TENDER, Platformazakupowa, find_refs
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import collect, load_json, make_connector, run_sync

SINCE = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
ACTIVE = load_json("platformazakupowa", "search_active.json")["tenders"]
INACTIVE = load_json("platformazakupowa", "search_inactive.json")["tenders"]


def fake_search(bodies: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        f = body["filters"]
        assert f["provider"] == "pz" and body["sort_by"] == "submitting_offers_deadline_asc"
        lo, hi = isoparse(f["submitting_offers_deadline_from"]), isoparse(f["submitting_offers_deadline_to"])
        pool = ACTIVE if body["active_status"] == 1 else INACTIVE
        hits = sorted((t for t in pool if lo <= isoparse(t["submittingOffersDeadline"]) <= hi),
                      key=lambda t: isoparse(t["submittingOffersDeadline"]))
        page = hits[body["offset"]:body["offset"] + body["limit"]]
        return httpx.Response(200, json={
            "results": [{"id": t["id"], "score": None} for t in page],
            "pagination": {"page": body["offset"] // body["limit"] + 1, "page_size": body["limit"],
                           "total_count": len(hits), "has_more": body["offset"] + len(page) < len(hits),
                           "offset": body["offset"]},
            "tenders": page,
        })
    return handler


@respx.mock
async def test_sync_keyset_pages_by_deadline_and_maps_fields(monkeypatch):
    monkeypatch.setattr(pz, "PAGE_SIZE", 2)          # recorded pool is small: force multi-page walks
    monkeypatch.setattr(pz, "SEGMENT_DAYS", ())      # one active segment + the inactive one
    bodies: list[dict] = []
    respx.post(SEARCH).mock(side_effect=fake_search(bodies))

    notices, checkpoints = await run_sync(Platformazakupowa, SINCE)

    assert [p for n in notices for p in validate_notice(n)] == []
    assert len(checkpoints) == 1
    # publication_date is a ">= day" filter, queried for the Warsaw day of `since` minus the ingest lag
    assert {b["filters"]["publication_date"] for b in bodies} == {"2026-10-29"}
    active = [b for b in bodies if b["active_status"] == 1]
    # 1: from 2000 -> two tenders due 12:00 (of 7)   2: from 12:00 -> full page all due 12:00
    # 3-5: offset paging inside the 12:00 instant (6 tenders)   6: from 12:00:01 -> the 12:04 tender, done
    assert [(b["filters"]["submitting_offers_deadline_from"], b["filters"]["submitting_offers_deadline_to"],
             b["offset"]) for b in active] == [
        ("2000-01-01T00:00:00.000Z", "2100-01-01T00:00:00.000Z", 0),
        ("2026-11-13T11:00:00.000Z", "2100-01-01T00:00:00.000Z", 0),
        ("2026-11-13T11:00:00.000Z", "2026-11-13T11:00:00.000Z", 0),
        ("2026-11-13T11:00:00.000Z", "2026-11-13T11:00:00.000Z", 2),
        ("2026-11-13T11:00:00.000Z", "2026-11-13T11:00:00.000Z", 4),
        ("2026-11-13T11:00:01.000Z", "2100-01-01T00:00:00.000Z", 0),
    ]

    # every recorded tender changed after SINCE is yielded once; the one last ingested before SINCE is not
    by_id = {n.source_id: n for n in notices}
    assert len(by_id) == len(notices)
    expected = {str(t["sourceExtraData"]["externalId"]) for t in ACTIVE + INACTIVE
                if max(isoparse(t["createdAt"]), isoparse(t["publicationDate"])) >= SINCE}
    assert set(by_id) == expected
    stale = next(t for t in ACTIVE if t["id"].startswith("6ef4b135"))
    assert str(stale["sourceExtraData"]["externalId"]) not in by_id
    assert len(by_id) == 7  # 6 active (one stale) + 1 inactive (one stale)

    boniewo = next(n for n in notices if n.raw["id"].startswith("5df44036"))
    assert boniewo.source == "platformazakupowa"
    assert boniewo.url == f"https://platformazakupowa.pl/transakcja/{boniewo.source_id}"
    assert boniewo.title.startswith("Rozbudowa i modernizacja magazynu obrony cywilnej w Boniewie")
    assert boniewo.buyer_name == "Gmina Boniewo"
    assert boniewo.buyer_country == "PL"
    assert boniewo.buyer_id and boniewo.buyer_id.isdigit()
    assert boniewo.notice_type is NoticeType.TENDER and boniewo.status is Status.OPEN
    assert boniewo.deadline_at == datetime(2026, 11, 13, 11, 0, tzinfo=UTC)
    assert boniewo.published_at == datetime(2026, 10, 29, 18, 19, tzinfo=UTC)
    assert boniewo.refs["bzp"] == "2026/BZP 00510384"
    assert boniewo.language == "pl"
    assert all(len(c) == 8 for c in boniewo.cpv_codes)
    assert "<" not in (boniewo.description or "")
    assert "description" not in boniewo.raw

    closed = next(n for n in notices if n.raw["id"].startswith("6f559f47"))
    assert closed.status is Status.CLOSED
    assert closed.deadline_at == datetime(2026, 10, 29, 13, 0, tzinfo=UTC)


@respx.mock
async def test_sync_segments_cover_deadline_axis_without_gaps(monkeypatch):
    bodies: list[dict] = []
    respx.post(SEARCH).mock(side_effect=fake_search(bodies))
    notices, checkpoints = await run_sync(Platformazakupowa, SINCE)
    assert len(checkpoints) == 1 and len(notices) == 7
    windows = sorted((isoparse(b["filters"]["submitting_offers_deadline_from"]),
                      isoparse(b["filters"]["submitting_offers_deadline_to"]))
                     for b in bodies if b["active_status"] == 1 and b["offset"] == 0)
    assert len(windows) == len(pz.SEGMENT_DAYS) + 1
    assert windows[0][0] == pz.MIN_DEADLINE and windows[-1][1] == pz.MAX_DEADLINE
    for (_, hi), (lo, _) in zip(windows, windows[1:]):
        assert (lo - hi).total_seconds() == 1


@respx.mock
async def test_sync_raises_on_api_error():
    respx.post(SEARCH).mock(return_value=httpx.Response(403, text="forbidden"))
    conn, client = make_connector(Platformazakupowa)
    try:
        with pytest.raises(Exception, match="403"):
            await collect(conn.sync(SINCE, {}))
    finally:
        await client.aclose()


@respx.mock
async def test_refresh_reads_tender_and_attachments():
    uuid = "d42e587f-69ec-5d5d-a87a-9711937793f9"
    respx.get(TENDER.format(id=uuid)).mock(
        return_value=httpx.Response(200, json=load_json("platformazakupowa", "tender_d42e587f.json")))
    respx.get(ATTACHMENTS.format(id=uuid)).mock(
        return_value=httpx.Response(200, json=load_json("platformazakupowa", "attachments_d42e587f.json")))
    conn, client = make_connector(Platformazakupowa)
    try:
        n = await conn.refresh(StoredRef("1398791", "https://platformazakupowa.pl/transakcja/1398791", {},
                                         {"id": uuid}))
    finally:
        await client.aclose()
    assert n is not None and validate_notice(n) == []
    assert n.source_id == "1398791"
    assert n.title.startswith("TPm-79/26 Adaptacja pomieszczeń parteru")
    assert n.buyer_name == "Uniwersytet Medyczny im. Karola Marcinkowskiego w Poznaniu"
    assert n.buyer_id == "7770003104" and n.buyer_city == "Poznań"
    assert n.cpv_codes == ["45300000", "45400000"]
    assert n.deadline_at == datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
    assert n.procedure_type == "above EU thresholds"
    assert len(n.documents) == 5
    assert n.documents[0].url == ("https://api.platformaofertowa.pl/tenders/attachment/download/"
                                  "4d115f73-2b28-494d-af26-098d21e1715b")
    assert n.documents[0].title.endswith("projekt umowy+załączniki.zip")
    # BZP number recovered from the attachment name "2026BZP 0051335001.pdf"
    assert n.refs == {"bzp": "2026/BZP 00513350", "procedure": "TPm-79/26"}


@respx.mock
async def test_refresh_returns_none_when_gone():
    respx.get(TENDER.format(id="gone")).mock(return_value=httpx.Response(404, text="Not Found"))
    conn, client = make_connector(Platformazakupowa)
    try:
        assert await conn.refresh(StoredRef("1", "https://platformazakupowa.pl/transakcja/1", {}, {"id": "gone"})) is None
    finally:
        await client.aclose()


def test_find_refs():
    assert find_refs("Ogłoszenie nr 2026/BZP 00510384/01 z dnia") == {"bzp": "2026/BZP 00510384"}
    assert find_refs("BZP.271.72.2026.MK") == {}
    assert find_refs("Dz.U. UE 2026/S 210-712345") == {"ted": "712345-2026"}
    assert find_refs("ogłoszenie TED nr 674227-2026") == {"ted": "674227-2026"}


def test_price_estimation_is_a_plan_but_price_rfq_is_a_tender():
    conn, _ = make_connector(Platformazakupowa)
    base = dict(ACTIVE[0])
    estimate = conn.to_notice(base | {"description": "<p>Szanowni Państwo, informujemy, że poniższe postępowanie "
                                                     "ma charakter szacowania wartości zamówienia.</p>"})
    rfq = conn.to_notice(base | {"description": "<p>3. Tryb postępowania : rozeznanie cenowe</p>"})
    assert estimate.notice_type is NoticeType.PLAN
    assert rfq.notice_type is NoticeType.TENDER
