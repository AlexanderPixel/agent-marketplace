"""udbud.dk connector against recorded search API pages (POST /soegning/public/soegeresultat)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import respx

from tenderhub.connectors.udbud_dk import API, UdbudDk
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_json, run_sync

SINCE = datetime(2026, 10, 26, 23, 30, tzinfo=UTC)   # = 2026-10-27 00:30 in Copenhagen
CPH = ZoneInfo("Europe/Copenhagen")


def _page_of(request: httpx.Request) -> int:
    return json.loads(request.content)["pagineringDto"]["aktuelSide"]


@respx.mock
async def test_sync_pages_by_total_and_maps_fields():
    pages = {1: load_json("udbud_dk", "search_page1.json"), 2: load_json("udbud_dk", "search_page2.json")}
    route = respx.post(API).mock(side_effect=lambda req: httpx.Response(200, json=pages[_page_of(req)]))

    notices, checkpoints = await run_sync(UdbudDk, SINCE)

    # totaltAntalResultater=136 at 100/page -> exactly two requests, then stop
    assert route.call_count == 2
    bodies = sorted((json.loads(c.request.content) for c in route.calls), key=lambda b: b["pagineringDto"]["aktuelSide"])
    assert [b["pagineringDto"]["aktuelSide"] for b in bodies] == [1, 2]
    assert bodies[0]["filterDto"] == {"publikationDatoFra": "2026-10-27"}   # since converted to Danish date
    assert bodies[0]["udbudStatusFilter"] == "ALLE"
    assert bodies[0]["pagineringDto"]["maksElementer"] == 100
    assert bodies[0]["pagineringDto"]["sorteringFelt"] == "PUBLIKATION_DATO"
    assert len(notices) == 8 and len(checkpoints) == 1
    assert checkpoints[0].state["published_from"] == "2026-10-27"
    assert [p for n in notices for p in validate_notice(n)] == []
    # page order preserved: page-1 rows first
    assert notices[0].source_id == "c2112902-e292-47b2-b0f2-faeea1a2ddce"
    assert notices[-1].source_id == "99de2aed-1cf3-4e6d-ba93-4a61a3d7be47"

    by_id = {n.source_id: n for n in notices}

    coast = by_id["c2112902-e292-47b2-b0f2-faeea1a2ddce"]  # TED-published utilities contract notice
    assert coast.source == "udbud_dk"
    assert coast.url == ("https://udbud.dk/detaljevisning?noticeId=c2112902-e292-47b2-b0f2-faeea1a2ddce"
                         "&noticeVersion=01&noticePublicationNumber=00743286-2026")
    assert coast.title == "Kystbeskyttelse af Strandparken i Køge Bugt"
    assert coast.notice_type is NoticeType.TENDER
    assert coast.buyer_name == "Strandparken I/S"
    assert coast.buyer_id == "26352843"
    assert coast.buyer_country == "DK"
    assert coast.cpv_codes == ["71311000"]
    assert coast.legal_basis == "32014L0025"
    assert coast.published_at == datetime(2026, 10, 27, tzinfo=CPH)
    assert coast.deadline_at == datetime(2026, 11, 27, 11, 0, tzinfo=UTC)
    assert coast.value_amount == 14_000_000 and coast.value_currency == "DKK"
    assert coast.refs == {"notice": "c2112902-e292-47b2-b0f2-faeea1a2ddce", "ted": "743286-2026"}

    change = by_id["7af1e32d-088c-40a6-9889-fb706607adfd"]  # erAendring=true -> change notice
    assert change.notice_type is NoticeType.MODIFICATION
    assert change.legal_basis == "32014L0024"

    fmi = by_id["fccc3285-6c80-4a54-a9f3-e8ab5933c910"]  # national below-threshold notice, defence agency
    assert fmi.title.startswith("4 EA Magnetic Measurement Table")
    assert fmi.buyer_name == "Forsvarsministeriets Materiel og Indkøbsstyrelse"
    assert fmi.buyer_id == "16287180"               # 'ORG-16-28-71-80' cleaned to the CVR
    assert fmi.legal_basis is None
    assert "ted" not in fmi.refs
    assert fmi.url.endswith("noticeVersion=01&noticePublicationNumber=")
    assert fmi.deadline_at == datetime(2026, 12, 9, 10, 0, tzinfo=CPH)
    assert fmi.value_amount is None and fmi.value_currency is None
    assert fmi.status is (Status.OPEN if fmi.deadline_at > datetime.now(UTC) else Status.CLOSED)

    pin = by_id["c4bc2fc6-b799-4879-9aa2-fac9aaf8b2f9"]
    assert pin.notice_type is NoticeType.PLAN and pin.deadline_at is None
    assert pin.published_at == datetime(2026, 10, 28, tzinfo=CPH)

    missiles = by_id["b9639df4-9510-4cbe-b90a-3aae54b28d7c"]  # defence-directive award
    assert missiles.notice_type is NoticeType.AWARD and missiles.status is Status.AWARDED
    assert missiles.legal_basis == "32009L0081"
    assert missiles.buyer_name == "Danish Ministry of Defence Acquisition and Logistics Organisation"
    assert missiles.cpv_codes == ["35623100"]

    eu = by_id["66d453be-7ec1-4766-bed3-2cd033662285"]  # non-Danish buyer id 'COM' -> no CVR, no country
    assert eu.buyer_id is None and eu.buyer_country is None
    assert eu.value_amount == 700_000_000 and eu.value_currency == "EUR"
    assert eu.deadline_at == datetime(2026, 11, 30, 13, 0, 59, tzinfo=UTC)


@respx.mock
async def test_single_page_stops_after_first_request():
    page = load_json("udbud_dk", "search_page2.json")
    page["totaltAntalResultater"] = 3
    route = respx.post(API).mock(return_value=httpx.Response(200, json=page))
    notices, checkpoints = await run_sync(UdbudDk, SINCE)
    assert route.call_count == 1
    assert len(notices) == 3 and len(checkpoints) == 1


@respx.mock
async def test_empty_result_still_checkpoints():
    route = respx.post(API).mock(return_value=httpx.Response(200, json={"resultatElementDtoList": [],
                                                                        "totaltAntalResultater": 0}))
    notices, checkpoints = await run_sync(UdbudDk, SINCE)
    assert route.call_count == 1
    assert notices == [] and len(checkpoints) == 1
