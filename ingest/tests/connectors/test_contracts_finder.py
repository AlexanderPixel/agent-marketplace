"""Contracts Finder connector against recorded OCDS search pages and a single-notice release package."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import respx

from tenderhub.connector import StoredRef
from tenderhub.connectors.contracts_finder import RELEASE, SEARCH, ContractsFinder, notice_guid
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_json, make_connector, run_sync

SINCE = datetime(2026, 10, 27, tzinfo=UTC)


@respx.mock
async def test_sync_follows_links_next_and_maps_fields():
    page1, page2 = load_json("contracts_finder", "search_page1.json"), load_json("contracts_finder", "search_page2.json")
    next_url = page1["links"]["next"]
    # the cursor route goes first: the plain search route's params pattern would also match the `next` url
    second = respx.get(next_url).mock(return_value=httpx.Response(200, json=page2))
    first = respx.get(SEARCH, params={"publishedFrom": "2026-10-27T00:00:00Z", "limit": "100"}).mock(
        return_value=httpx.Response(200, json=page1))

    notices, checkpoints = await run_sync(ContractsFinder, SINCE)

    assert first.call_count == 1 and second.call_count == 1
    assert "cursor=" in str(second.calls[0].request.url)
    assert len(notices) == 6 and len(checkpoints) == 1
    assert checkpoints[0].state == {"published_from": "2026-10-27T00:00:00Z", "pages": 2}
    assert [p for n in notices for p in validate_notice(n)] == []
    assert all(n.source == "contracts_finder" and n.buyer_country == "GB" for n in notices)

    by_id = {n.source_id: n for n in notices}
    tender = by_id["c95b145b-df51-4e53-a3ae-c7f8e086d556"]
    assert tender.url == "https://www.contractsfinder.service.gov.uk/Notice/c95b145b-df51-4e53-a3ae-c7f8e086d556"
    assert tender.notice_type is NoticeType.TENDER and tender.status is Status.OPEN
    assert tender.buyer_name == "Anthro JV" and tender.buyer_city == "London"
    assert tender.cpv_codes == ["35125300"]
    assert tender.published_at == datetime(2026, 10, 30, 12, 50, 2, tzinfo=UTC)
    assert tender.deadline_at == datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    assert tender.value_amount == 500000.0 and tender.value_currency == "GBP"
    assert tender.platform_url.startswith("https://www.competefor.co.uk/")
    assert tender.refs["ocid"].startswith("ocds-b5fd17-")
    assert tender.refs["release"] == "c95b145b-df51-4e53-a3ae-c7f8e086d556-919851"
    assert tender.language == "en"

    plan = by_id["e004e4c9-7238-4902-80c1-52f0650e4ca0"]
    assert plan.notice_type is NoticeType.PLAN and plan.status is Status.UNKNOWN
    assert plan.deadline_at is None and plan.buyer_name == "AMEY DEFENCE SERVICES LIMITED"
    assert plan.title.startswith("FDIS - LiDAR Scanning")

    amended = by_id["c32f774f-8897-4558-8ded-6c499500b751"]  # tenderAmendment of a tender notice
    assert amended.notice_type is NoticeType.TENDER
    assert amended.buyer_name == "Ministry of Justice"
    assert amended.deadline_at == datetime(2026, 11, 12, 17, 0, tzinfo=UTC)
    assert amended.published_at == datetime(2026, 10, 29, 9, 50, 10, tzinfo=UTC)

    award = by_id["fe5875f1-9491-4431-b914-aaf615b6f5f4"]
    assert award.notice_type is NoticeType.AWARD and award.status is Status.AWARDED
    assert award.title == "Co-Sourced Internal Audit Service"
    assert award.buyer_name == "ORBIT GROUP LIMITED" and award.buyer_city == "Coventry"
    assert award.cpv_codes == ["79212200"]
    assert award.value_amount == 840000.0 and award.value_currency == "GBP"
    assert award.procedure_type == "Call-off from a framework agreement"
    assert award.published_at == datetime(2026, 10, 29, 22, 46, 38, tzinfo=UTC)
    assert award.refs["procedure"] == "GPL0251"
    # the self link to the Contracts Finder notice page is the notice url, not a document
    assert all(d.url != award.url for d in award.documents)

    assert "ITEP1159 - Gov Delivery Email Platform 26/27/28" in {n.title for n in notices}


@respx.mock
async def test_older_release_of_same_notice_is_not_yielded_twice():
    page = load_json("contracts_finder", "search_page1.json")
    newest = page["releases"][2]                       # tenderAmendment, release 919787
    older = {**newest, "id": newest["id"].rsplit("-", 1)[0] + "-919604", "tag": ["tender"]}
    page = {**page, "releases": [newest, older], "links": {}}
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json=page))

    notices, _ = await run_sync(ContractsFinder, SINCE)

    assert [n.refs["release"] for n in notices] == [newest["id"]]


@respx.mock
async def test_empty_result_still_checkpoints():
    route = respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"releases": []}))
    notices, checkpoints = await run_sync(ContractsFinder, SINCE)
    assert route.call_count == 1
    assert notices == [] and len(checkpoints) == 1


@respx.mock
async def test_refresh_reads_latest_release_of_notice():
    guid = "c32f774f-8897-4558-8ded-6c499500b751"
    respx.get(RELEASE.format(guid=guid)).mock(
        return_value=httpx.Response(200, json=load_json("contracts_finder", "release_c32f774f.json")))
    respx.get(RELEASE.format(guid="00000000-0000-0000-0000-000000000000")).mock(
        return_value=httpx.Response(404, json=[{"code": "NotFound"}]))
    conn, client = make_connector(ContractsFinder)
    try:
        n = await conn.refresh(StoredRef(guid, f"https://www.contractsfinder.service.gov.uk/Notice/{guid}", {}, {}))
        gone = await conn.refresh(StoredRef("00000000-0000-0000-0000-000000000000", "", {}, {}))
    finally:
        await client.aclose()
    assert n is not None and validate_notice(n) == []
    assert n.source_id == guid and n.notice_type is NoticeType.TENDER
    assert n.deadline_at == datetime(2026, 11, 12, 17, 0, tzinfo=UTC)
    assert gone is None


def test_notice_guid():
    assert notice_guid("fe5875f1-9491-4431-b914-aaf615b6f5f4-919751") == "fe5875f1-9491-4431-b914-aaf615b6f5f4"
