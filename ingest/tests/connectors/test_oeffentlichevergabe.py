"""Bekanntmachungsservice connector against recorded (trimmed) daily eForms export zips."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import httpx
import respx

from tenderhub.connector import StoredRef
from tenderhub.connectors.oeffentlichevergabe import EXPORT, NOTICE_API, Oeffentlichevergabe
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import collect, fixture_path, make_connector

SINCE = datetime(2026, 10, 27, 23, 30, tzinfo=UTC)   # = 2026-10-28 00:30 in Berlin
NOW = datetime(2026, 10, 30, 14, 0, tzinfo=UTC)       # today (10-30) has no export yet -> last day is 10-29
CET = timezone(timedelta(hours=1))


def _zip(day: str) -> bytes:
    return fixture_path("oeffentlichevergabe", f"notices_{day}_EFORMS.zip").read_bytes()


async def _sync(state: dict | None = None, since: datetime = SINCE):
    conn, client = make_connector(Oeffentlichevergabe)
    conn.ctx.now = lambda: NOW
    try:
        return await collect(conn.sync(since, state or {}))
    finally:
        await client.aclose()


def _day_routes(responses: dict[str, httpx.Response]) -> respx.Route:
    """One export route answering per `pubDay` (requests run concurrently, so order is not fixed)."""
    return respx.get(EXPORT).mock(side_effect=lambda req: responses[req.url.params["pubDay"]])


@respx.mock
async def test_sync_walks_days_until_yesterday_and_maps_fields():
    route = _day_routes({
        "2026-10-28": httpx.Response(200, content=_zip("2026-10-28"), headers={"etag": '"version-6"'}),
        "2026-10-29": httpx.Response(200, content=_zip("2026-10-29"), headers={"etag": '"version-0"'}),
    })

    notices, checkpoints = await _sync()

    # one request per publication day, from `since` (Berlin date) up to yesterday; never today
    assert route.call_count == 2
    assert sorted(c.request.url.params["pubDay"] for c in route.calls) == ["2026-10-28", "2026-10-29"]
    assert {c.request.url.params["format"] for c in route.calls} == {"eforms.zip"}
    assert len(notices) == 8 and len(checkpoints) == 2
    # days are fetched concurrently but yielded strictly in day order, each followed by its checkpoint
    assert [n.raw["pub_day"] for n in notices] == ["2026-10-28"] * 6 + ["2026-10-29"] * 2
    assert [c.state["last_day"] for c in checkpoints] == ["2026-10-28", "2026-10-29"]
    assert checkpoints[-1].state == {"last_day": "2026-10-29",
                                     "etags": {"2026-10-28": '"version-6"', "2026-10-29": '"version-0"'}}
    assert [p for n in notices for p in validate_notice(n)] == []
    assert {n.buyer_country for n in notices} == {"DE"}

    by_id = {n.source_id: n for n in notices}

    # defence-directive competition notice from a Bundeswehr service centre
    guard = by_id["c5b95c53-2c8d-43e1-8d13-c1b0557e7c8d"]
    assert guard.source == "oeffentlichevergabe"
    assert guard.url == ("https://oeffentlichevergabe.de/ui/de/search/details"
                         "?noticeId=c5b95c53-2c8d-43e1-8d13-c1b0557e7c8d&noticeVersion=01")
    assert guard.notice_type is NoticeType.TENDER
    assert guard.title.startswith("Abschluss eines Vertrages über die konventionell gewerbliche Bewachung")
    assert guard.buyer_name == "Bundeswehr-Dienstleistungszentrum Rotenburg (Wümme)"
    assert guard.buyer_city == "Rotenburg/Wümme"
    assert guard.buyer_id == "991-14142-17"
    assert guard.legal_basis == "32009L0081"
    assert guard.procedure_type == "neg-w-call"
    assert guard.cpv_codes == ["79713000"]
    assert guard.language == "de"
    assert guard.published_at == datetime(2026, 10, 28, 3, 26, 4, tzinfo=UTC)   # zip entry time, CET
    assert guard.deadline_at == datetime(2026, 12, 3, 13, 0, tzinfo=CET)
    assert guard.platform_url == "https://www.evergabe-online.de/tenderdetails.html?id=902400"
    assert [d.url for d in guard.documents] == ["https://www.evergabe-online.de/tenderdetails.html?id=902400"]
    assert guard.refs == {"notice": "c5b95c53-2c8d-43e1-8d13-c1b0557e7c8d", "notice_version": "01",
                          "procedure": "25650f5e-8427-4b2d-9341-347dcc9506d5",
                          "ocid": "ocds-mnwr74-25650f5e-8427-4b2d-9341-347dcc9506d5"}

    # competition with estimated value, several CPVs (procedure + lot, de-duplicated)
    rostock = by_id["7e506ec5-e5b6-4718-b504-2054e6865724"]
    assert rostock.title == "Planung Kältetechnik TGA-Planung - Sanierung der Eissporthalle Rostock"
    assert rostock.cpv_codes == ["71200000", "45331231", "71321000", "71221000"]
    assert (rostock.value_amount, rostock.value_currency) == (200000.0, "EUR")
    assert rostock.deadline_at == datetime(2026, 11, 30, 11, 0, tzinfo=CET)
    assert rostock.status is Status.OPEN

    # award: awarded total from the notice result
    peine = by_id["0a81f63c-6987-43fd-9cd0-3e7752b8424d"]
    assert peine.notice_type is NoticeType.AWARD and peine.status is Status.AWARDED
    assert (peine.value_amount, peine.value_currency) == (2609489.0, "EUR")
    assert peine.deadline_at is None and peine.buyer_name == "Landkreis Peine"

    # contract modification; placeholder buyer id 'XXXX' is dropped
    modif = by_id["01dc0273-018a-49a1-b66e-2f202c4354f4"]
    assert modif.notice_type is NoticeType.MODIFICATION
    assert modif.buyer_id is None and modif.buyer_name == "FE.EI - Beschaffung Infrastruktur"

    # national below-threshold notice (numeric id, reduced eforms-sdk-0.1 structure, buyer inline)
    gsi = by_id["25975366"]
    assert gsi.notice_type is NoticeType.TENDER
    assert gsi.buyer_name == "GSI Helmholtzzentrum für Schwerionenforschung GmbH"
    assert gsi.cpv_codes == ["48000000"]
    assert gsi.procedure_type == "de-comp-w-call"
    assert gsi.deadline_at == datetime(2026, 11, 11, 12, 0, tzinfo=CET)
    assert gsi.url.endswith("noticeId=25975366&noticeVersion=1")
    assert gsi.refs["ocid"] == "ocds-mnwr74-25975366"

    # entry stamped before its publication day -> clamped to the start of that day
    early = by_id["e392a2d7-61ba-4d9b-bdc8-ca51dd33a634"]
    assert early.published_at == datetime(2026, 10, 27, 23, 0, tzinfo=UTC)

    # second day: BAAINBw award + a re-published defence competition whose deadline has passed
    baainbw = by_id["2493c652-6b8c-415a-813d-f8dd011a8811"]
    assert baainbw.buyer_name == "Bundesamt für Ausrüstung, Informationstechnik und Nutzung der Bundeswehr"
    assert baainbw.buyer_city == "Koblenz" and baainbw.cpv_codes == ["35400000"]
    assert baainbw.notice_type is NoticeType.AWARD
    dock = by_id["282f5cee-5586-40c7-9b25-81709c92f4e8"]
    assert dock.status is Status.CLOSED
    assert dock.refs["changed_notice"] == "469bd9bf-e4fe-4c60-b18d-0ea255d805af-01"


@respx.mock
async def test_unchanged_days_are_skipped_with_etag():
    route = _day_routes({
        "2026-10-28": httpx.Response(304, headers={"etag": '"version-6"'}),
        "2026-10-29": httpx.Response(200, content=_zip("2026-10-29"), headers={"etag": '"version-1"'}),
    })
    state = {"last_day": "2026-10-29", "etags": {"2026-10-28": '"version-6"', "2026-10-29": '"version-0"'}}

    notices, checkpoints = await _sync(state)

    sent = {c.request.url.params["pubDay"]: c.request.headers["if-none-match"] for c in route.calls}
    assert sent == {"2026-10-28": '"version-6"', "2026-10-29": '"version-0"'}
    assert len(notices) == 2   # only the changed day was re-parsed
    assert checkpoints[-1].state["etags"]["2026-10-29"] == '"version-1"'


@respx.mock
async def test_stops_when_day_not_yet_available_and_nothing_to_do_for_today():
    respx.get(EXPORT).mock(return_value=httpx.Response(
        400, text="The specified pubDay exceeds the allowed range. It must lie in the past."))
    notices, checkpoints = await _sync(since=datetime(2026, 10, 29, 12, tzinfo=UTC))
    assert notices == [] and checkpoints == []

    # since = today: no request at all, state carried over
    route = respx.get(EXPORT)
    route.reset()
    notices, checkpoints = await _sync({"last_day": "2026-10-29", "etags": {"2026-10-29": "x"}},
                                       since=datetime(2026, 10, 30, 1, tzinfo=UTC))
    assert route.call_count == 0 and notices == []
    assert checkpoints[-1].state == {"last_day": "2026-10-29", "etags": {"2026-10-29": "x"}}


@respx.mock
async def test_refresh_fetches_latest_version_of_one_notice():
    import zipfile
    from io import BytesIO

    xml = zipfile.ZipFile(BytesIO(_zip("2026-10-28"))).read("c5b95c53-2c8d-43e1-8d13-c1b0557e7c8d-01.xml")
    route = respx.get(f"{NOTICE_API}/c5b95c53-2c8d-43e1-8d13-c1b0557e7c8d").mock(
        return_value=httpx.Response(200, content=xml))
    respx.get(f"{NOTICE_API}/gone").mock(return_value=httpx.Response(410))

    conn, client = make_connector(Oeffentlichevergabe)
    try:
        ref = StoredRef("c5b95c53-2c8d-43e1-8d13-c1b0557e7c8d", "https://x", {"notice": "c5b95c53-2c8d-43e1-8d13-c1b0557e7c8d"},
                        {"published_at": "2026-10-28T04:26:04+01:00", "pub_day": "2026-10-28"})
        n = await conn.refresh(ref)
        assert route.calls[0].request.url.params["format"] == "eforms"
        assert n is not None and validate_notice(n) == []
        assert n.deadline_at == datetime(2026, 12, 3, 13, 0, tzinfo=CET)
        assert n.published_at == datetime(2026, 10, 28, 3, 26, 4, tzinfo=UTC)
        assert await conn.refresh(StoredRef("gone", "https://x", {}, {})) is None
    finally:
        await client.aclose()


@respx.mock
async def test_long_backfill_keeps_a_bounded_window_and_stops_at_unavailable_day():
    from tenderhub.connectors import oeffentlichevergabe as mod

    in_flight = peak = 0

    async def handler(req):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        if req.url.params["pubDay"] == "2026-10-20":   # pretend this day is not closed yet
            return httpx.Response(400, text="It must lie in the past.")
        return httpx.Response(404)

    route = respx.get(EXPORT).mock(side_effect=handler)
    notices, checkpoints = await _sync(since=datetime(2026, 10, 9, 12, tzinfo=UTC))   # 10-09 .. 10-29

    assert notices == []
    assert [c.state["last_day"] for c in checkpoints] == [f"2026-10-{d:02d}" for d in range(9, 20)]
    assert 1 < peak <= mod.WINDOW                       # parallel, but bounded
    assert route.call_count <= 11 + 1 + mod.WINDOW      # nothing far past the stop day is requested


def test_phone_number_in_the_identifier_field_is_not_a_buyer_id():
    """e-Vergabe fills BT-501 with the phone number ('t:022855041533') when the buyer has no identifier."""
    from lxml import etree

    xml = fixture_path("oeffentlichevergabe", "notice_d53c984e-fb97-4555-8e8d-170b3ea2578d.xml").read_bytes()
    conn, _ = make_connector(Oeffentlichevergabe)
    conn.ctx.now = lambda: NOW
    n = conn.to_notice(etree.fromstring(xml), published=None)
    assert n.buyer_name == "Bundesamt für Infrastruktur, Umweltschutz und Dienstleistungen der Bundeswehr"
    assert n.buyer_id is None
    assert n.title == "1/DLII6/VM191 - RV Tubus, divers und Katheter, divers"
    assert n.refs["changed_notice"] == "3c8f8b3f-2fce-4b85-9077-b4537fd8edfc-01"


def test_org_identifier_keeps_real_ids_and_drops_phones_and_words():
    from tenderhub.connectors.oeffentlichevergabe import org_identifier

    for real in ("991-14142-17", "09162000-ZRE1000000-09", "DE289669264", "Leitweg-ID: 991-19564-47",
                 "62f0f0af-9431-411a-8f96-be7b21daafb8", "HRB 1234", "0898776620"):
        assert org_identifier(real) == real
    for bogus in ("t:022855041533", "t: 022894990", "Tel.-Nr.: +49 921/606-2871", "+49 203 604 3423", "fax: 0228 123",
                  "XXXX", "keine Angabe", "entfällt", "-Nicht angegeben-", "WISTA.Plan GmbH", None, ""):
        assert org_identifier(bogus) is None
    # a bare number that is the organisation's own phone number
    assert org_identifier("0898776620", ["+49 89 8776620"]) is None
    assert org_identifier("0898776620", ["+49 89 1111111"]) == "0898776620"
