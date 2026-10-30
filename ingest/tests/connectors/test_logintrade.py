"""Logintrade connector against recorded pages: platform-wide feed + one stand-alone tenant (gcm)."""

from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import pytest
import respx

from tenderhub.connector import Checkpoint, StoredRef
from tenderhub.connectors import logintrade as lt
from tenderhub.connectors.logintrade import FEED, WARSAW, Logintrade
from tenderhub.models import NoticeType, validate_notice

from ..conftest import load_text, make_connector, run_sync

SINCE = datetime(2026, 10, 29, tzinfo=WARSAW)
GCM = "https://gcm.logintrade.net"
GCM_BUYER = "Górnośląskie Centrum Medyczne im. prof. Leszka Gieca Śląskiego Uniwersytetu Medycznego w Katowicach"
LOTOS_URL = "https://lotos.logintrade.net/zapytania_email,1670239,654aab8b5bc2e7d8a23dd1cb0a9d7fcf.html"
GCM_URL = "https://gcm.logintrade.net/zapytania_email,243242,1921a1f96118224cb8f65d41905839ac.html"


@pytest.fixture(autouse=True)
def one_tenant(monkeypatch):
    monkeypatch.setattr(lt, "load_tenants", lambda platform: [{"tenant": "gcm.logintrade.net", "buyer": GCM_BUYER}])


def _paged(prefix: str, pages: dict[str, str]):
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        return httpx.Response(200, text=load_text("logintrade", pages[page]))
    return handler


def _mock_site() -> dict[str, respx.Route]:
    routes = {
        "feed": respx.get(url__startswith=FEED).mock(side_effect=_paged(FEED, {
            "1": "feed_page1.html", "2": "feed_page2.html", "3": "feed_page3.html"})),
        "ustawowe": respx.get(url__startswith=f"{GCM}/rejestracja/ustawowe.html").mock(side_effect=_paged("", {
            "1": "gcm_ustawowe_page1.html", "2": "gcm_ustawowe_page2.html"})),
        "przetargi": respx.get(url__startswith=f"{GCM}/rejestracja/przetargi.html").mock(side_effect=_paged("", {
            "1": "gcm_przetargi_page1.html"})),
        "lotos": respx.get(LOTOS_URL).respond(200, text=load_text("logintrade", "detail_lotos_1670239.html")),
        "gcm": respx.get(GCM_URL).respond(200, text=load_text("logintrade", "detail_gcm_243242.html")),
    }
    # every other notice page is gone -> the connector falls back to the list row
    routes["other"] = respx.get(url__regex=r"/zapytania_email,\d+,[0-9a-f]+\.html$").respond(404)
    return routes


@respx.mock
async def test_sync_reads_feed_and_tenant_lists_and_maps_fields():
    routes = _mock_site()

    notices, checkpoints = await run_sync(Logintrade, SINCE)

    # feed: page 2 still has a row from 10-29, page 3 is all older than since-1d -> stop
    assert [c.request.url.params.get("page") for c in routes["feed"].calls] == [None, "2", "3"]
    # tenant list sorted by deadline: page 2 ends before `since` -> stop; przetargi page 1 already old
    assert routes["ustawowe"].call_count == 2 and routes["przetargi"].call_count == 1
    assert [p for n in notices for p in validate_notice(n)] == []
    by_id = {n.source_id: n for n in notices}
    assert set(by_id) == {
        "platformazakupowa.grupaazoty.com:1670245", "lotos:1670239", "lotams:1670244",  # feed page 1
        "zepak:1669876", "cersanit:1670184",                                             # feed page 2
        "gcm:243242", "gcm:243307", "gcm:240731",                                        # gcm ustawowe
    }
    assert len(notices) == 8 and len(checkpoints) == 4

    # shared-platform request, enriched from its legacy notice page
    lotos = by_id["lotos:1670239"]
    assert lotos.source == "logintrade" and lotos.url == LOTOS_URL
    assert lotos.title == "RG/160058670/ 9344 Kable elektryczne na potrzeby instalacji 4010 NB02"
    assert lotos.notice_type is NoticeType.TENDER
    assert lotos.buyer_name == "Rafineria Gdańska Sp. z o.o."
    assert lotos.buyer_id == "5832850390" and lotos.buyer_city == "Gdańsk" and lotos.buyer_country == "PL"
    assert lotos.published_at == datetime(2026, 10, 30, tzinfo=WARSAW)
    assert lotos.deadline_at == datetime(2026, 11, 12, 10, 0, tzinfo=WARSAW)
    assert lotos.procedure_type == "Zapytanie ofertowe"
    assert lotos.refs == {"logintrade": "Z473/460288"}
    assert lotos.cpv_codes == [] and lotos.language == "pl"
    assert lotos.description.startswith("Rafineria Gdańska zaprasza")
    assert lotos.raw["kind"] == "feed" and lotos.raw["list"]["region"] == "pomorskie"

    # feed row whose notice page is unavailable: list data only, deadline = end of that day
    azoty = by_id["platformazakupowa.grupaazoty.com:1670245"]
    assert azoty.title.startswith("260001183 Przygotowanie do rewizji")
    assert azoty.deadline_at == datetime(2026, 11, 5, 23, 59, tzinfo=WARSAW)
    assert azoty.buyer_name is None and azoty.platform_url == "https://platformazakupowa.grupaazoty.com/"

    # stand-alone tenant, public procurement list + current-layout notice page
    gcm = by_id["gcm:243242"]
    assert gcm.url == GCM_URL
    assert gcm.title.startswith("DZ.3321.281.2026 Dostawa wyposażenia medycznego")
    assert gcm.buyer_name.startswith("GÓRNOŚLĄSKIE CENTRUM MEDYCZNE")
    assert gcm.buyer_id == "9542269625" and gcm.buyer_city == "Katowice"
    assert gcm.procedure_type == "Przetarg nieograniczony"
    assert gcm.published_at == datetime(2026, 10, 23, 7, 18, tzinfo=WARSAW)
    assert gcm.deadline_at == datetime(2026, 11, 24, 11, 0, tzinfo=WARSAW)
    assert gcm.refs == {"procedure": "DZ.3321.281.2026", "logintrade": "Z363/13195"}
    assert [d.title for d in gcm.documents][:2] == ["Ogłoszenie o zamówieniu opublikowane w DUUE 23.10.2026.pdf",
                                                     "SWZ DZ.3321.281.2026.pdf"]
    assert all(d.url.startswith("https://gcm.logintrade.net/DocumentService,getAttachmentUnlogged,")
               for d in gcm.documents)

    # notice page missing -> buyer from the tenant registry, times from the list
    stents = by_id["gcm:240731"]
    assert stents.buyer_name == GCM_BUYER and stents.refs == {"procedure": "DZ.3321.213.2026"}
    assert stents.deadline_at == datetime(2026, 10, 30, 11, 0, tzinfo=WARSAW)

    state = checkpoints[-1].state
    assert state["tenant_offset"] == 0 and "gcm.logintrade.net" in state["visited"]
    assert set(state["seen"]) == set(by_id)

    # pages are localised by Accept-Language; the connector always asks for Polish
    for route in routes.values():
        assert all(c.request.headers["accept-language"].startswith("pl") for c in route.calls)


@respx.mock
async def test_tenants_walked_concurrently_and_failures_skipped(monkeypatch):
    monkeypatch.setattr(lt, "load_tenants", lambda platform: [
        {"tenant": "usk-wroc.logintrade.net", "buyer": "USK"},  # lists behind HTTP basic auth
        {"tenant": "gcm.logintrade.net", "buyer": GCM_BUYER},
        {"tenant": "third.logintrade.net", "buyer": "Third"},  # outside this run's batch
    ])
    monkeypatch.setattr(lt, "TENANTS_PER_RUN", 2)
    routes = _mock_site()
    respx.get(url__startswith="https://usk-wroc.logintrade.net/").respond(401)
    in_flight = {"now": 0, "max": 0}
    real_get = Logintrade._get

    async def counting_get(self, url, page=1):
        in_flight["now"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["now"])
        try:
            await asyncio.sleep(0.01)
            return await real_get(self, url, page)
        finally:
            in_flight["now"] -= 1

    monkeypatch.setattr(Logintrade, "_get", counting_get)
    notices, checkpoints = await run_sync(Logintrade, SINCE, state={"tenant_offset": 0})

    assert {n.source_id for n in notices} >= {"gcm:243242", "lotos:1670239"}
    assert in_flight["max"] > 2            # feed, tenant lists and notice pages overlap
    assert routes["ustawowe"].call_count == 2
    state = checkpoints[-1].state
    assert state["tenant_offset"] == 2     # batch of 2 done -> next run starts at the third tenant
    assert set(state["visited"]) == {"usk-wroc.logintrade.net", "gcm.logintrade.net"}
    # a checkpoint never remembers a notice that was not yielded before it
    yielded: set[str] = set()
    async def replay():
        conn, client = make_connector(Logintrade)
        try:
            async for item in conn.sync(SINCE, {}):
                if isinstance(item, Checkpoint):
                    assert set(item.state["seen"]) <= yielded
                else:
                    yielded.add(item.source_id)
        finally:
            await client.aclose()
    await replay()


def test_english_tenant_list_is_understood():
    rows, last = lt.parse_tenant_list(load_text("logintrade", "gcm_ustawowe_en.html"))
    assert len(rows) == 3 and last == 1
    assert rows[0]["case_number"] == "DZ.3321.281.2026"
    assert rows[0]["published"] == "2026-10-23 07:18" and rows[0]["deadline"] == "2026-11-24 11:00"


@respx.mock
async def test_second_sync_skips_unchanged_rows():
    _mock_site()
    _, checkpoints = await run_sync(Logintrade, SINCE)

    respx.clear()
    routes = _mock_site()
    notices, _ = await run_sync(Logintrade, SINCE, state=checkpoints[-1].state)

    assert notices == []
    assert routes["lotos"].call_count == 0 and routes["gcm"].call_count == 0 and routes["other"].call_count == 0


@respx.mock
async def test_refresh_rereads_notice_page():
    routes = _mock_site()
    notices, _ = await run_sync(Logintrade, SINCE)
    gcm = next(n for n in notices if n.source_id == "gcm:243242")

    conn, client = make_connector(Logintrade)
    try:
        again = await conn.refresh(StoredRef(gcm.source_id, gcm.url, gcm.refs, gcm.raw))
        gone = next(n for n in notices if n.source_id == "gcm:243307")
        missing = await conn.refresh(StoredRef(gone.source_id, gone.url, gone.refs, gone.raw))
    finally:
        await client.aclose()
    assert routes["gcm"].call_count == 2
    assert again.content_hash() == gcm.content_hash()
    assert missing is None


def test_market_consultation_is_a_plan():
    rows, last = lt.parse_tenant_list(load_text("logintrade", "gcm_przetargi_page1.html"))
    assert last == 2 and len(rows) == 2
    conn, _ = make_connector(Logintrade)
    n = conn.to_notice(rows[0], "przetargi", None)
    assert n.notice_type is NoticeType.PLAN
    assert n.source_id == "gcm:239871" and n.procedure_type == "Postępowanie z wyłączeniem ustawy Pzp"
    assert validate_notice(n) == []
