"""CVP IS (viesiejipirkimai.lt) connector against recorded, trimmed HTML pages."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import httpx
import respx

from tenderhub.connector import StoredRef
from tenderhub.connectors.cvp_lt import (
    BASE, LIST_URL, ORG_URL, CvpLt, _gather_limited, java_date, legal_basis, lt_date, parse_amount,
)
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_text, make_connector, run_sync

EET = timezone(timedelta(hours=2))
# between the 3rd row of page 2 (11:18 EET) and the last one (05:22 EET) -> stop on page 2
SINCE = datetime(2026, 10, 30, 6, 0, tzinfo=EET)
DETAIL_FIXTURES = {
    "10290864": "pmc_10290864.html",
    "9987006": "cft_9987006.html",
    "10287717": "cft_10287717.html",
    "8417623": "cft_8417623.html",
    "9286989": "cft_9286989.html",
}


def _detail(request: httpx.Request) -> httpx.Response:
    rid = request.url.params["resourceId"]
    return httpx.Response(200, text=load_text("cvp_lt", DETAIL_FIXTURES[rid]))


def _org(request: httpx.Request) -> httpx.Response:
    if request.url.params["id"] == "1146":
        return httpx.Response(200, text=load_text("cvp_lt", "org_1146.html"))
    return httpx.Response(404, text="not found")


def _mock_details() -> dict[str, respx.Route]:
    return {
        "cft": respx.get(f"{BASE}/epps/cft/prepareViewCfTWS.do").mock(side_effect=_detail),
        "pmc": respx.get(f"{BASE}/epps/pmc/viewPmc.do").mock(side_effect=_detail),
        "org": respx.get(ORG_URL).mock(side_effect=_org),
    }


@respx.mock
async def test_sync_walks_newest_first_and_maps_fields():
    lst = respx.get(LIST_URL).mock(side_effect=[
        httpx.Response(200, text=load_text("cvp_lt", "list_page1.html")),
        httpx.Response(200, text=load_text("cvp_lt", "list_page2.html")),
    ])
    routes = _mock_details()

    notices, checkpoints = await run_sync(CvpLt, SINCE)

    # page 2 holds a row older than `since` -> no page 3 even though the next button is enabled
    assert lst.call_count == 2
    p1, p2 = (c.request.url.params for c in lst.calls)
    assert p1["searchType"] == "cftFTS" and p1["d-3680175-s"] == "datePublished" and p1["d-3680175-o"] == "1"
    assert p1["d-3680175-p"] == "1" and p2["d-3680175-p"] == "2" and p1["T01_ps"] == "50"
    assert routes["cft"].call_count == 3 and routes["pmc"].call_count == 1

    assert [n.source_id for n in notices] == ["10290864", "9987006", "10287717", "8417623"]
    assert [p for n in notices for p in validate_notice(n)] == []
    by_id = {n.source_id: n for n in notices}

    net = by_id["9987006"]
    assert net.source == "cvp_lt"
    assert net.url == f"{BASE}/epps/cft/prepareViewCfTWS.do?resourceId=9987006"
    assert net.title == "Kompiuterinio tinklo įranga"
    assert net.notice_type is NoticeType.TENDER and net.status is Status.OPEN
    assert net.buyer_name == "Viešoji įstaiga CPO LT"
    assert net.buyer_country == "LT" and net.buyer_city == "Vilnius"
    assert net.cpv_codes == ["32420000"]
    assert net.procedure_type == "Atviras konkursas"
    assert net.legal_basis == "32014L0024"
    assert net.published_at == datetime(2026, 10, 30, 14, 9, 36, tzinfo=UTC)
    assert net.deadline_at == datetime(2026, 11, 16, 8, 0, tzinfo=UTC)
    assert net.value_amount is None and net.value_currency is None
    assert net.language == "lt"
    assert net.description.startswith("Kompiuterinio tinklo įranga ligoninės reikmėms")
    assert net.platform_url == f"{BASE}/epps/cft/listContractDocuments.do?resourceId=9987006"
    assert net.documents[0].url == f"{BASE}/epps/cft/downloadNoticeForAdvSearch.do?resourceId=9987006"
    assert net.refs == {"cvp": "9987006", "procedure": "1002/2272/4809046"}

    consultation = by_id["10290864"]  # market consultation -> plan
    assert consultation.notice_type is NoticeType.PLAN
    assert consultation.url == f"{BASE}/epps/pmc/viewPmc.do?resourceId=10290864"
    assert consultation.buyer_name == "VšĮ Vilniaus rajono poliklinika"  # " (PV)" marker stripped
    assert consultation.buyer_city is None  # organisation page 404 -> best effort
    assert consultation.cpv_codes == ["33100000"]
    assert consultation.deadline_at == datetime(2026, 11, 6, 13, 0, tzinfo=UTC)

    air_force = by_id["10287717"]
    assert air_force.buyer_name == "Lietuvos kariuomenės Karinių oro pajėgų Aviacijos bazė"
    assert air_force.value_amount == 69421.49 and air_force.value_currency == "EUR"
    assert air_force.cpv_codes == ["44212000"]

    vests = by_id["8417623"]  # defence directive procedure
    assert vests.title == "Šarvinės liemenės"
    assert vests.legal_basis == "32009L0081"
    assert vests.procedure_type == "Skelbiamos derybos pagal GSPĮ"
    assert vests.cpv_codes == ["35815100"]
    assert vests.value_amount == 3870000.0
    assert vests.refs["ted"] == "523474-2026" and vests.refs["ted_all"] == "418553-2026,523474-2026"

    # resumable state: detail cache for every yielded row, buyer-city cache
    final = checkpoints[-1].state
    assert set(final["details"]) == set(by_id)
    assert final["orgs"]["1146"] == "Vilnius"


@respx.mock
async def test_second_sync_reuses_cached_details():
    respx.get(LIST_URL).mock(side_effect=[
        httpx.Response(200, text=load_text("cvp_lt", "list_page1.html")),
        httpx.Response(200, text=load_text("cvp_lt", "list_page2.html")),
        httpx.Response(200, text=load_text("cvp_lt", "list_page1.html")),
        httpx.Response(200, text=load_text("cvp_lt", "list_page2.html")),
    ])
    routes = _mock_details()
    first, cps = await run_sync(CvpLt, SINCE)
    org_calls = routes["org"].call_count

    second, _ = await run_sync(CvpLt, SINCE, state=cps[-1].state)

    assert routes["cft"].call_count + routes["pmc"].call_count == 4  # nothing re-fetched
    assert org_calls == 4 and routes["org"].call_count == 4  # buyer cities (and 404s) are cached too
    assert [n.content_hash() for n in second] == [n.content_hash() for n in first]


@respx.mock
async def test_stops_on_last_page():
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=load_text("cvp_lt", "list_last.html")))
    respx.get(f"{BASE}/epps/dps/prepareViewCfTDPSWS.do").mock(return_value=httpx.Response(404))
    notices, checkpoints = await run_sync(CvpLt, datetime(2024, 1, 1, tzinfo=UTC))
    assert len(respx.calls) == 3  # one list page (next button disabled) + two detail attempts
    assert [n.source_id for n in notices] == ["29622", "29016"]
    assert all(n.status is Status.OPEN and n.notice_type is NoticeType.TENDER for n in notices)
    assert notices[1].buyer_name == "Vilniaus universitetas"
    assert notices[1].deadline_at is None
    assert [p for n in notices for p in validate_notice(n)] == []
    assert len(checkpoints) == 2


@respx.mock
async def test_refresh_picks_up_detail_changes():
    routes = _mock_details()
    conn, client = make_connector(CvpLt)
    try:
        ref = StoredRef(
            source_id="9286989",
            url=f"{BASE}/epps/cft/prepareViewCfTWS.do?resourceId=9286989",
            refs={"cvp": "9286989"},
            raw={"row": {"id": "9286989", "title": "Kolimatorinis taikiklis su priartintoju automatiniam ginklui (PPR-583)",
                         "href": f"{BASE}/epps/cft/prepareViewCfTWS.do?resourceId=9286989",
                         "published": "Thu Aug 20 05:11:00 EEST 2026", "deadline": "Mon Oct 05 15:00:00 EEST 2026",
                         "procedure": "Skelbiamos derybos pagal GSPĮ", "status": "Pasiūlymų teikimas"}},
        )
        n = await conn.refresh(ref)
    finally:
        await client.aclose()
    assert routes["cft"].call_count == 1
    assert validate_notice(n) == []
    assert n.deadline_at == datetime(2026, 10, 29, 13, 0, tzinfo=UTC)  # extended deadline from the detail page
    assert n.status is Status.CLOSED  # deadline has passed
    assert n.legal_basis == "32009L0081" and n.cpv_codes == ["35340000"]
    assert n.buyer_name == "Išteklių agentūra prie Lietuvos Respublikos vidaus reikalų ministerijos"
    assert n.value_amount == 1145504.13
    assert n.refs["ted"] == "745279-2026"  # the newest TED notice links to the TED connector
    assert n.published_at == datetime(2026, 8, 20, 2, 11, tzinfo=UTC)


def test_date_and_amount_helpers():
    assert java_date("Fri Oct 30 16:14:16 EET 2026") == datetime(2026, 10, 30, 14, 14, 16, tzinfo=UTC)
    assert java_date("Thu Sep 24 05:12:06 EEST 2026") == datetime(2026, 9, 24, 2, 12, 6, tzinfo=UTC)
    assert java_date("") is None
    assert lt_date("16/11/2026 10:00") == datetime(2026, 11, 16, 8, 0, tzinfo=UTC)
    assert parse_amount("1145504,13.") == 1145504.13
    assert parse_amount("3870000.") == 3870000.0
    assert parse_amount("0.0") is None and parse_amount("") is None
    assert legal_basis("2014/25/ES (Komunalinė)") == "32014L0025"
    assert legal_basis("") is None


async def test_gather_limited_bounds_concurrency_and_keeps_order():
    live = peak = 0

    async def job(i: int) -> int:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01 * (5 - i % 5))
        live -= 1
        return i

    assert await _gather_limited([job(i) for i in range(12)], limit=3) == list(range(12))
    assert peak == 3


@respx.mock
async def test_list_in_dd_mm_locale_still_stops_and_parses():
    """With `Accept-Language: en` the list shows '30/10/2026 16:54:47' dates and '423,500.00' values."""
    lst = respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=load_text("cvp_lt", "list_ltdates.html")))
    respx.get(f"{BASE}/epps/cft/prepareViewCfTWS.do").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/epps/pmc/viewPmc.do").mock(return_value=httpx.Response(404))
    since = datetime(2026, 10, 30, 14, 30, tzinfo=UTC)  # 16:30 Vilnius: between rows 3 and 4

    notices, _ = await run_sync(CvpLt, since)

    assert lst.call_count == 1  # an older row was reached -> no next page despite the enabled button
    assert [n.source_id for n in notices] == ["10291283", "10291115", "10290982"]
    assert [p for n in notices for p in validate_notice(n)] == []
    first, _, consultation = notices
    assert first.published_at == datetime(2026, 10, 30, 14, 54, 47, tzinfo=UTC)
    assert first.deadline_at == datetime(2026, 11, 6, 6, 0, tzinfo=UTC)
    assert first.buyer_name == "UAB Kauno butų ūkis" and first.title == "Statybos darbų priežiūra S12"
    assert consultation.notice_type is NoticeType.PLAN
    assert consultation.value_amount == 423500.0 and consultation.value_currency == "EUR"
    assert consultation.buyer_name == "VĮ Registrų centras"


def test_amount_locales():
    assert parse_amount("87,603.31") == 87603.31
    assert parse_amount("3,870,000") == 3870000.0
    assert parse_amount("1.234,56") == 1234.56
    assert lt_date("30/10/2026 16:54:47") == datetime(2026, 10, 30, 14, 54, 47, tzinfo=UTC)
