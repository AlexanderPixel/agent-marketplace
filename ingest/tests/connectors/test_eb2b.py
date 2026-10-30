"""eB2B connector against recorded responses of platforma.eb2b.com.pl (global open-auctions list JSON and
the public per-notice attachment lists)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import respx

from tenderhub.connector import StoredRef
from tenderhub.connectors.eb2b import ATTACHMENTS_API, LIST_API, PAGE_SIZE, Eb2b
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import collect, load_json, make_connector, run_sync

WARSAW = ZoneInfo("Europe/Warsaw")
SINCE = datetime(2026, 10, 30, 12, 30, tzinfo=UTC)  # 13:30 Warsaw: page 2 ends with a row published at 13:00
IDS = ("536486", "536483", "536424", "536470")


def _mock_source() -> tuple[respx.Route, dict[str, respx.Route]]:
    pages = {"1": load_json("eb2b", "list_page1.json"), "2": load_json("eb2b", "list_page2.json")}
    listing = respx.get(LIST_API).mock(
        side_effect=lambda req: httpx.Response(200, json=pages[req.url.params["page"]]))
    attachments = {i: respx.get(ATTACHMENTS_API, params={"id": i}).mock(return_value=httpx.Response(
        200, json=load_json("eb2b", f"attachments_{i}.json"))) for i in IDS}
    return listing, attachments


@respx.mock
async def test_sync_walks_newest_first_and_maps_fields():
    listing, attachments = _mock_source()

    notices, checkpoints = await run_sync(Eb2b, SINCE)

    # two list pages; the walk stops at the first row published before `since` (536343, 13:00 Warsaw)
    assert listing.call_count == 2
    first, second = (c.request.url.params for c in listing.calls)
    assert (first["page"], first["start"], first["limit"]) == ("1", "0", str(PAGE_SIZE))
    assert (second["page"], second["start"]) == ("2", str(PAGE_SIZE))
    assert json.loads(first["sort"])[0] == {"property": "publication_date", "direction": "DESC"}
    assert all(r.call_count == 1 for r in attachments.values())
    assert [n.source_id for n in notices] == list(IDS)
    assert [p for n in notices for p in validate_notice(n)] == []
    assert len(checkpoints) == 2
    assert checkpoints[-1].state["seen"] == {"536486": "2026-10-30 14:54:26", "536483": "2026-10-30 14:35:08",
                                             "536424": "2026-10-30 14:14:04", "536470": "2026-10-30 14:30:29"}

    by_id = {n.source_id: n for n in notices}
    rfq = by_id["536486"]  # POLREGIO request for price information, outside the PZP act, in its RFI stage
    assert rfq.source == "eb2b"
    assert rfq.url == ("https://platforma.eb2b.com.pl/open-preview-auction.html/536486/zapytanie-o-informacje-cenowa-"
                       "na-wykonanie-uslugi-zastepcza-komunikacja-autobusowa-na-odcinku-skarzysko-kamienna-kielce-"
                       "glowne-w-terminie-13-12-2026-r-13-03-2027-r")
    assert rfq.platform_url == rfq.url
    assert rfq.title.startswith("Zapytanie o informację cenową na wykonanie usługi „Zastępcza komunikacja")
    assert "  " not in rfq.title
    assert rfq.notice_type is NoticeType.TENDER and rfq.status is Status.UNKNOWN
    assert (rfq.buyer_name, rfq.buyer_country) == ("POLREGIO S.A.", "PL")
    assert rfq.procedure_type == "Postępowanie poza ustawą Pzp"
    assert rfq.published_at == datetime(2026, 10, 30, 15, 10, tzinfo=WARSAW)
    assert rfq.deadline_at == datetime(2026, 11, 6, 9, 0, tzinfo=WARSAW)
    assert rfq.refs == {"procedure": "PRT", "evidence": "AU-018009"}
    assert rfq.language == "pl"
    assert [d.url for d in rfq.documents] == [
        "https://platforma.eb2b.com.pl/auction/auction/download-attachment/id/5285359",
        "https://platforma.eb2b.com.pl/auction/auction/download-attachment/id/5285358",
    ]
    assert rfq.documents[0].title == "Oświadczenie_o_wykluczeniu_z_zamówienia"
    assert rfq.documents[0].kind == "Załączniki organizatora"
    assert rfq.raw["region_name"] == "ŚWIĘTOKRZYSKIE"
    assert rfq.raw["departments"] == ["Świętokrzyski Zakład w Kielcach"]
    assert "username" not in rfq.raw

    pzp = by_id["536483"]  # hospital tender under the PZP basic procedure
    assert pzp.buyer_name == "WOJEWÓDZKI SZPITAL SPECJALISTYCZNY IM. STEFANA KARDYNAŁA WYSZYŃSKIEGO SP ZOZ"
    assert pzp.procedure_type == "Tryb podstawowy – bez negocjacji (supplies)"
    assert pzp.deadline_at == datetime(2026, 11, 9, 10, 0, tzinfo=WARSAW)
    assert pzp.refs["procedure"] == "WSS SP ZOZ DZP 382.72.2026"
    assert len(pzp.documents) == 2

    police = by_id["536424"]
    assert police.buyer_name == "KOMENDA WOJEWÓDZKA POLICJI W KATOWICACH"
    assert police.title.startswith("Zakup i dostawa materiałów eksploatacyjnych do drukarki 3D")
    assert police.documents == []

    reflectometer = by_id["536470"]  # participation deadline left two months after the offer stage ends
    assert reflectometer.buyer_name == "PKP TELKOL SP. Z O.O."
    assert reflectometer.raw["participation_requests_deadline"] == "2026-12-30 12:00:00"
    assert reflectometer.deadline_at == datetime(2026, 11, 2, 14, 0, tzinfo=WARSAW)
    assert reflectometer.published_at == datetime(2026, 10, 30, 13, 56, tzinfo=WARSAW)


@respx.mock
async def test_second_run_skips_unchanged_rows():
    listing, attachments = _mock_source()
    _, checkpoints = await run_sync(Eb2b, SINCE)

    notices, again = await run_sync(Eb2b, SINCE, state=checkpoints[-1].state)

    assert notices == []
    assert listing.call_count == 4
    assert all(r.call_count == 1 for r in attachments.values())  # nothing re-fetched
    assert again[-1].state == checkpoints[-1].state


@respx.mock
async def test_changed_row_is_reyielded_and_failed_attachments_retried():
    _, attachments = _mock_source()
    attachments["536483"].mock(return_value=httpx.Response(200, json={"success": False, "msg": "x"}))
    state = {"seen": {"536486": "2026-10-30 14:54:26", "536483": "2026-10-30 09:00:00",
                      "536424": "2026-10-30 10:00:00", "536470": "2026-10-30 14:30:29", "1": "gone"}}

    notices, checkpoints = await run_sync(Eb2b, SINCE, state=state)

    assert [n.source_id for n in notices] == ["536483", "536424"]
    assert notices[0].documents == []
    seen = checkpoints[-1].state["seen"]
    assert "536483" not in seen  # attachment list failed: fetch again next run
    assert seen["536424"] == "2026-10-30 14:14:04" and "1" not in seen


@respx.mock
async def test_refresh_rereads_notice_by_id_filter():
    _mock_source()
    by_id = respx.get(LIST_API).mock(side_effect=lambda req: httpx.Response(200, json=(
        load_json("eb2b", "list_by_id_536424.json") if "536424" in req.url.params["filter"]
        else {"success": True, "total": 0, "data": []})))
    respx.get(ATTACHMENTS_API, params={"id": "1"}).mock(
        return_value=httpx.Response(200, json={"success": True, "data": []}))
    conn, client = make_connector(Eb2b)
    try:
        n = await conn.refresh(StoredRef(source_id="536424", url="https://x", refs={}, raw={}))
        gone = await conn.refresh(StoredRef(source_id="1", url="https://x", refs={}, raw={}))
    finally:
        await client.aclose()
    assert json.loads(by_id.calls[0].request.url.params["filter"]) == [{"property": "id", "value": 536424}]
    assert n is not None and n.source_id == "536424" and validate_notice(n) == []
    assert n.deadline_at == datetime(2026, 11, 6, 12, 0, tzinfo=WARSAW)
    assert gone is None


@respx.mock
async def test_empty_list_still_checkpoints():
    respx.get(LIST_API).mock(return_value=httpx.Response(200, json={"success": True, "total": 0, "data": []}))
    conn, client = make_connector(Eb2b)
    try:
        notices, checkpoints = await collect(conn.sync(SINCE, {}))
    finally:
        await client.aclose()
    assert notices == [] and len(checkpoints) == 1


def test_status_and_bzp_mapping():
    row = load_json("eb2b", "list_page1.json")["data"][1]
    conn = Eb2b.__new__(Eb2b)  # to_notice is pure
    cancelled = conn.to_notice({**row, "status_code": 9, "bzp_links": [
        {"type": 1, "bzp_number": "2026/BZP 00512345/01"}]}, [])
    assert cancelled.status is Status.CANCELLED
    assert cancelled.refs["bzp"] == "2026/BZP 00512345/01"
    rfi = conn.to_notice({**row, "public_order_mode": 10}, [])
    assert rfi.notice_type is NoticeType.PLAN


@respx.mock
async def test_attachment_lists_and_next_page_are_fetched_concurrently():
    pages = {"1": load_json("eb2b", "list_page1.json"), "2": load_json("eb2b", "list_page2.json")}
    in_flight = peak = 0
    order: list[str] = []

    async def slow(req: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        order.append(req.url.params.get("page") or f"att:{req.url.params['id']}")
        await asyncio.sleep(0.05)
        in_flight -= 1
        if req.url.path.endswith("/list"):
            return httpx.Response(200, json=pages[req.url.params["page"]])
        return httpx.Response(200, json=load_json("eb2b", f"attachments_{req.url.params['id']}.json"))

    respx.get(LIST_API).mock(side_effect=slow)
    respx.get(ATTACHMENTS_API).mock(side_effect=slow)

    notices, _ = await run_sync(Eb2b, SINCE)

    assert [n.source_id for n in notices] == list(IDS)  # order preserved despite concurrency
    assert peak >= 3  # page 2 prefetch overlaps page 1's attachment lists
    assert order.index("2") < order.index("att:536483")
