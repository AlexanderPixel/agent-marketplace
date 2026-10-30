"""Find a Tender (UK) connector against recorded OCDS release-package pages."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from tenderhub.connectors import find_a_tender
from tenderhub.connectors.find_a_tender import API, BURST, BURST_WINDOW, FindATender, _slices
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import collect, load_json, make_connector

NOW = datetime(2026, 10, 30, 14, 13, 2, tzinfo=UTC)


async def sync_at(now: datetime, since: datetime, state: dict | None = None):
    conn, client = make_connector(FindATender)
    conn.ctx.now = lambda: now
    try:
        return await collect(conn.sync(since, state or {}))
    finally:
        await client.aclose()


class FakeClock:
    """Replaces the connector's wall clock and asyncio.sleep so budget waits are instant but recorded."""

    def __init__(self, monkeypatch, start: float = 1_800_000_000.0):
        self.t, self.waits = start, []
        monkeypatch.setattr(find_a_tender, "_wall", lambda: self.t)
        monkeypatch.setattr(find_a_tender.asyncio, "sleep", self.sleep)

    async def sleep(self, s):
        if s >= 1:                 # ignore the Http client's own millisecond spacing (rps=1000 in tests)
            self.waits.append(s)
        self.t += s


def qs(request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}


@respx.mock
async def test_sync_follows_cursor_links_and_maps_fields():
    page1, page2 = load_json("find_a_tender", "page1.json"), load_json("find_a_tender", "page2.json")
    next_url = page1["links"]["next"]
    # the cursor route is registered first: respx matches in order and the next URL also carries updatedFrom
    second = respx.get(next_url).mock(return_value=httpx.Response(200, json=page2))
    first = respx.get(API, params={"updatedFrom": "2026-10-30T06:00:00", "limit": "100"}).mock(
        return_value=httpx.Response(200, json=page1))

    # an 8 h window is a single, open-ended slice
    notices, checkpoints = await sync_at(NOW, NOW - timedelta(hours=8, minutes=13, seconds=2))

    assert first.call_count == 1 and second.call_count == 1   # page2 has no links.next -> stop
    assert "updatedTo" not in qs(first.calls[0].request)
    assert "cursor=" in str(second.calls[0].request.url)
    assert len(notices) == 7
    final = checkpoints[-1].state
    assert final["pages"] == 2 and final["slices"] == 1 and final["updated_from"] == "2026-10-30T06:00:00"
    assert len(final["recent_requests"]) == 2           # request history persisted for the next run
    assert [p for n in notices for p in validate_notice(n)] == []
    by_id = {n.source_id: n for n in notices}

    # Procurement Act 2023 tender notice (UK4) with CPVs on items and a participation deadline
    scaffold = by_id["103262-2026"]
    assert scaffold.source == "find_a_tender"
    assert scaffold.url == "https://www.find-tender.service.gov.uk/Notice/103262-2026"
    assert scaffold.title == "S1138 (2027) The Hire, Erection and Dismantling of Scaffold Services"
    assert scaffold.notice_type is NoticeType.TENDER and scaffold.status is Status.OPEN
    assert scaffold.buyer_name == "Northern Ireland Electricity Networks Limited"
    assert scaffold.buyer_id == "GB-COH-NI026041"
    assert scaffold.buyer_country == "GB" and scaffold.buyer_city == "Belfast"
    assert scaffold.cpv_codes[:2] == ["44212310", "45500000"] and len(scaffold.cpv_codes) == 8
    assert scaffold.published_at == datetime(2026, 10, 30, 14, 11, 39, tzinfo=UTC)
    assert scaffold.deadline_at == datetime(2026, 12, 3, 12, 0, tzinfo=UTC)
    assert scaffold.value_amount == 4_800_000 and scaffold.value_currency == "GBP"
    assert scaffold.procedure_type == "Competitive flexible procedure"
    assert scaffold.legal_basis == "2023/54"
    assert scaffold.platform_url == "https://e-sourcingni.bravosolution.co.uk"
    assert scaffold.language == "en"
    assert scaffold.refs == {"ocid": "ocds-h6vhtk-06727d", "notice": "103262-2026", "procedure": "S1138 (2027)"}

    # legacy (PCR 2015 / CELEX) tender: CPV in tender.classification, deadline in tenderPeriod
    laser = by_id["103248-2026"]
    assert laser.notice_type is NoticeType.TENDER
    assert laser.cpv_codes == ["38000000"]
    assert laser.deadline_at == datetime(2026, 11, 30, 12, 0, tzinfo=UTC)
    assert laser.buyer_name == "University of Glasgow" and laser.buyer_country == "GB"
    assert laser.legal_basis == "32014L0024"
    assert laser.platform_url == "https://www.publiccontractsscotland.gov.uk/"

    # award: value falls back to the contract value; country from countryName
    award = by_id["103258-2026"]
    assert award.notice_type is NoticeType.AWARD and award.status is Status.AWARDED
    assert award.value_amount == 148_500 and award.value_currency == "GBP"
    assert award.buyer_name == "Salford City Council" and award.buyer_country == "GB"
    assert award.deadline_at is None

    # cancellation without a top-level `buyer`: buyer taken from parties[roles=buyer]
    cancelled = by_id["103235-2026"]
    assert cancelled.notice_type is NoticeType.CANCELLATION and cancelled.status is Status.CANCELLED
    assert cancelled.buyer_name == "Suffolk County Council"
    assert "procedure" not in cancelled.refs  # tender.id equals the ocid here

    update = by_id["103222-2026"]
    assert update.notice_type is NoticeType.MODIFICATION
    assert update.deadline_at == datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    assert update.platform_url.startswith("https://procontract.due-north.com/")

    # DE&S pipeline notice: planning, no deadline, defence buyer
    des = by_id["103157-2026"]
    assert des.notice_type is NoticeType.PLAN
    assert des.buyer_name == "Defence Equipment and Support" and des.buyer_city == "Bristol"
    assert des.cpv_codes == ["50842000"]
    assert des.deadline_at is None
    assert des.value_amount == 1_200_000
    assert des.raw["id"] == "103157-2026"


@respx.mock
async def test_window_is_split_into_concurrent_slices():
    seen: list[dict[str, str]] = []

    def answer(request):
        q = qs(request)
        seen.append(q)
        rid = f"{len(seen)}-2026"
        return httpx.Response(200, json={"releases": [{"id": rid, "ocid": f"ocds-x-{rid}", "date": q["updatedFrom"] + "Z",
                                                       "tag": ["tender"], "tender": {"title": f"T {rid}"}}]})

    respx.get(API).mock(side_effect=answer)
    since = NOW - timedelta(days=3)
    notices, checkpoints = await sync_at(NOW, since)

    assert len(seen) == 4 and len(notices) == 4                # 72 h -> 4 slices of 18 h, one request each
    assert checkpoints[-1].state["slices"] == 4 and checkpoints[-1].state["pages"] == 4
    starts = sorted(q["updatedFrom"] for q in seen)
    assert starts[0] == "2026-10-27T14:13:02" and starts[-1] == "2026-10-29T20:13:02"
    open_ended = [q for q in seen if "updatedTo" not in q]
    assert len(open_ended) == 1 and open_ended[0]["updatedFrom"] == starts[-1]
    # contiguous coverage: every bounded slice ends (1 s past) where the next one starts
    bounded = sorted((q["updatedFrom"], q["updatedTo"]) for q in seen if "updatedTo" in q)
    for (_, hi), nxt in zip(bounded, starts[1:]):
        assert (datetime.fromisoformat(hi) - datetime.fromisoformat(nxt)) == timedelta(seconds=1)
    assert all(q["limit"] == "100" for q in seen)


def test_slices_are_capped_and_newest_first():
    assert _slices(NOW - timedelta(hours=2), NOW) == [(NOW - timedelta(hours=2), None)]
    many = _slices(NOW - timedelta(days=30), NOW)
    assert len(many) == 4 and many[0][1] is None and many[-1][0] == NOW - timedelta(days=30)


@respx.mock
async def test_budget_from_previous_run_is_respected(monkeypatch):
    clock = FakeClock(monkeypatch)
    route = respx.get(API).mock(return_value=httpx.Response(200, json={"releases": []}))
    # the previous run (another process) used the whole burst 25 s ago
    state = {"recent_requests": [clock.t - 25] * BURST}
    notices, checkpoints = await sync_at(NOW, NOW - timedelta(hours=1), state)
    assert route.call_count == 1 and notices == []
    assert len(clock.waits) == 1 and clock.waits[0] == pytest.approx(BURST_WINDOW - 25, abs=0.1)


@respx.mock
async def test_burst_is_spent_at_full_speed_then_paced(monkeypatch):
    clock = FakeClock(monkeypatch)
    pages = BURST + 2

    def answer(request):
        n = int(qs(request).get("cursor") or 0)
        nxt = f"{API}?updatedFrom=x&limit=100&cursor={n + 1}" if n + 1 < pages else None
        return httpx.Response(200, json={"releases": [{"id": f"{n}-2026", "tender": {"title": "t"}}],
                                         "links": {"next": nxt} if nxt else {}})

    respx.get(url__startswith=API).mock(side_effect=answer)
    notices, _ = await sync_at(NOW, NOW - timedelta(hours=1))
    assert len(notices) == pages
    # the first BURST requests go out back to back; then one window wait frees the whole burst again
    assert len(clock.waits) == 1
    assert clock.waits[0] == pytest.approx(BURST_WINDOW, abs=0.1)


@respx.mock
async def test_empty_result_still_checkpoints():
    respx.get(API).mock(return_value=httpx.Response(200, json={"releases": [], "uri": API}))
    notices, checkpoints = await sync_at(NOW, NOW - timedelta(hours=3))
    assert notices == [] and len(checkpoints) == 2   # one per page, plus the final one


@respx.mock
async def test_rate_limit_body_is_honoured(monkeypatch):
    clock = FakeClock(monkeypatch)
    limited = httpx.Response(429, text="Rate limit of 12 exceeded. Please retry after 120 seconds.")
    # the shared Http client retries once itself (test config), then the connector waits as told
    route = respx.get(API).mock(side_effect=[limited, limited, httpx.Response(200, json={"releases": []})])
    notices, checkpoints = await sync_at(NOW, NOW - timedelta(hours=3))
    assert route.call_count == 3 and notices == [] and checkpoints
    assert any(w == pytest.approx(122, abs=0.1) for w in clock.waits)


@respx.mock
async def test_error_in_one_slice_propagates_and_cancels_the_rest():
    respx.get(API).mock(return_value=httpx.Response(404, text="nope"))
    with pytest.raises(find_a_tender.HttpError):
        await sync_at(NOW, NOW - timedelta(days=2))
