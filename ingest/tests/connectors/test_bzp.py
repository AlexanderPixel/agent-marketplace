"""BZP (e-Zamówienia) connector against recorded mo-board API records, served by a fake that mimics the API.

The fake reproduces the behaviour measured on the live API (2026-10-30): `PublicationDateFrom` is exact,
`PublicationDateTo` is applied one day late (`publicationDate < To + 1 day`), results are ordered by objectId
with a `SearchAfter=<objectId>` cursor, and Board/Search pages plan versions newest-first by `PageNumber`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from dateutil.parser import isoparse

from tenderhub.connector import Context, StoredRef
from tenderhub.connectors import bzp
from tenderhub.connectors.bzp import API, NOTICE_TYPES, PLAN_API, Bzp, html_fields
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_json, make_connector, run_sync

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
SINCE = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)

FILES = {
    "ContractNotice": ["contract_p1.json", "contract_p2.json"],
    "TenderResultNotice": ["result.json"],
    "NoticeUpdateNotice": ["update.json"],
    "AgreementIntentionNotice": ["intention.json"],
    "SmallContractNotice": ["small.json"],
    "TenderPlanNotice": ["plan_p1.json", "plan_p2.json"],
}


def _pool(notice_type: str) -> list[dict]:
    return [x for f in FILES.get(notice_type, []) for x in load_json("bzp", f)]


def _pub(x: dict) -> datetime:
    return isoparse(x["publicationDate"])


class FakeBoard:
    """Serves the recorded records the way the live API does; records every request and the peak concurrency."""

    def __init__(self, delay: float = 0.0):
        self.calls: list[dict[str, str]] = []
        self.plan_calls: list[dict[str, str]] = []
        self.delay = delay
        self.in_flight = self.peak = 0

    async def _track(self):
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(self.delay)
        self.in_flight -= 1

    async def notice(self, request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        self.calls.append(p)
        await self._track()
        if p["NoticeType"] == "TenderPlanNotice":
            return httpx.Response(400, json={"errors": {"NoticeType": ["out of range"]}})
        lo = datetime.fromisoformat(p["PublicationDateFrom"]).replace(tzinfo=UTC)
        hi = datetime.fromisoformat(p["PublicationDateTo"]).replace(tzinfo=UTC) + timedelta(days=1)
        items = sorted((x for x in _pool(p["NoticeType"]) if lo <= _pub(x) < hi), key=lambda x: x["objectId"])
        if "NoticeNumber" in p:
            items = [x for x in items if x["noticeNumber"] == p["NoticeNumber"]]
        if "SearchAfter" in p:
            items = [x for x in items if x["objectId"] > p["SearchAfter"]]
        return httpx.Response(200, json=items[: int(p["PageSize"])])

    async def board(self, request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        self.plan_calls.append(p)
        await self._track()
        assert (p["SortingColumnName"], p["SortingDirection"]) == ("PublicationDate", "DESC")
        items = sorted(_pool(p["NoticeType"]), key=_pub, reverse=True)
        size, page = min(int(p["PageSize"]), 10), int(p["PageNumber"])
        return httpx.Response(200, json=items[(page - 1) * size: page * size])


@pytest.fixture
def board(monkeypatch) -> FakeBoard:
    monkeypatch.setattr(Context, "now", lambda self: NOW)
    fake = FakeBoard()
    respx.get(API).mock(side_effect=fake.notice)
    respx.get(PLAN_API).mock(side_effect=fake.board)
    return fake


@respx.mock
async def test_sync_maps_every_type(board):
    notices, checkpoints = await run_sync(Bzp, SINCE)

    # every type probed once, in one small page; plans in one Board/Search page
    assert len(board.calls) == len(NOTICE_TYPES) and {c["NoticeType"] for c in board.calls} == set(NOTICE_TYPES)
    first = board.calls[0]
    assert first["PublicationDateFrom"] == "2026-10-21T00:00:00" and first["PageSize"] == str(bzp.PROBE_SIZE)
    assert first["PublicationDateTo"] == "2026-10-29T12:00:00"  # open-ended: now + 1 day
    assert [c["PageNumber"] for c in board.plan_calls] == ["1"]
    assert len(checkpoints) == len(NOTICE_TYPES) + 2  # one per type, plans, and the final one

    assert len(notices) == 11
    assert [p for n in notices for p in validate_notice(n)] == []
    assert len({n.source_id for n in notices}) == 11
    by_id = {n.source_id: n for n in notices}

    boiler = by_id["2026/BZP 00505976/01"]  # school boiler replacement, deadline extended after publication
    assert boiler.source == "bzp"
    assert boiler.url == "https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/id/08df3489-b6bf-c8e5-75d7-fc0001a43a42"
    assert boiler.title == "Wymiana kotła gazowego"
    assert boiler.notice_type is NoticeType.TENDER
    assert boiler.buyer_name.startswith("ZESPÓŁ SZKÓŁ CENTRUM KSZTAŁCENIA ROLNICZEGO")
    assert boiler.buyer_id == "9181039877"
    assert boiler.buyer_country == "PL" and boiler.buyer_city == "Tarnogród"
    assert boiler.cpv_codes == ["45331110", "45333000", "45331100"]
    assert boiler.published_at == datetime(2026, 10, 28, 0, 23, 39, 89578, tzinfo=UTC)
    assert boiler.deadline_at == datetime(2026, 11, 16, 9, 0, tzinfo=UTC)  # the API's current deadline, not the HTML's
    assert boiler.status is Status.OPEN
    assert boiler.procedure_type == "podstawowym na podstawie: art. 275 pkt 1 ustawy"
    assert boiler.platform_url == ("https://ezamowienia.gov.pl/mp-client/search/list/"
                                   "ocds-148610-a67e6574-01a7-43a5-8367-6c420eee18da")
    assert boiler.description.startswith("Przedmiotem zamówienia jest realizacja inwestycji")
    assert boiler.language == "pl"
    assert boiler.refs == {"bzp": "2026/BZP 00505976", "notice": "2026/BZP 00505976/01",
                           "ocid": "ocds-148610-a67e6574-01a7-43a5-8367-6c420eee18da",
                           "bzp_object_id": "08df3489-b6bf-c8e5-75d7-fc0001a43a42",
                           "bzp_plan": "2026/BZP 00059206/04/P"}
    assert "htmlBody" not in boiler.raw and boiler.raw["orderType"] == "Works"

    food = by_id["2026/BZP 00505988/01"]  # multi-lot: CPV descriptions contain commas
    assert food.cpv_codes[0] == "15000000" and "03142500" in food.cpv_codes
    assert food.refs["bzp_plan"] == "2026/BZP 00049050/02/P"  # links the tender to its plan position
    assert by_id["2026/BZP 00505979/01"].title.startswith("Zakup samochodu")

    result = by_id["2026/BZP 00499874/01"]
    assert result.notice_type is NoticeType.AWARD and result.status is Status.AWARDED
    assert result.value_amount == 223153.87 and result.value_currency == "PLN"
    assert result.platform_url == "https://platformazakupowa.pl/transakcja/1256353"
    assert result.deadline_at is None
    assert result.raw["contractors"][0]["contractorName"] == "PIEKARNIA TOMASZ NOSEK sp. z o.o."
    assert all(any(c.values()) for c in result.raw["contractors"])  # empty lot rows dropped

    cancelled = by_id["2026/BZP 00499903/01"]  # every lot ended in "uniewaznienie"
    assert cancelled.notice_type is NoticeType.CANCELLATION and cancelled.status is Status.CANCELLED

    update = by_id["2026/BZP 00499873/01"]
    assert update.notice_type is NoticeType.MODIFICATION
    assert update.refs["bzp_changed"] == "2026/BZP 00489499"
    assert "Po zmianie: 2026-10-26 10:00" in update.description

    intention = by_id["2026/BZP 00499956/01"]
    assert intention.notice_type is NoticeType.AWARD
    assert intention.value_amount == 235179.64
    assert intention.procedure_type.startswith("zamówienia z wolnej ręki")

    small = by_id["2026/BZP 00500201/01"]
    assert small.notice_type is NoticeType.AWARD and small.buyer_name == "Powiat Suski"
    assert "ocid" not in small.refs  # tenderId is null for small contracts

    plan = by_id["2025/BZP 00628272/35/P"]  # 35th version of a district's tender plan
    assert plan.notice_type is NoticeType.PLAN and plan.status is Status.UNKNOWN
    assert plan.url == "https://ezamowienia.gov.pl/mo-client-board/bzp/tender-details/2025%2FBZP%2000628272%2F35%2FP"
    assert plan.title == "Plan postępowań: Miasto Stołeczne Warszawa Dzielnica Mokotów"
    assert plan.buyer_city == "Warszawa" and plan.buyer_id == "01525964000059" and plan.buyer_country == "PL"
    assert plan.published_at == datetime(2026, 10, 28, 10, 28, 17, 546996, tzinfo=UTC)
    assert plan.refs["bzp"] == "2025/BZP 00628272" and plan.cpv_codes == [] and plan.deadline_at is None
    assert {"2025/BZP 00478963/11/P", "2025/BZP 00610043/06/P"} <= by_id.keys()


@respx.mock
async def test_full_probe_fans_out_into_parallel_time_slices(board, monkeypatch):
    # a probe of 1 item is "full": ContractNotice's 3 records (00:23, 04:42, 05:17 on 10-28) are then read by
    # the probe's cursor plus parallel slices of the rest of the window, each bounded with the API's To quirk
    monkeypatch.setattr(bzp, "PROBE_SIZE", 1)
    monkeypatch.setattr(bzp, "PAGE_SIZE", 1)
    monkeypatch.setattr(bzp, "SLICE_TARGET", 1)
    monkeypatch.setattr(bzp, "MAX_SLICES", 4)
    board.delay = 0.01
    notices, _ = await run_sync(Bzp, datetime(2026, 10, 28, 0, 0, tzinfo=UTC))

    contract = [c for c in board.calls if c["NoticeType"] == "ContractNotice"]
    probe, rest = contract[0], contract[1:]
    assert probe["PublicationDateFrom"] == "2026-10-28T00:00:00" and "SearchAfter" not in probe
    starts = sorted({c["PublicationDateFrom"] for c in rest if "SearchAfter" not in c})
    # rate 1 per 23.6 min over the probe, ~11.6 h left -> ~30 slices, capped at 4 of ~2.9 h from 00:23:39
    assert starts == ["2026-10-28T03:17:44", "2026-10-28T06:11:49", "2026-10-28T09:05:54"]
    bounded = [c for c in rest if c["PublicationDateFrom"] == "2026-10-28T03:17:44"]
    assert bounded[0]["PublicationDateTo"] == "2026-10-27T06:11:49"  # slice end minus the API's extra day
    assert any(c.get("SearchAfter") == "08df3489-b6bf-c8e5-75d7-fc0001a43a42" for c in rest)  # cursor continues
    assert board.peak > 1  # requests really overlap

    got = sorted(n.source_id for n in notices if n.notice_type is NoticeType.TENDER)
    assert got == ["2026/BZP 00505976/01", "2026/BZP 00505979/01", "2026/BZP 00505988/01"]  # each exactly once


@respx.mock
async def test_slice_clips_items_past_its_end_even_if_the_server_returns_them(monkeypatch):
    # if the API ever applied To exactly-or-later, a bounded slice must still not leak into the next one
    monkeypatch.setattr(Context, "now", lambda self: NOW)
    rows = load_json("bzp", "contract_p1.json") + load_json("bzp", "contract_p2.json")
    respx.get(API).mock(return_value=httpx.Response(200, json=sorted(rows, key=lambda x: x["objectId"])))
    conn, client = make_connector(Bzp)
    queue: asyncio.Queue = asyncio.Queue()
    try:
        await conn._chain("ContractNotice", datetime(2026, 10, 28, tzinfo=UTC),
                          datetime(2026, 10, 28, 5, tzinfo=UTC), NOW, None, queue)
    finally:
        await client.aclose()
    assert [n.source_id for n in queue.get_nowait()] == ["2026/BZP 00505976/01", "2026/BZP 00505979/01"]


@respx.mock
async def test_plans_fetch_estimated_pages_at_once_and_stop_at_since(board, monkeypatch):
    monkeypatch.setattr(bzp, "PLAN_PAGE_SIZE", 1)
    notices, _ = await run_sync(Bzp, datetime(2026, 10, 28, 9, 0, tzinfo=UTC))
    plans = [n.source_id for n in notices if n.notice_type is NoticeType.PLAN]
    assert plans == ["2025/BZP 00628272/35/P", "2025/BZP 00478963/11/P"]  # the 08:26 version is older than since
    assert sorted(int(c["PageNumber"]) for c in board.plan_calls)[:3] == [1, 2, 3]
    assert all(c["PageSize"] == "1" for c in board.plan_calls)


@respx.mock
async def test_empty_source_still_checkpoints_every_type(monkeypatch):
    respx.get(API).mock(return_value=httpx.Response(200, json=[]))
    respx.get(PLAN_API).mock(return_value=httpx.Response(200, json=[]))
    notices, checkpoints = await run_sync(Bzp, SINCE)
    assert notices == [] and len(checkpoints) == len(NOTICE_TYPES) + 2


@respx.mock
async def test_backs_off_when_the_waf_blocks(monkeypatch):
    monkeypatch.setattr(bzp, "WAF_BACKOFF", 0.01)
    monkeypatch.setattr(bzp, "WAF_PAUSE", 0.001)
    blocked = httpx.Response(403, text="<title>Dostęp zablokowany / Access Blocked</title> Web Application Firewall (WAF)")
    route = respx.get(API).mock(side_effect=[blocked] + [httpx.Response(200, json=[])] * len(NOTICE_TYPES))
    respx.get(PLAN_API).mock(return_value=httpx.Response(200, json=[]))
    notices, checkpoints = await run_sync(Bzp, SINCE)
    assert route.call_count == len(NOTICE_TYPES) + 1 and len(checkpoints) == len(NOTICE_TYPES) + 2


@respx.mock
async def test_bad_request_is_raised_not_swallowed():
    respx.get(API).mock(return_value=httpx.Response(400, json={"errors": {"NoticeType": ["nope"]}}))
    respx.get(PLAN_API).mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(Exception, match="HTTP 400"):
        await run_sync(Bzp, SINCE)


@respx.mock
async def test_early_stop_cancels_the_parallel_fetches(board):
    board.delay = 0.05
    conn, client = make_connector(Bzp)
    try:
        gen = conn.sync(SINCE, {})
        first = await anext(gen)
        await gen.aclose()
    finally:
        await client.aclose()
    assert first.source == "bzp"


@respx.mock
async def test_refresh_looks_up_one_notice_by_number(board):
    conn, client = make_connector(Bzp)
    try:
        ref = StoredRef(source_id="2026/BZP 00505976/01", url="", refs={},
                        raw={"noticeType": "ContractNotice", "publicationDate": "2026-10-28T00:23:39.0895785Z"})
        n = await conn.refresh(ref)
        missing = await conn.refresh(StoredRef("2026/BZP 00000001/01", "", {}, {"noticeType": "ContractNotice"}))
    finally:
        await client.aclose()
    params = board.calls[0]
    assert params["NoticeNumber"] == "2026/BZP 00505976/01" and params["NoticeType"] == "ContractNotice"
    assert params["PublicationDateFrom"] == "2026-10-27T00:23:39"
    assert n.deadline_at == datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
    assert missing is None
    assert len(board.calls) == 2


async def test_refresh_skips_immutable_plan_versions():
    conn, client = make_connector(Bzp)
    try:
        assert await conn.refresh(StoredRef("2025/BZP 00628272/35/P", "", {}, {"noticeType": "TenderPlanNotice"})) is None
    finally:
        await client.aclose()


def test_html_fields_splits_numbered_headings_and_stops_at_sections():
    html = load_json("bzp", "contract_p1.json")[0]["htmlBody"]
    fields = dict(html_fields(html))
    assert fields["Identyfikator postępowania"] == "ocds-148610-a67e6574-01a7-43a5-8367-6c420eee18da"
    assert fields["Termin składania ofert"] == "2026-11-13 10:00"
    assert "SEKCJA" not in fields["Tryb udzielenia zamówienia wraz z podstawą prawną"]
