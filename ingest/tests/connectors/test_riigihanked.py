"""Riigihangete register (Estonia) against recorded API responses."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import httpx
import pytest
import respx

from tenderhub.connector import Context, StoredRef
from tenderhub.connectors.riigihanked import API, BOOTSTRAP, SEARCH, Riigihanked
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_json, make_connector, run_sync

SINCE = datetime(2026, 10, 28, tzinfo=UTC)          # 02:00 in Tallinn -> first day 2026-10-28
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)     # last day 2026-10-29
COOKIES = [("set-cookie", "JSESSIONID=ABC; Path=/rhr; Secure; HttpOnly"), ("set-cookie", "XSRF-TOKEN=tok-1; Path=/")]


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(Context, "now", lambda self: NOW)


def _mock_details() -> None:
    def header(request: httpx.Request, pid: str) -> httpx.Response:
        return httpx.Response(200, json=load_json("riigihanked", f"header_{pid}.json"))

    def latest(request: httpx.Request, pid: str) -> httpx.Response:
        return httpx.Response(200, json=load_json("riigihanked", f"latest_{pid}.json"))

    def general(request: httpx.Request, vid: str) -> httpx.Response:
        return httpx.Response(200, json=load_json("riigihanked", f"general_{vid}.json"))

    respx.get(url__regex=rf"{re.escape(API)}/procurement/(?P<pid>\d+)/header-info").mock(side_effect=header)
    respx.get(url__regex=rf"{re.escape(API)}/procurement/(?P<pid>\d+)/latest-version").mock(side_effect=latest)
    respx.get(url__regex=rf"{re.escape(API)}/proc-vers/(?P<vid>\d+)/general-info").mock(side_effect=general)


@respx.mock
async def test_sync_walks_days_and_maps_fields():
    boot = respx.get(BOOTSTRAP).mock(return_value=httpx.Response(200, json={"version": "7.2.2"}, headers=COOKIES))

    def search(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["X-XSRF-TOKEN"] == "tok-1"
        assert "JSESSIONID=ABC" in request.headers.get("cookie", "")
        day = body["filter"]["procurementProcessRevealDateBegin"]
        assert body["filter"]["procurementProcessRevealDateEnd"] == day
        assert body["orderBy"] == {"procurementProcessRevealDate": "desc"}
        return httpx.Response(200, json=load_json("riigihanked", f"search_{day}.json"))

    route = respx.post(SEARCH).mock(side_effect=search)
    _mock_details()

    notices, checkpoints = await run_sync(Riigihanked, SINCE)

    assert boot.call_count == 1
    assert route.call_count == 2
    assert [c.state for c in checkpoints] == [{"day": "2026-10-28"}, {"day": "2026-10-29"}]
    assert len(notices) == 5
    assert [p for n in notices for p in validate_notice(n)] == []

    by_id = {n.source_id: n for n in notices}
    saw = by_id["10279324"]  # Estonian Centre for Defence Investment: mobile saw rental, open procedure
    assert saw.source == "riigihanked"
    assert saw.url == "https://riigihanked.riik.ee/rhr-web/#/procurement/10279324/general-info"
    assert saw.title == "Mobiilse saekaatri rent"
    assert saw.buyer_name == "Riigi Kaitseinvesteeringute Keskus"
    assert saw.buyer_id == "70009764"
    assert saw.buyer_country == "EE"
    assert saw.notice_type is NoticeType.TENDER
    assert saw.status is Status.OPEN
    assert saw.procedure_type == "Open procedure"
    assert saw.cpv_codes == ["77211100"]
    assert saw.published_at == datetime(2026, 10, 28, 5, 0, 38, 38000, tzinfo=UTC)
    assert saw.deadline_at == datetime(2026, 11, 30, 8, 0, tzinfo=UTC)
    assert saw.description.startswith("Mobiilse saekaatri rent (koos operaatoriga)")
    assert saw.language == "et"
    assert saw.refs == {"rhr": "310139", "rhr_id": "10279324"}
    assert saw.raw["search"]["procurementId"] == 10279324
    assert saw.value_amount == 250000.0 and saw.value_currency == "EUR"

    consultation = by_id["10996644"]  # market consultation -> plan
    assert consultation.notice_type is NoticeType.PLAN
    assert consultation.buyer_name == "Tervisekassa"
    assert consultation.cpv_codes == ["33100000"]
    assert consultation.value_amount is None and consultation.value_currency is None

    dps = by_id["9735404"]  # dynamic purchasing system with several CPVs, open for entry
    assert dps.cpv_codes == ["90911000", "90911200", "77314000", "90620000"]
    assert dps.status is Status.OPEN
    assert dps.value_amount == 97500000.0

    terminated = by_id["10970025"]
    assert terminated.status is Status.CANCELLED
    assert terminated.buyer_name == "aktsiaselts TALLINNA SADAM"
    assert terminated.cpv_codes == ["44100000"]

    low_value = by_id["11006544"]
    assert low_value.procedure_type == "Low-value purchase"
    assert low_value.deadline_at == datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
    assert low_value.value_amount is None            # showCost == false: the buyer kept the estimate private

    # results come out day by day (oldest first) even though everything was fetched concurrently
    first_day = {"10279324", "9735404", "10996644"} & set(by_id)
    assert {n.source_id for n in notices[:len(first_day)]} == first_day


@respx.mock
async def test_expired_session_rebootstraps_and_empty_day_checkpoints():
    boot = respx.get(BOOTSTRAP).mock(return_value=httpx.Response(200, json={"version": "7.2.2"}, headers=COOKIES))
    route = respx.post(SEARCH).mock(side_effect=[httpx.Response(401), httpx.Response(200, json=[]),
                                                 httpx.Response(200, json=[])])

    notices, checkpoints = await run_sync(Riigihanked, SINCE)

    assert boot.call_count == 2 and route.call_count == 3
    assert notices == [] and len(checkpoints) == 2


@respx.mock
async def test_refresh_refetches_one_procurement_and_handles_missing():
    respx.get(f"{API}/procurement/1/header-info").mock(
        return_value=httpx.Response(404, json=[{"message": "PROCUREMENT_NOT_FOUND"}]))
    _mock_details()
    conn, client = make_connector(Riigihanked)
    try:
        n = await conn.refresh(StoredRef("10970025", "", {}, {}))
        gone = await conn.refresh(StoredRef("1", "", {}, {}))
    finally:
        await client.aclose()
    assert n is not None and validate_notice(n) == []
    assert n.title.startswith("Üldehitusmaterjalide")
    assert n.status is Status.CANCELLED
    assert n.deadline_at == datetime(2026, 11, 30, 12, 0, tzinfo=UTC)
    assert gone is None


@respx.mock
async def test_days_and_details_are_fetched_concurrently():
    """Day 1's search only answers once day 2's search is in flight, and a header-info only answers once
    another procurement's header-info is in flight: a sequential implementation would time out here."""
    import asyncio

    respx.get(BOOTSTRAP).mock(return_value=httpx.Response(200, json={}, headers=COOKIES))
    seen_days: set[str] = set()
    both_days = asyncio.Event()
    headers_in_flight = 0
    two_headers = asyncio.Event()

    async def search(request: httpx.Request) -> httpx.Response:
        day = json.loads(request.content)["filter"]["procurementProcessRevealDateBegin"]
        seen_days.add(day)
        if len(seen_days) == 2:
            both_days.set()
        await asyncio.wait_for(both_days.wait(), 2)
        return httpx.Response(200, json=load_json("riigihanked", f"search_{day}.json"))

    async def header(request: httpx.Request, pid: str) -> httpx.Response:
        nonlocal headers_in_flight
        headers_in_flight += 1
        if headers_in_flight >= 2:
            two_headers.set()
        await asyncio.wait_for(two_headers.wait(), 2)
        return httpx.Response(200, json=load_json("riigihanked", f"header_{pid}.json"))

    respx.post(SEARCH).mock(side_effect=search)
    _mock_details()
    respx.get(url__regex=rf"{re.escape(API)}/procurement/(?P<pid>\d+)/header-info").mock(side_effect=header)

    notices, checkpoints = await run_sync(Riigihanked, SINCE)

    assert len(notices) == 5 and len(checkpoints) == 2
    assert seen_days == {"2026-10-28", "2026-10-29"}
