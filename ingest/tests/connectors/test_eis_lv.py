"""EIS (Latvia) connector against recorded search-API pages and procurement detail pages."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import respx

from tenderhub.connector import StoredRef
from tenderhub.connectors.eis_lv import DETAIL, SEARCH_API, SEARCH_PAGE, EisLv
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_json, load_text, make_connector, run_sync

RIGA = ZoneInfo("Europe/Riga")
SINCE = datetime(2026, 9, 20, tzinfo=UTC)


def _mock_details(*ids: int) -> dict[int, respx.Route]:
    return {i: respx.get(DETAIL.format(id=i)).mock(
        return_value=httpx.Response(200, text=load_text("eis_lv", f"procurement_{i}.html"))) for i in ids}


@respx.mock
async def test_sync_pages_newest_first_and_maps_list_plus_detail():
    page = respx.get(SEARCH_PAGE).mock(return_value=httpx.Response(200, text=load_text("eis_lv", "supplier_index.html")))
    search = respx.post(SEARCH_API).mock(side_effect=[
        httpx.Response(200, json=load_json("eis_lv", "search_page1.json")),
        httpx.Response(200, json=load_json("eis_lv", "search_page2.json")),
    ])
    details = _mock_details(184959, 185159, 185200, 181401)
    older = respx.get(DETAIL.format(id=180176)).mock(return_value=httpx.Response(500))

    notices, checkpoints = await run_sync(EisLv, SINCE)

    assert page.call_count == 1  # one anti-forgery token serves the whole sync
    assert search.call_count == 2  # page 2 reaches a row older than `since` -> stop although Hits=6
    first, second = (json.loads(c.request.content) for c in search.calls)
    assert first["FormCode"] == "SUP.S01" and first["From"] == 0
    assert first["SearchParameters"]["PublicationDateFrom"] == "2026-09-20T00:00:00"
    assert second["From"] == 3 and second["SearchParameters"]["From"] == 3
    req = search.calls[0].request
    assert req.headers["__RequestVerificationToken"].startswith("pe7rD5Ser")
    assert req.headers["Origin"] == "https://www.eis.gov.lv"
    assert all(r.call_count == 1 for r in details.values()) and older.call_count == 0
    # detail GETs reuse the jar session (no explicit Cookie header -> the server does not rotate it)
    assert "Eis.Language=LAT" in details[185159].calls[0].request.headers["Cookie"]

    assert [n.source_id for n in notices] == ["184959", "185159", "185200", "181401"]  # 180176 is older than since
    assert len(checkpoints) == 2
    assert [p for n in notices for p in validate_notice(n)] == []
    by_id = {n.source_id: n for n in notices}

    valic = by_id["181401"]  # defence: VALIC shotgun purchase under the Defence & Security Procurement Law
    assert valic.source == "eis_lv"
    assert valic.url == "https://www.eis.gov.lv/EKEIS/Supplier/Procurement/181401"
    assert valic.title == "Bises, tās rezerves daļu un personāla apmācības iegāde"
    assert valic.buyer_name == "Valsts aizsardzības loģistikas un iepirkumu centrs"
    assert valic.buyer_id == "40900035122" and valic.buyer_country == "LV"
    assert valic.cpv_codes == ["35300000"]
    assert valic.legal_basis == "32009L0081"
    assert valic.procedure_type == "Sarunu procedūra, publicējot dalības uzaicinājumu"
    assert valic.notice_type is NoticeType.TENDER
    assert valic.status is Status.CLOSED  # PAT: bids opened
    assert valic.published_at == datetime(2026, 9, 24, 15, 0, 23, tzinfo=RIGA)
    assert valic.deadline_at == datetime(2026, 10, 26, 11, 0, tzinfo=RIGA)
    assert valic.refs == {"eis": "181401", "procedure": "VALIC 2026/090/2N-A-SPP"}
    assert valic.language == "lv"
    assert "ContactList" not in valic.raw["detail"]["fields"]

    lots = by_id["185159"]  # municipal service procurement in 3 lots, discontinued
    assert lots.status is Status.CANCELLED
    assert lots.cpv_codes == ["85100000", "85121270", "92620000"]
    assert lots.legal_basis == "PIL"
    assert lots.buyer_id == "90000035590"
    assert lots.deadline_at == datetime(2026, 11, 16, 9, 0, tzinfo=RIGA)
    assert lots.description.startswith("Daļas: 1. Ūdens aerobikas")
    assert lots.refs["iub"] == "08b6b230-6821-4930-90e7-0d0549ec567d"
    assert lots.documents[0].url == "https://eformsb.pvs.iub.gov.lv/show/08b6b230-6821-4930-90e7-0d0549ec567d"

    amended = by_id["184959"]  # amended after publication: list ProcurementId is a new version id
    assert amended.url == "https://www.eis.gov.lv/EKEIS/Supplier/Procurement/184959"
    assert amended.refs == {"eis": "184959", "eis_version": "185210", "procedure": "TNP 2026/107/ESF",
                            "iub": "a58f53b7-2d9f-4306-b41e-575838730502"}
    assert amended.title == "Peldēšanas nodarbības Tukuma novada iedzīvotājiem un skolēniem"
    assert amended.buyer_name == "Tukuma novada pašvaldība" and amended.cpv_codes == ["92622000", "92610000"]
    assert amended.published_at == datetime(2026, 10, 30, 15, 35, 56, tzinfo=RIGA)

    consult = by_id["185200"]  # market consultation ("Apspriede ar piegādātājiem") with estimated value
    assert consult.notice_type is NoticeType.PLAN
    assert consult.value_amount == 490000.0 and consult.value_currency == "EUR"
    assert consult.cpv_codes == ["38434000"]
    assert consult.deadline_at == datetime(2026, 11, 13, 17, 0, tzinfo=RIGA)  # from the detail page stage
    assert consult.buyer_name == '"Bērnu klīniskā universitātes slimnīca" VSIA'


@respx.mock
async def test_empty_result_still_checkpoints():
    respx.get(SEARCH_PAGE).mock(return_value=httpx.Response(200, text=load_text("eis_lv", "supplier_index.html")))
    respx.post(SEARCH_API).mock(return_value=httpx.Response(200, json={"Success": True, "EventData": {"Items": [], "Hits": 0}}))
    notices, checkpoints = await run_sync(EisLv, SINCE)
    assert notices == [] and len(checkpoints) == 1


@respx.mock
async def test_refresh_rebuilds_from_detail_page_and_handles_removed():
    _mock_details(181401)
    respx.get(DETAIL.format(id=1)).mock(return_value=httpx.Response(
        302, headers={"Location": "https://www.eis.gov.lv/EKEIS/Error.html?message=x"}))
    respx.get("https://www.eis.gov.lv/EKEIS/Error.html").mock(return_value=httpx.Response(200, text="<title>Kļūda</title>"))
    conn, client = make_connector(EisLv)
    try:
        n = await conn.refresh(StoredRef("181401", DETAIL.format(id=181401), {"eis": "181401"}, raw={}))
        gone = await conn.refresh(StoredRef("1", DETAIL.format(id=1), {}, raw={}))
    finally:
        await client.aclose()
    assert validate_notice(n) == []
    assert n.title == "Bises, tās rezerves daļu un personāla apmācības iegāde"
    assert n.buyer_name == "Valsts aizsardzības loģistikas un iepirkumu centrs" and n.buyer_id == "40900035122"
    assert n.published_at == datetime(2026, 9, 24, tzinfo=RIGA)
    assert n.deadline_at == datetime(2026, 10, 26, 11, 0, tzinfo=RIGA)
    assert n.status is Status.CLOSED and n.cpv_codes == ["35300000"]
    assert gone is None


@respx.mock
async def test_expired_token_is_refetched_once():
    page = respx.get(SEARCH_PAGE).mock(return_value=httpx.Response(200, text=load_text("eis_lv", "supplier_index.html")))
    search = respx.post(SEARCH_API).mock(side_effect=[
        httpx.Response(200, json={"Success": False, "RedirectUrl": "/EKEIS/Error.html?x"}),
        httpx.Response(200, json={"Success": True, "EventData": {"Items": [], "Hits": 0}}),
    ])
    notices, checkpoints = await run_sync(EisLv, SINCE)
    assert page.call_count == 2 and search.call_count == 2
    assert notices == [] and len(checkpoints) == 1
