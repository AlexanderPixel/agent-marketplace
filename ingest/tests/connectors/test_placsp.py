"""PLACSP connector against four recorded (trimmed) pages of the official ATOM syndication feed.

Chain: head (20:03 .. 16:24 Madrid) -> PAGE2 (15:26 .. 13:12) -> PAGE3 (13:11 .. 12:39:38) -> PAGE4 (12:39:37).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from tenderhub.classify import classify_defence
from tenderhub.connectors.placsp import FEED, Placsp
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import fixture_path, load_text, run_sync

MADRID = ZoneInfo("Europe/Madrid")
BASE = "https://contrataciondelestado.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3"
PAGE2 = BASE + "_20261029_201534.atom"
PAGE3 = BASE + "_20261029_201534_1.atom"
PAGE4 = BASE + "_20261029_201534_2.atom"
PAGE5 = BASE + "_20261029_201534_3.atom"
SINCE = datetime(2026, 10, 29, 12, 30, tzinfo=UTC)   # 13:30 in Madrid
ETAG = '"3dad34-65eff8204759b"'
HEAD_IDS = ["20269137", "20806386", "20806403", "20689578"]
PAGE2_IDS = ["20806429", "20742996", "20806566", "18180277"]
PAGE3_IDS = ["19845684", "20806643", "20611490"]


def _atom(name: str, **headers: str) -> httpx.Response:
    return httpx.Response(200, text=load_text("placsp", name),
                          headers={"content-type": "application/atom+xml", **headers})


@respx.mock
async def test_sync_walks_newest_first_and_stops_at_since():
    head = respx.get(FEED).mock(return_value=_atom("page1.atom", etag=ETAG))
    page2 = respx.get(PAGE2).mock(return_value=_atom("page2.atom"))
    page3 = respx.get(PAGE3).mock(return_value=_atom("page3.atom"))
    page4 = respx.get(PAGE4).mock(return_value=httpx.Response(500))

    notices, checkpoints = await run_sync(Placsp, SINCE)

    assert head.call_count == 1 and page2.call_count == 1
    # page 2 starts inside the window, so page 3 may be prefetched — it is abandoned at its first (too old)
    # entry, or cancelled before it starts, and never followed
    assert page3.call_count <= 1 and page4.call_count == 0
    # 4 entries on the head page + 3 on page 2; the 4th entry on page 2 (13:12 Madrid) is older than since
    assert [n.source_id for n in notices] == HEAD_IDS + PAGE2_IDS[:3]
    assert [p for n in notices for p in validate_notice(n)] == []
    assert len(checkpoints) == 1
    assert checkpoints[0].state == {"newest": "2026-10-29T20:03:09.066000+01:00", "etag": ETAG,
                                    "covered": SINCE.isoformat()}

    by_id = {n.source_id: n for n in notices}

    guadix = by_id["20269137"]   # EV = pendiente de adjudicación, above EU threshold, published in the OJEU
    assert guadix.source == "placsp"
    assert guadix.url == ("https://contrataciondelestado.es/wps/poc?uri=deeplink:detalle_licitacion"
                          "&idEvl=REmMFP2q%2FJrjHF5qKI4aaw%3D%3D")
    assert guadix.title.startswith("La contratación del servicio de asistencia técnica para la redacción del Plan")
    assert guadix.notice_type is NoticeType.TENDER and guadix.status is Status.CLOSED
    assert guadix.buyer_name == "Junta de Gobierno Local del Ayuntamiento de Guadix"
    assert guadix.buyer_id == "P1809100I"
    assert guadix.buyer_country == "ES" and guadix.buyer_city == "Guadix"
    assert guadix.cpv_codes == ["71410000", "71400000"]
    assert guadix.procedure_type == "Abierto"
    assert guadix.legal_basis == "32014L0024"
    assert guadix.published_at == datetime(2026, 8, 7, tzinfo=MADRID)
    assert guadix.deadline_at == datetime(2026, 9, 10, 23, 59, tzinfo=MADRID)
    assert guadix.value_amount == 300000.0 and guadix.value_currency == "EUR"
    assert guadix.language == "es"
    assert guadix.refs == {"placsp": "20269137", "expediente": "5375/2026",
                           "procedure": "5e0902a3-2876-4db7-93ba-575573a7d769"}   # = TED procedure-identifier
    assert guadix.documents[0].title == "PCAP.pdf" and guadix.documents[0].kind == "administrative_specification"
    assert all(d.url.startswith("https://contrataciondelestado.es/") for d in guadix.documents)

    # Ministerio de Defensa buyer: open tender, deadline in Madrid time, ministry appended for classification
    albacete = by_id["20742996"]
    assert albacete.notice_type is NoticeType.TENDER and albacete.status is Status.UNKNOWN  # runner derives open
    assert albacete.buyer_name == ("Jefatura de la Sección Económico Administrativa 23 de la Base Aérea de Albacete"
                                   " (Ministerio de Defensa)")
    assert albacete.deadline_at == datetime(2026, 11, 5, 23, 59, tzinfo=MADRID)
    assert albacete.procedure_type == "Abierto simplificado"
    assert albacete.cpv_codes == ["44100000"]
    assert albacete.description.startswith("Suministros. 3 lotes: 1) Suministro de materiales")
    assert classify_defence(albacete)[0]

    isdefe = by_id["20806403"]   # ADJ = awarded
    assert isdefe.notice_type is NoticeType.AWARD and isdefe.status is Status.AWARDED
    assert isdefe.raw["awards"][0]["winner"] == "Eaton Industries (Spain) S.L."
    assert isdefe.published_at == datetime(2026, 10, 29, tzinfo=MADRID)

    assert by_id["20806429"].notice_type is NoticeType.CANCELLATION       # ANUL
    assert by_id["20806429"].status is Status.CANCELLED
    tragsa = by_id["20806566"]   # PRE = anuncio previo
    assert tragsa.notice_type is NoticeType.PLAN
    assert tragsa.cpv_codes == ["43200000"]          # only on the lots, not the folder
    assert by_id["20806386"].cpv_codes == []          # patrimonial lease: no CPV at all
    assert by_id["20806386"].deadline_at == datetime(2026, 11, 13, 14, 0, tzinfo=MADRID)


@respx.mock
async def test_checkpoint_cursor_stops_on_head_page():
    head = respx.get(FEED).mock(return_value=_atom("page1.atom"))
    page2 = respx.get(PAGE2).mock(return_value=_atom("page2.atom"))

    # last complete run saw up to 17:30 Madrid -> re-read one hour before it, so stop at 16:30
    notices, checkpoints = await run_sync(Placsp, datetime(2026, 10, 27, tzinfo=UTC),
                                          state={"newest": "2026-10-29T17:30:00+01:00"})

    assert head.call_count == 1 and page2.call_count <= 1   # prefetched at most, never followed
    assert [n.source_id for n in notices] == ["20269137", "20806386"]
    assert checkpoints[-1].state == {"newest": "2026-10-29T20:03:09.066000+01:00",
                                     "covered": "2026-10-29T16:30:00+01:00"}


@respx.mock
async def test_repeated_folders_are_yielded_once_and_page_loops_end():
    # an older page that repeats the same folders (older versions) and links back to itself
    respx.get(FEED).mock(return_value=_atom("page1.atom"))
    again = respx.get(PAGE2).mock(return_value=_atom("page1.atom"))

    notices, _ = await run_sync(Placsp, datetime(2026, 10, 29, 14, 0, tzinfo=UTC))

    assert again.call_count == 1
    assert [n.source_id for n in notices] == HEAD_IDS


@respx.mock
async def test_empty_feed_still_checkpoints():
    respx.get(FEED).mock(return_value=httpx.Response(
        200, text='<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>x</title>'
                  '<updated>2026-10-29T20:15:34.641+01:00</updated></feed>'))
    notices, checkpoints = await run_sync(Placsp, SINCE, state={"newest": "2026-10-29T20:03:09+01:00"})
    assert notices == [] and len(checkpoints) == 1
    assert checkpoints[0].state == {"newest": "2026-10-29T20:03:09+01:00", "covered": "2026-10-29T19:03:09+01:00"}


@respx.mock
async def test_walk_crosses_pages_and_maps_award_from_defence_buyer():
    respx.get(FEED).mock(return_value=_atom("page1.atom"))
    respx.get(PAGE2).mock(return_value=_atom("page2.atom"))
    respx.get(PAGE3).mock(return_value=_atom("page3.atom"))
    page4 = respx.get(PAGE4).mock(return_value=_atom("page4.atom"))
    page5 = respx.get(PAGE5).mock(return_value=httpx.Response(500))

    notices, checkpoints = await run_sync(Placsp, datetime(2026, 10, 29, 12, 50, tzinfo=MADRID))

    assert [n.source_id for n in notices] == HEAD_IDS + PAGE2_IDS + PAGE3_IDS[:2]
    assert page4.call_count <= 1 and page5.call_count == 0   # page 4 starts before since: not followed
    assert [p for n in notices for p in validate_notice(n)] == []

    torrejon = notices[-2]   # RES = resuelta (formalised), Ministerio de Defensa
    assert torrejon.title == "(20269013) Suministro de rombos"
    assert torrejon.notice_type is NoticeType.AWARD and torrejon.status is Status.AWARDED
    assert torrejon.buyer_name == ("Jefatura de la Sección Económico Administrativa 22 - Base Aérea de Torrejón"
                                   " (Agrupación de Base) (Ministerio de Defensa)")
    assert torrejon.buyer_id == "S2822022F" and torrejon.buyer_city == "Torrejón de Ardoz"
    assert torrejon.cpv_codes == ["18140000"]
    assert torrejon.value_amount == 29454.55 and torrejon.value_currency == "EUR"
    assert torrejon.refs == {"placsp": "19845684", "expediente": "2026/EA22/00000849E"}
    assert torrejon.raw["awards"][0]["winner"] == "EMILIO MUÑOZ BEJAR, S.L."
    assert classify_defence(torrejon)[0]
    assert checkpoints[-1].state["covered"] == "2026-10-29T12:50:00+01:00"


@respx.mock
async def test_next_page_downloads_while_current_page_is_still_streaming():
    """The next page is requested as soon as the current page's first entry is parsed, not after it ends."""
    page3_requested = asyncio.Event()
    body = fixture_path("placsp", "page2.atom").read_bytes()
    split = body.index(b"</entry>") + len(b"</entry>")   # link rel=next + first entry, then a stall

    async def slow_page2():
        yield body[:split]
        await asyncio.wait_for(page3_requested.wait(), 5)   # deadlocks (-> TimeoutError) if not parallel
        yield body[split:]

    def page3_side_effect(request):
        page3_requested.set()
        return _atom("page3.atom")

    respx.get(FEED).mock(return_value=_atom("page1.atom"))
    respx.get(PAGE2).mock(side_effect=lambda request: httpx.Response(200, content=slow_page2()))
    respx.get(PAGE3).mock(side_effect=page3_side_effect)
    respx.get(PAGE4).mock(return_value=_atom("page4.atom"))

    notices, _ = await run_sync(Placsp, datetime(2026, 10, 29, 12, 50, tzinfo=MADRID))
    assert [n.source_id for n in notices] == HEAD_IDS + PAGE2_IDS + PAGE3_IDS[:2]   # still in feed order


@respx.mock
async def test_unchanged_head_is_a_single_304():
    state = {"newest": "2026-10-29T20:03:09.066000+01:00", "etag": ETAG, "covered": "2026-10-27T13:30:00+00:00"}
    head = respx.get(FEED, headers={"If-None-Match": ETAG}).mock(return_value=httpx.Response(304))
    page2 = respx.get(PAGE2).mock(return_value=_atom("page2.atom"))

    notices, checkpoints = await run_sync(Placsp, datetime(2026, 10, 28, tzinfo=UTC), state=state)

    assert head.call_count == 1 and page2.call_count == 0
    assert notices == [] and [c.state for c in checkpoints] == [state]


@respx.mock
async def test_backfill_before_covered_window_is_not_conditional():
    state = {"etag": ETAG, "covered": "2026-10-29T12:30:00+00:00"}
    head = respx.get(FEED).mock(return_value=_atom("page1.atom", etag=ETAG))
    respx.get(PAGE2).mock(return_value=_atom("page2.atom"))
    respx.get(PAGE3).mock(return_value=_atom("page3.atom"))
    respx.get(PAGE4).mock(return_value=_atom("page4.atom"))

    notices, _ = await run_sync(Placsp, datetime(2026, 10, 29, 12, 0, tzinfo=UTC), state=state)

    assert "if-none-match" not in head.calls.last.request.headers
    assert [n.source_id for n in notices] == HEAD_IDS + PAGE2_IDS + PAGE3_IDS[:2]


@respx.mock
async def test_transient_errors_are_retried_and_empty_pages_end_the_walk(monkeypatch):
    delays: list[int] = []
    monkeypatch.setattr("tenderhub.connectors.placsp.retry_delay", lambda attempt: delays.append(attempt) or 0.0)
    respx.get(FEED).mock(return_value=_atom("page1.atom"))
    page2 = respx.get(PAGE2).mock(side_effect=[httpx.Response(503), httpx.ConnectError("reset"),
                                               _atom("page2.atom")])
    respx.get(PAGE3).mock(return_value=httpx.Response(200, content=b""))   # past the end: empty 200

    notices, checkpoints = await run_sync(Placsp, datetime(2026, 10, 1, tzinfo=UTC))

    assert page2.call_count == 3 and delays == [0, 1]
    assert [n.source_id for n in notices] == HEAD_IDS + PAGE2_IDS
    assert len(checkpoints) == 1


@respx.mock
async def test_hard_errors_propagate():
    respx.get(FEED).mock(return_value=_atom("page1.atom"))
    respx.get(PAGE2).mock(return_value=httpx.Response(404, text="gone"))
    with pytest.raises(Exception, match="404"):
        await run_sync(Placsp, datetime(2026, 10, 1, tzinfo=UTC))

