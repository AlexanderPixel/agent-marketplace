"""EU Funding & Tenders (SEDIA search API) connector against recorded, trimmed API pages."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import respx

from tenderhub.classify import classify_defence
from tenderhub.connector import StoredRef
from tenderhub.connectors.eu_funding_tenders import API, EuFundingTenders
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_json, make_connector, run_sync

SINCE = datetime(2026, 10, 28, tzinfo=UTC)
NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
BRU = ZoneInfo("Europe/Brussels")
FIX = "eu_funding_tenders"


class SmallPages(EuFundingTenders):
    """The recorded tender pages were trimmed to 3 rows of 5; page size 3 keeps them consistent."""

    page_size = 3


def _part(request: httpx.Request, name: str) -> object:
    """Pull one JSON part out of the multipart body."""
    body = request.content.decode()
    m = re.search(rf'name="{name}"[^\r\n]*\r\n(?:[^\r\n]+\r\n)*\r\n(.*?)\r\n--', body, re.S)
    assert m, f"multipart part {name!r} missing"
    return json.loads(m.group(1))


def _router(calls: list[tuple[dict, dict]]):
    """Answer by query kind: open defence calls, changed defence calls, tenders (paged)."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = _part(request, "query")
        calls.append((dict(request.url.params), query))
        musts = query["bool"]["must"]
        types = next(m["terms"]["type"] for m in musts if "type" in m.get("terms", {}))
        if types == ["0"]:
            page = request.url.params["pageNumber"]
            return httpx.Response(200, json=load_json(FIX, f"tenders_p{page}.json"))
        if any("status" in m.get("terms", {}) for m in musts):
            return httpx.Response(200, json=load_json(FIX, "calls_open.json"))
        return httpx.Response(200, json=load_json(FIX, "calls_changed.json"))

    return handler


@respx.mock
async def test_sync_bootstraps_open_defence_calls_and_pages_tenders():
    calls: list[tuple[dict, dict]] = []
    route = respx.post(url__startswith=API).mock(side_effect=_router(calls))

    notices, checkpoints = await run_sync(SmallPages, SINCE)

    # open EDF calls (first run only), EDF calls changed since, tenders page 1 + page 2 (passes run concurrently)
    assert route.call_count == 4
    params = [p for p, _ in calls]
    assert all(p["apiKey"] == "SEDIA" and p["pageSize"] == "3" for p in params)
    assert sorted(p["pageNumber"] for p in params) == ["1", "1", "1", "2"]
    first = route.calls[0].request
    assert _part(first, "sort") == {"field": "identifier", "order": "ASC"}
    assert "cftLeadContractingAuthorityCode" in _part(first, "displayFields")
    musts = [q["bool"]["must"] for _, q in calls]
    open_q = next(m for m in musts if any("status" in t.get("terms", {}) for t in m))
    changed_q = next(m for m in musts if m is not open_q and {"terms": {"type": ["1", "8"]}} in m)
    tender_q = next(m for m in musts if {"terms": {"type": ["0"]}} in m)
    assert sum(m == tender_q for m in musts) == 2      # same query, two pages, stop at totalResults
    assert {"terms": {"frameworkProgramme": ["44181033", "31109727"]}} in open_q
    assert {"terms": {"status": ["31094501", "31094502"]}} in open_q
    assert {"range": {"esDA_IngestDate": {"gte": "2026-10-28T00:00:00.000+0000"}}} in changed_q
    assert tender_q == [{"terms": {"type": ["0"]}},
                        {"range": {"esDA_IngestDate": {"gte": "2026-10-28T00:00:00.000+0000"}}}]

    assert len(notices) == 8
    assert [p for n in notices for p in validate_notice(n)] == []
    # results are yielded in pass order with one checkpoint per pass
    assert [c.state["last_pass"] for c in checkpoints] == ["open_calls", "calls", "tenders"]
    assert [n.notice_type for n in notices[3:]].count(NoticeType.PLAN) == 1
    assert all(n.source_id.startswith(("EDF-", "cascade-")) for n in notices[:3])
    assert checkpoints[-1].state["bootstrapped"] is True
    assert len({n.source_id for n in notices}) == 8

    by_id = {n.source_id: n for n in notices}
    # EU institution tender: buyer, CPV, value, TED cross-reference, deadline in the buyer's zone
    t = by_id["49b6860e-3063-4206-9dbb-cbfd08360d40-CN"]
    assert t.source == "eu_funding_tenders"
    assert t.url == ("https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/"
                     "tender-details/49b6860e-3063-4206-9dbb-cbfd08360d40-CN")
    assert t.title.startswith("Delivering IT and Firearms Equipment to the NFFP")
    assert t.notice_type is NoticeType.TENDER
    assert t.buyer_name == "European Union Agency For Law Enforcement Training"
    assert t.buyer_country is None
    assert t.cpv_codes == ["30232100", "30191400", "22815000", "73432000", "30200000", "30213300"]
    assert t.procedure_type == "Open procedure"
    assert t.value_amount == 400000.0 and t.value_currency == "EUR"
    assert t.published_at == datetime(2026, 10, 30, tzinfo=BRU)
    # SEDIA says '2027-01-11T11:00:59+0000' but it is Budapest wall time (TED: 11:00:59+01:00)
    assert t.deadline_at == datetime(2027, 1, 11, 11, 0, 59, tzinfo=timezone(timedelta(hours=1)))
    assert t.refs == {"sedia": "49b6860e-3063-4206-9dbb-cbfd08360d40-CN", "procedure": "CEPOL/2026/OP/0012",
                      "ted": "754856-2026", "oj": "211/2026", "party_id": "47352388"}
    assert t.platform_url == "https://www.cepol.europa.eu/"
    assert t.description.startswith("The subject of this call for tenders")

    pin = by_id["3dd5b064-77cf-4723-ab3c-91a66842cc2f-PIN"]
    assert pin.notice_type is NoticeType.PLAN and pin.deadline_at is None
    assert pin.buyer_name == "European Parliament, SG - Directorate-General for Security and Safety"
    assert pin.refs["ted"] == "473220-2026"

    exa = by_id["533e0b3c-da10-4b27-b4e3-0355bda42d61-EXA"]   # ex-ante publicity: registration deadline
    assert exa.notice_type is NoticeType.TENDER
    assert exa.deadline_at == datetime(2026, 10, 26, 21, 59, 59, tzinfo=UTC)
    assert exa.status is Status.CLOSED and "ted" not in exa.refs

    eeas = by_id["f38cf358-a8ea-4a60-9284-4f9f7a3a4751-CN"]  # delegation in China: local zone + CNY
    assert eeas.deadline_at == datetime(2026, 11, 30, 9, 0, 59, tzinfo=UTC)
    assert eeas.value_currency == "CNY" and eeas.procedure_type == "Competitive procedure with negotiation"

    closed = by_id["7f34d1fb-d0da-4a45-8457-9b42fe70aea0-CN"]
    assert closed.status is Status.CLOSED

    # EDIP call topic: multiple cut-offs, budget, defence programme marked
    seap = by_id["EDF-EDIP-P-2026-2027-FNLC-SA-SEAP"]
    assert seap.url.endswith("/topic-details/EDF-EDIP-P-2026-2027-FNLC-SA-SEAP")
    assert seap.notice_type is NoticeType.TENDER
    assert seap.buyer_name == "European Commission — European Defence Fund (EDF)"
    assert seap.refs["programme"] == "EDF" and seap.refs["instrument"] == "EDIP"
    assert seap.refs["call"] == "EDF-EDIP-P-2026-2027-FNLC-SA"
    assert seap.value_amount == 10_000_000.0 and seap.value_currency == "EUR"
    assert seap.procedure_type == "Call for proposals: EDF Lump Sum Grants"
    assert seap.language == "en" and seap.cpv_codes == []
    assert seap.description.startswith("Expected Impact:")
    assert classify_defence(seap)[0]

    mab = by_id["EDF-EDIP-USI-2026-LS-IRA-MAB"]
    assert mab.title == "Missiles, ammunition and bombs"
    assert mab.value_amount == 180_000_000.0

    # cascade (FSTP) call run by an EDF project consortium
    cascade = by_id["cascade-46905650"]
    assert cascade.url.endswith("/competitive-calls-cs/46905650")
    assert cascade.buyer_name == "BATTLEVERSE consortium (European Defence Fund (EDF) project 101224034)"
    assert cascade.deadline_at == datetime(2026, 11, 16, 18, 0, tzinfo=BRU)
    assert cascade.value_amount == 1_200_000.0
    assert cascade.refs["topic"] == "EDF-2024-RA-SIMTRAIN-BRG-STEP" and cascade.refs["project"] == "101224034"


def test_mapping_status_and_next_cut_off_are_relative_to_now():
    conn, _ = make_connector(EuFundingTenders)
    rows = {r["metadata"]["identifier"][0]: r for r in load_json(FIX, "calls_open.json")["results"]}
    seap = rows["EDF-EDIP-P-2026-2027-FNLC-SA-SEAP"]
    n = conn.to_notice(seap, NOW)
    assert n.status is Status.OPEN
    assert n.deadline_at == datetime(2026, 11, 12, 17, 0, tzinfo=BRU)   # date-only cut-off -> 17:00 Brussels
    later = conn.to_notice(seap, datetime(2026, 12, 1, tzinfo=UTC))
    assert later.deadline_at == datetime(2027, 2, 16, 17, 0, tzinfo=BRU)  # next cut-off
    assert later.status is Status.OPEN
    after_all = conn.to_notice(seap, datetime(2028, 6, 1, tzinfo=UTC))
    assert after_all.deadline_at == datetime(2028, 5, 30, 17, 0, tzinfo=BRU) and after_all.status is Status.CLOSED
    assert conn.to_notice({"metadata": {"type": ["6"], "identifier": ["x"], "title": ["news"]}}, NOW) is None


@respx.mock
async def test_second_run_skips_bootstrap_and_empty_results_still_checkpoint():
    empty = {"totalResults": 0, "results": []}
    route = respx.post(url__startswith=API).mock(return_value=httpx.Response(200, json=empty))
    notices, checkpoints = await run_sync(EuFundingTenders, SINCE, state={"bootstrapped": True})
    assert notices == [] and route.call_count == 2 and len(checkpoints) == 2
    assert all(c.state["bootstrapped"] for c in checkpoints)
    assert route.calls[0].request.url.params["pageSize"] == "100"


@respx.mock
async def test_remaining_pages_fetched_concurrently_and_failure_propagates():
    pages = {1: [{"metadata": {}}] * 3, 2: [{"metadata": {}}] * 3, 3: [{"metadata": {}}]}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["pageNumber"])
        seen.append(request.url.params["pageNumber"])
        return httpx.Response(200, json={"totalResults": 7, "results": pages[page]})

    respx.post(url__startswith=API).mock(side_effect=handler)
    conn, client = make_connector(SmallPages)
    try:
        rows = await conn._collect({"bool": {"must": []}})
    finally:
        await client.aclose()
    assert len(rows) == 7 and sorted(seen) == ["1", "2", "3"]

    respx.post(url__startswith=API).mock(return_value=httpx.Response(404, text="nope"))
    conn, client = make_connector(SmallPages)
    try:
        gen = conn.sync(SINCE, {"bootstrapped": True})
        try:
            await gen.__anext__()
            raise AssertionError("expected HttpError")
        except Exception as e:  # noqa: BLE001
            assert "404" in str(e)
        await gen.aclose()
    finally:
        await client.aclose()


@respx.mock
async def test_refresh_fetches_one_tender_by_identifier():
    route = respx.post(url__startswith=API).mock(
        return_value=httpx.Response(200, json=load_json(FIX, "refresh_tender.json")))
    conn, client = make_connector(EuFundingTenders)
    sid = "49b6860e-3063-4206-9dbb-cbfd08360d40-CN"
    try:
        n = await conn.refresh(StoredRef(source_id=sid, url="", refs={}, raw={"type": "0", "identifier": sid}))
    finally:
        await client.aclose()
    assert n is not None and n.source_id == sid and n.refs["ted"] == "754856-2026"
    assert _part(route.calls[0].request, "query") == {
        "bool": {"must": [{"terms": {"identifier": [sid]}}, {"terms": {"type": ["0"]}}]}}
