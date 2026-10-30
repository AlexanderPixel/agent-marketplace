"""TenderNed connector against recorded API responses (list pages, detail records, document lists)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
import respx

from tenderhub.connector import StoredRef
from tenderhub.connectors.tenderned import API, TZ, TenderNed
from tenderhub.http import HttpError
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_json, make_connector, run_sync

SINCE = datetime(2026, 10, 27, 23, 0, tzinfo=UTC)  # 2026-10-28 00:00 in Amsterdam


def _mock_list() -> dict[str, respx.Route]:
    # routed by page number: pages are prefetched concurrently, so call order is not guaranteed
    return {p: respx.get(API, params={"page": p}).mock(
        return_value=httpx.Response(200, json=load_json("tenderned", f"list_page{p}.json"))) for p in ("0", "1")}


def _mock_details() -> dict[str, respx.Route]:
    routes = {}
    for pid in ("447195", "447102", "447088", "446894", "440202"):
        routes[pid] = respx.get(f"{API}/{pid}").mock(
            return_value=httpx.Response(200, json=load_json("tenderned", f"detail_{pid}.json")))
    for pid in ("447195", "447088"):
        routes[f"docs{pid}"] = respx.get(f"{API}/{pid}/documenten").mock(
            return_value=httpx.Response(200, json=load_json("tenderned", f"documents_{pid}.json")))
    return routes


@respx.mock
async def test_sync_pages_list_fetches_details_and_maps_fields():
    lst = _mock_list()
    routes = _mock_details()

    notices, checkpoints = await run_sync(TenderNed, SINCE)

    assert lst["0"].call_count == 1 and lst["1"].call_count == 1
    assert dict(lst["0"].calls[0].request.url.params) == {"page": "0", "size": "100",
                                                          "publicatieDatumVanaf": "2026-10-28"}
    assert dict(lst["1"].calls[0].request.url.params)["publicatieDatumVanaf"] == "2026-10-28"
    # 440202 (2026-09-17) is older than `since`: skipped without a detail call, and paging stops
    assert [n.source_id for n in notices] == ["447195", "447102", "447088", "446894"]
    assert routes["440202"].call_count == 0
    # documents are only fetched for TenderNed-hosted tenders/consultations, not for imported or award notices
    assert routes["docs447195"].call_count == 1 and routes["docs447088"].call_count == 1
    assert len(checkpoints) == 2
    assert set(checkpoints[-1].state["seen"]) == {"447195", "447102", "447088", "446894"}
    assert [p for n in notices for p in validate_notice(n)] == []

    by_id = {n.source_id: n for n in notices}
    works = by_id["447088"]  # national open tender, documents on TenderNed
    assert works.source == "tenderned"
    assert works.url == "https://www.tenderned.nl/aankondigingen/overzicht/447088"
    assert works.title == "Bouw- en woonrijp maken Zeeheldenbuurt Hoogeveen"
    assert works.buyer_name == "Gemeente Hoogeveen"
    assert works.buyer_country == "NL"
    assert works.notice_type is NoticeType.TENDER
    assert works.cpv_codes == ["45200000"]
    assert works.procedure_type == "Openbaar"
    assert works.legal_basis == "ARW 2016 - Aanbestedingsreglement Werken 2016"
    assert works.published_at == datetime(2026, 10, 30, 10, 52, 12, 921173, tzinfo=TZ)
    assert works.deadline_at == datetime(2026, 12, 2, 10, 0, tzinfo=TZ)
    assert works.deadline_at.utcoffset().total_seconds() == 3600
    assert works.language == "nl"
    assert works.refs == {"tenderned": "447088", "tenderned_kenmerk": "621696", "reference": "Z.663188"}
    assert works.platform_url is None
    assert works.documents[0].url == f"{API}/447088/pdf"
    assert works.documents[0].kind == "notice"
    assert any(d.kind == "procurement_documents" and d.title.startswith("Aanbestedingsleidraad")
               and d.url == f"{API}/447088/documenten/15163647/content" for d in works.documents)

    eu = by_id["446894"]  # EU tender run on Mercell, also published on TED
    assert eu.notice_type is NoticeType.TENDER
    assert eu.legal_basis == "32014L0024"
    assert eu.refs["ted"] == "755500-2026"
    assert eu.platform_url == "https://s2c.mercell.com/today/215165"
    assert eu.cpv_codes == ["50500000"]
    assert eu.deadline_at == datetime(2026, 11, 19, 12, 0, tzinfo=TZ)

    consult = by_id["447195"]  # market consultation: deadline is a date only
    assert consult.notice_type is NoticeType.PLAN
    assert consult.cpv_codes == ["90722000", "90713100"]  # main CPV first
    assert consult.deadline_at == datetime(2026, 11, 2, 23, 59, tzinfo=TZ)
    assert any(d.kind == "consultation" for d in consult.documents)

    stopped = by_id["447102"]  # Defensie: early termination of an award procedure
    assert stopped.buyer_name == "Ministerie van Defensie"
    assert stopped.notice_type is NoticeType.CANCELLATION
    assert stopped.status is Status.CANCELLED
    assert stopped.cpv_codes[0] == "60100000"


@respx.mock
async def test_seen_ids_from_state_are_not_refetched():
    respx.get(API).mock(return_value=httpx.Response(200, json=load_json("tenderned", "list_page1.json")))
    routes = _mock_details()
    notices, checkpoints = await run_sync(TenderNed, datetime(2026, 9, 1, tzinfo=UTC),
                                          state={"seen": {"446894": "2026-10-30", "1": "2020-01-01"}})
    assert [n.source_id for n in notices] == ["440202"]
    assert routes["446894"].call_count == 0
    # stale ids are pruned from the state
    assert checkpoints[-1].state["seen"] == {"446894": "2026-10-30", "440202": "2026-09-17"}


@respx.mock
async def test_empty_result_still_checkpoints():
    respx.get(API).mock(return_value=httpx.Response(200, json={"content": [], "last": True, "totalElements": 0}))
    notices, checkpoints = await run_sync(TenderNed, SINCE)
    assert notices == [] and len(checkpoints) == 1


@respx.mock
async def test_detail_404_falls_back_to_list_item():
    page = load_json("tenderned", "list_page0.json")
    page["content"] = page["content"][2:]
    page["last"] = True
    respx.get(API).mock(return_value=httpx.Response(200, json=page))
    respx.get(f"{API}/447088").mock(return_value=httpx.Response(404, json={"message": "not found"}))
    notices, _ = await run_sync(TenderNed, SINCE)
    (n,) = notices
    assert validate_notice(n) == []
    assert n.title == "Bouw- en woonrijp maken Zeeheldenbuurt Hoogeveen"
    assert n.notice_type is NoticeType.TENDER
    assert n.deadline_at == datetime(2026, 12, 2, 10, 0, tzinfo=TZ)
    assert n.procedure_type == "Openbaar"


@respx.mock
async def test_refresh_defence_directive_award():
    _mock_details()
    conn, client = make_connector(TenderNed)
    try:
        n = await conn.refresh(StoredRef("440202", "", {"tenderned": "440202"}, {}))
        respx.get(f"{API}/999").mock(return_value=httpx.Response(404, json={}))
        gone = await conn.refresh(StoredRef("999", "", {}, {}))
    finally:
        await client.aclose()
    assert gone is None
    assert validate_notice(n) == []
    assert n.title == "Overeenkomst CTG 5.56mm Blanks"
    assert n.notice_type is NoticeType.AWARD and n.status is Status.AWARDED
    assert n.legal_basis == "32009L0081"
    assert n.cpv_codes == ["35330000", "35331500"]
    assert n.refs["ted"] == "639742-2026"
    assert n.published_at == datetime(2026, 9, 17, 4, 7, 20, 643803, tzinfo=TZ)


@respx.mock
async def test_detail_requests_run_concurrently_and_keep_list_order():
    _mock_list()
    in_flight = peak = 0

    def slow(pid: str):
        async def handler(request):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.05 if pid == "447195" else 0.01)  # first item is the slowest
            in_flight -= 1
            return httpx.Response(200, json=load_json("tenderned", f"detail_{pid}.json"))
        return handler

    for pid in ("447195", "447102", "447088", "446894"):
        respx.get(f"{API}/{pid}").mock(side_effect=slow(pid))
    respx.get(url__regex=rf"{API}/\d+/documenten").mock(return_value=httpx.Response(200, json={"documenten": []}))

    notices, _ = await run_sync(TenderNed, SINCE)
    assert peak >= 3  # details of both pages were in flight together
    assert [n.source_id for n in notices] == ["447195", "447102", "447088", "446894"]


@respx.mock
async def test_detail_server_error_propagates():
    _mock_list()
    _mock_details()
    respx.get(f"{API}/447102").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(HttpError):
        await run_sync(TenderNed, SINCE)
