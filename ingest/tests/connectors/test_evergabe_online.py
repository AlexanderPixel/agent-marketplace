"""e-Vergabe connector against recorded search pages (Wicket, incl. the Ajax rows-per-page switch) and notice XMLs."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx
import respx

from tenderhub.classify import classify_defence
from tenderhub.connector import StoredRef
from tenderhub.connectors.evergabe_online import EvergabeOnline, _buyer_name, parse_dmy
from tenderhub.models import Notice, NoticeType, Status, validate_notice

from ..conftest import fixture_path, load_text, make_connector, run_sync

BERLIN = ZoneInfo("Europe/Berlin")
HOST = "https://www.evergabe-online.de"
SINCE = datetime(2026, 10, 27, 23, 0, tzinfo=UTC)  # 28.10.2026 00:00 Berlin


def fx(name: str) -> bytes:
    return fixture_path("evergabe_online", name).read_bytes()


class Site:
    """Replays the Wicket flow: cookie check redirect, ajax rows switch, stateful next-page links, XML downloads."""

    def __init__(self, xml_status: dict[str, int] | None = None):
        self.calls: list[str] = []
        self.xml_ids: list[str] = []
        self.xml_status = xml_status or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url, q = request.url, request.url.query.decode()
        self.calls.append(f"{request.method} {url.path}?{q}")
        cookie = request.headers.get("cookie", "")
        if url.path == "/search.html" and request.method == "GET":
            if not q and "COOKIES_SUPPORTED" not in cookie:
                return httpx.Response(302, headers={"Location": "/search.html?0&cookieCheck",
                                                    "Set-Cookie": "COOKIES_SUPPORTED=yes; Path=/"})
            if not q:
                return httpx.Response(302, headers={"Location": "/search.html?1"})
            if q in ("0&cookieCheck", "1"):
                return httpx.Response(200, text=load_text("evergabe_online", "search_initial.html"))
            if q == "2-1.-searchPanel-results-searchResults-results-topToolbars-toolbars-1-span-navigator-next":
                if not request.headers.get("referer", "").startswith(HOST):
                    return httpx.Response(403, text="forbidden")
                return httpx.Response(302, headers={"Location": "/search.html?2"})
            if q == "2":
                return httpx.Response(200, text=load_text("evergabe_online", "search_page2.html"))
        if url.path == "/search.html" and request.method == "POST":
            assert q == "1-1.0-rowsPerPage-rowsPerPageForm-rowsPerPageChoice"
            assert request.headers["wicket-ajax"] == "true"
            assert request.headers["wicket-ajax-baseurl"] == "search.html?1"
            assert parse_qs(request.content.decode()) == {"rowsPerPageChoice": ["3"]}
            return httpx.Response(200, content=fx("search_rows100.xml"), headers={"Content-Type": "text/xml"})
        if url.path == "/download/Bekanntmachung.xml":
            tid = url.params["id"]
            self.xml_ids.append(tid)
            if status := self.xml_status.get(tid):
                return httpx.Response(status, text="<html>gone</html>")
            return httpx.Response(200, content=fx(f"notice_{tid}.xml"), headers={"Content-Type": "text/xml"})
        return httpx.Response(404, text=f"unexpected {request.method} {url}")


@respx.mock
async def test_sync_switches_to_100_rows_pages_until_older_than_since_and_maps_fields():
    site = Site()
    respx.route(host="www.evergabe-online.de").mock(side_effect=site)

    notices, checkpoints = await run_sync(EvergabeOnline, SINCE)

    # cookie check, ajax switch to 100 rows, one next page; stops there because page 2 reaches 26.10.
    assert site.calls[:4] == [
        "GET /search.html?", "GET /search.html?0&cookieCheck", "GET /search.html?", "GET /search.html?1"]
    assert site.calls[4] == "POST /search.html?1-1.0-rowsPerPage-rowsPerPageForm-rowsPerPageChoice"
    assert sum("navigator-next" in c for c in site.calls) == 1
    assert sorted(site.xml_ids) == ["902362", "902383", "902820", "903293", "903335"]  # not 902072 (26.10.)
    assert [n.source_id for n in notices] == ["903335", "902820", "903293", "902383", "902362"]
    assert len(checkpoints) == 2 and checkpoints[-1].state["total"] == 1072
    assert [p for n in notices for p in validate_notice(n)] == []

    by_id = {n.source_id: n for n in notices}

    canteen = by_id["903335"]  # national UVgO notice by a Bundeswehr office
    assert canteen.source == "evergabe_online"
    assert canteen.url == "https://www.evergabe-online.de/tenderdetails.html?id=903335"
    assert canteen.title == "Vergabe Dienstleistungskonzession Heimkantine Julius-Leber-Kaserne Berlin"
    assert canteen.notice_type is NoticeType.TENDER
    assert canteen.buyer_name == "Verpflegungsamt der Bundeswehr"
    assert canteen.buyer_city == "Oldenburg" and canteen.buyer_country == "DE"
    assert canteen.cpv_codes == ["55500000", "55410000", "55510000", "55512000", "55330000", "55511000"]
    assert canteen.procedure_type == "National Öffentliche Ausschreibung"
    assert canteen.published_at == datetime(2026, 10, 30, tzinfo=BERLIN)
    assert canteen.deadline_at == datetime(2026, 11, 26, 13, 0, tzinfo=BERLIN)
    assert canteen.refs == {"evergabe": "903335", "reference": "VPB/2BB/DN018 (VpflABw)"}
    assert canteen.description.startswith("Vergabe Dienstleistungskonzession")
    assert canteen.language == "de"
    assert canteen.documents[0].url == "https://www.evergabe-online.de/Bekanntmachung.pdf?id=903335"
    assert classify_defence(canteen)[0]

    sonar = by_id["902820"]  # BAAINBw eForms notice under the defence directive
    assert sonar.buyer_name == "Bundesamt für Ausrüstung, Informationstechnik und Nutzung der Bundeswehr"
    assert sonar.buyer_id == "991-19518-88" and sonar.buyer_city == "Koblenz"
    assert sonar.legal_basis == "32009L0081"
    assert sonar.cpv_codes == ["38113000", "19520000"]
    assert sonar.deadline_at == datetime(2026, 12, 7, 12, 0, tzinfo=UTC)
    assert sonar.refs["notice"] == "1173bed4-057c-4d73-8214-6286d76cade3"
    assert sonar.refs["procedure"] == "2d468c03-ad1d-47b1-be94-70a56249320e"
    is_def, reasons = classify_defence(sonar)
    assert is_def and "legal_basis:2009/81" in reasons

    assert by_id["903293"].buyer_name == "Bundeswehr-Dienstleistungszentrum Stetten a. k. M."
    assert by_id["903293"].cpv_codes == ["85143000"]

    jobs = by_id["902383"]  # eForms contract notice, other buyer
    assert jobs.title == "REZ Bayern_45Startklar_AA Rosenheim"
    assert jobs.buyer_name.startswith("Bundesagentur für Arbeit, Regionales Einkaufszentrum Bayern")
    assert jobs.buyer_id == "DE811458858"
    assert jobs.procedure_type == "EU Offenes Verfahren"
    assert jobs.legal_basis == "32014L0024"
    assert jobs.deadline_at == datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
    assert jobs.published_at == datetime(2026, 10, 28, tzinfo=BERLIN)  # list day (eForms issued 27.10.)
    assert not classify_defence(jobs)[0]

    pin = by_id["902362"]  # eForms prior information notice, no deadline
    assert pin.notice_type is NoticeType.PLAN and pin.status is Status.UNKNOWN
    assert pin.deadline_at is None
    assert pin.cpv_codes == ["45221110", "45112500"]
    assert pin.buyer_name == "Hallesche Verkehrs-AG"


@respx.mock
async def test_incremental_run_skips_rows_already_yielded_unless_their_list_row_changed():
    site = Site()
    respx.route(host="www.evergabe-online.de").mock(side_effect=site)
    first, checkpoints = await run_sync(EvergabeOnline, SINCE)
    state = checkpoints[-1].state
    assert len(first) == 5 and set(state["known"]) == {n.source_id for n in first}

    site.xml_ids.clear()
    state["known"]["902383"][1] = "stale"  # e.g. its deadline changed in the result list since last run
    again, checkpoints = await run_sync(EvergabeOnline, SINCE, state)
    assert [n.source_id for n in again] == ["902383"] and site.xml_ids == ["902383"]
    assert checkpoints[-1].state["skipped_known"] == 4


@respx.mock
async def test_failed_xml_falls_back_to_list_row():
    site = Site(xml_status={"902820": 403})
    respx.route(host="www.evergabe-online.de").mock(side_effect=site)
    notices, checkpoints = await run_sync(EvergabeOnline, datetime(2026, 10, 29, 23, 0, tzinfo=UTC))  # 30.10.
    assert [n.source_id for n in notices] == ["903335", "902820", "903293"]
    assert not any("navigator-next" in c for c in site.calls)  # page 1 already reaches older rows
    sonar = notices[1]
    assert validate_notice(sonar) == []
    assert sonar.title == "FD-Boote Kl- 423 GFK-Sonardom FGS OSTE und OKER"
    assert sonar.buyer_name == "Bundeswehrverwaltung (Bundeswehr)"  # list Vergabestelle + parent for the classifier
    assert classify_defence(sonar)[0]
    assert sonar.deadline_at == datetime(2026, 12, 7, 13, 0, tzinfo=BERLIN)  # list "Nächste Frist"
    assert sonar.cpv_codes == []
    assert "902820" not in checkpoints[-1].state["known"]  # retried on the next run


@respx.mock
async def test_refresh_rereads_notice_xml_and_handles_withdrawn():
    site = Site(xml_status={"999999": 410})
    respx.route(host="www.evergabe-online.de").mock(side_effect=site)
    conn, client = make_connector(EvergabeOnline)
    try:
        row = {"id": "902072", "title": "Grundräumung - Teich Atzendorf", "reference": "V-035-61-26-VOB",
               "vergabestelle": "Sachsen-Anhalt:Salzstadt Staßfurt", "place": "Staßfurt",
               "procedure": "National Vorinformation", "next_deadline": "", "published": "26.10.26"}
        n = await conn.refresh(StoredRef("902072", "https://www.evergabe-online.de/tenderdetails.html?id=902072",
                                         {}, {"list": row}))
        assert n is not None and validate_notice(n) == []
        assert n.notice_type is NoticeType.PLAN
        assert n.buyer_name == "Salzstadt Staßfurt" and n.buyer_city == "Staßfurt"
        assert n.published_at == datetime(2026, 10, 26, tzinfo=BERLIN)
        assert await conn.refresh(StoredRef("999999", "", {}, {})) is None
    finally:
        await client.aclose()


def test_buyer_name_keeps_authority_and_marks_bundeswehr_bodies():
    assert _buyer_name("Verpflegungsamt der Bundeswehr", "Bundeswehrverwaltung") == "Verpflegungsamt der Bundeswehr"
    assert _buyer_name("BAIUDBw DL II 6.1", "Bundeswehrverwaltung") == "BAIUDBw DL II 6.1 (Bundeswehr)"
    assert _buyer_name("Paul-Ehrlich-Institut", "Paul-Ehrlich-Institut") == "Paul-Ehrlich-Institut"
    assert _buyer_name(None, "Thüringen: Friedrich-Schiller-Universität Jena") == "Friedrich-Schiller-Universität Jena"
    assert classify_defence(Notice("x", "1", "https://x.de", "t", buyer_name=_buyer_name(None, "Bundeswehrverwaltung")))[0]


def test_parse_dmy():
    assert parse_dmy("13.11.26, 10:00") == datetime(2026, 11, 13, 10, 0, tzinfo=BERLIN)
    assert parse_dmy("26.11.2026 13:00") == datetime(2026, 11, 26, 13, 0, tzinfo=BERLIN)
    assert parse_dmy("30.10.26") == datetime(2026, 10, 30, tzinfo=BERLIN)
    assert parse_dmy("") is None

