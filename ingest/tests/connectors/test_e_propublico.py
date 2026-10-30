"""e-ProPublico connector against recorded pages (list, proceeding cards, document tabs)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import respx

from tenderhub.connector import StoredRef
from tenderhub.connectors.e_propublico import DOCS, LIST, EPropublico, parse_list
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_text, make_connector, run_sync

WARSAW = ZoneInfo("Europe/Warsaw")
SINCE = datetime(2026, 10, 23, tzinfo=WARSAW)
DETAILS_RE = re.compile(r"https://e-propublico\.pl/Ogloszenia/Details/(?P<id>[0-9a-f-]{36})")

STARCZA = "e6baa3c2-e1ec-4f75-89c9-fe047232b01b"
KRAKOW_PAN = "79062d0b-6ff2-4eca-a425-cefcd77e1964"
ROPCZYCE = "9ac60255-ffa6-4d0e-abaa-d5fb57bec685"   # published 2026-10-22, never modified -> before SINCE
POZNAN = "21ddb48a-9655-44d4-ba70-9e83cb0895cc"     # published 2026-09-17, modified 2026-10-23
NISKO = "cf2c778c-89bb-4959-a725-016ecd7ebb55"      # modified 2026-10-30
AGH_CLOSED = "2b2eafef-2122-475f-b6f1-33b94760c66b"


def _details(request: httpx.Request, **_: str) -> httpx.Response:
    pid = DETAILS_RE.match(str(request.url)).group("id")
    return httpx.Response(200, text=load_text("e_propublico", f"details_{pid[:8]}.html"))


def _docs(request: httpx.Request) -> httpx.Response:
    pid = request.url.params["przetargId"]
    return httpx.Response(200, text=load_text("e_propublico", f"docs_{pid[:8]}.html"))


def _mock_site():
    lst = respx.get(LIST).mock(side_effect=lambda req: httpx.Response(200, text=load_text(
        "e_propublico", {"1": "list_page1.html", "2": "list_page2.html"}.get(req.url.params["page"], "list_empty.html"))))
    det = respx.get(url__regex=DETAILS_RE.pattern).mock(side_effect=_details)
    docs = respx.get(DOCS).mock(side_effect=_docs)
    return lst, det, docs


def test_parse_list_reads_rows_and_page_count():
    rows, total = parse_list(load_text("e_propublico", "list_page1.html"))
    assert total == 2
    assert [r["id"] for r in rows] == [STARCZA, KRAKOW_PAN, ROPCZYCE]
    assert rows[0] == {
        "id": STARCZA, "signature": "GKI.271.1.6.2026",
        "title": "Odbiór i zagospodarowanie odpadów komunalnych od właścicieli nieruchomości zamieszkałych "
                 "na terenie Gminy Starcza w 2027 roku",
        "buyer": "Gmina Starcza", "deadline": "02.11.2026 09:00:00", "status": "Składanie ofert",
    }
    assert parse_list(load_text("e_propublico", "list_empty.html"))[0] == []


@respx.mock
async def test_sync_walks_list_reads_cards_and_filters_by_change_time():
    lst, det, docs = _mock_site()

    notices, checkpoints = await run_sync(EPropublico, SINCE)

    assert [c.request.url.params["page"] for c in lst.calls] == ["1", "2"]  # "Strona 2 z 2" stops paging
    assert det.call_count == 5
    assert sorted(c.request.url.params["przetargId"] for c in docs.calls) == sorted([STARCZA, KRAKOW_PAN, POZNAN, NISKO])
    assert all(c.request.headers["accept-language"].startswith("pl") for c in lst.calls + det.calls)
    assert [p for n in notices for p in validate_notice(n)] == []

    by_id = {n.source_id: n for n in notices}
    assert set(by_id) == {STARCZA, KRAKOW_PAN, POZNAN, NISKO}  # ROPCZYCE unchanged since 2026-10-22

    n = by_id[STARCZA]
    assert n.source == "e_propublico"
    assert n.url == f"https://e-propublico.pl/Ogloszenia/Details/{STARCZA}"
    assert n.title.startswith("Odbiór i zagospodarowanie odpadów komunalnych")
    assert n.notice_type is NoticeType.TENDER and n.status is Status.OPEN
    assert n.buyer_name == "Gmina Starcza" and n.buyer_city == "Starcza" and n.buyer_country == "PL"
    assert n.procedure_type == "Tryb podstawowy bez negocjacji"
    assert n.published_at == datetime(2026, 10, 19, 12, 54, 23, tzinfo=UTC)    # CEST
    assert n.deadline_at == datetime(2026, 11, 2, 8, 0, tzinfo=UTC)            # CET
    assert n.cpv_codes == []  # the platform does not publish CPV codes in HTML
    assert n.refs == {"procedure": "GKI.271.1.6.2026"}
    assert n.language == "pl"
    assert len(n.documents) == 15
    assert (n.documents[0].title, n.documents[0].kind) == ("Zmiana ogłoszenia o zamówieniu", "amendment")
    assert n.documents[0].url == "https://e-propublico.pl/Dokumenty/Download/7c2c50f2-b011-2c43-aa16-3697c22b7171"
    assert {d.kind for d in n.documents} >= {"q&a", "notice", "specification", "annex"}
    assert n.raw["details"]["modified"] == "2026-10-27 07:35:25"

    nisko = by_id[NISKO]
    assert nisko.buyer_name == "Samodzielny Publiczny Zespół Zakładów Opieki Zdrowotnej w Nisku"
    assert nisko.buyer_city == "Nisko"
    assert nisko.deadline_at == datetime(2026, 11, 5, 7, 0, tzinfo=WARSAW)

    state = checkpoints[-1].state["rows"]
    assert set(state) == {STARCZA, KRAKOW_PAN, ROPCZYCE, POZNAN, NISKO}
    assert state[ROPCZYCE]["changed"] == "2026-10-22T14:29:06+02:00"


@respx.mock
async def test_second_sync_skips_unchanged_rows_without_fetching_cards():
    _mock_site()
    _, checkpoints = await run_sync(EPropublico, SINCE)
    state = checkpoints[-1].state
    respx.reset()
    lst, det, docs = _mock_site()

    notices, checkpoints = await run_sync(EPropublico, datetime(2026, 10, 28, tzinfo=WARSAW), state)

    assert lst.call_count == 2
    assert [DETAILS_RE.match(str(c.request.url)).group("id") for c in det.calls] == [NISKO]
    assert [n.source_id for n in notices] == [NISKO]
    assert set(checkpoints[-1].state["rows"]) == set(state["rows"])


@respx.mock
async def test_empty_list_still_checkpoints():
    respx.get(LIST).mock(return_value=httpx.Response(200, text=load_text("e_propublico", "list_empty.html")))
    notices, checkpoints = await run_sync(EPropublico, SINCE)
    assert notices == [] and len(checkpoints) == 1


@respx.mock
async def test_refresh_reads_closed_proceeding_with_budget_and_sections():
    _mock_site()
    conn, client = make_connector(EPropublico)
    try:
        ref = StoredRef(AGH_CLOSED, f"https://e-propublico.pl/Ogloszenia/Details/{AGH_CLOSED}", {}, {})
        n = await conn.refresh(ref)
        respx.get(url__regex=DETAILS_RE.pattern).mock(return_value=httpx.Response(404, text="Not found"))
        gone = await conn.refresh(ref)
    finally:
        await client.aclose()

    assert gone is None
    assert validate_notice(n) == []
    assert n.status is Status.CLOSED  # "Postępowanie zakończone"
    assert n.title == "Wynajem pojazdu wraz z kierowcą na wycieczki - KC-DE-zp.25.494.2026"
    assert n.buyer_name == "Akademia Górniczo - Hutnicza im. Stanisława Staszica w Krakowie"
    assert n.buyer_city == "Kraków"
    assert n.value_amount == 16000.0 and n.value_currency == "PLN"
    assert n.published_at == datetime(2026, 10, 7, 13, 22, 16, tzinfo=WARSAW)
    assert [(d.title, d.kind) for d in n.documents[:2]] == [("Ogłoszenie o zamówieniu", "notice"), ("SWZ", "specification")]
    assert n.refs["procedure"] == "KC-DE-zp.25.494.2026"


@respx.mock
async def test_stopping_early_cancels_in_flight_fetches():
    _mock_site()
    conn, client = make_connector(EPropublico)
    try:
        gen = conn.sync(SINCE, {})
        first = await anext(gen)
        await gen.aclose()  # the runner does this when max_notices is reached
    finally:
        await client.aclose()
    assert first.source == "e_propublico" and validate_notice(first) == []
    assert conn.http.client.is_closed
