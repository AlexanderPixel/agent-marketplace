"""eZamawiający (Marketplanet tenants) against recorded list pages and notice cards."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from tenderhub.connector import StoredRef
from tenderhub.connectors import ezamawiajacy as mod
from tenderhub.connectors.ezamawiajacy import Ezamawiajacy, parse_details, parse_list
from tenderhub.models import NoticeType, validate_notice

from ..conftest import load_text, make_connector, run_sync

SRC = "ezamawiajacy"
WARSAW = ZoneInfo("Europe/Warsaw")
SINCE = datetime(2026, 10, 30, 12, 40, tzinfo=UTC)          # 13:40 Warsaw
WODY = "https://wodypolskie.ezamawiajacy.pl/pn/pgwwp/demand/notice/public"
JUTROSIN = "https://jutrosin.ezamawiajacy.pl"
TENANTS = [
    {"tenant": "wodypolskie.ezamawiajacy.pl", "notices": 4758, "buyer": "Państwowe Gospodarstwo Wodne Wody Polskie",
     "example_url": "https://wodypolskie.ezamawiajacy.pl/pn/pgwwp/demand/324253/notice/public/details"},
    {"tenant": "jutrosin.ezamawiajacy.pl", "notices": 18, "buyer": "Gmina Jutrosin",
     "example_url": "https://jutrosin.ezamawiajacy.pl"},
    {"tenant": "scinawa.ezamawiajacy.pl", "notices": 26, "buyer": "GMINA ŚCINAWA",
     "example_url": "https://scinawa.ezamawiajacy.pl/pn/scinawa/demand/313170/notice/public/details"},
    {"tenant": "szpitalstw.ezamawiajacy.pl", "notices": 338, "buyer": "Szpital Stalowa Wola",
     "example_url": "https://szpitalstw.ezamawiajacy.pl"},
    # second subdomain of the same buyer (same slug), like cpk / portpolska -> never read
    {"tenant": "wody-alias.ezamawiajacy.pl", "notices": 3, "buyer": "Wody Polskie",
     "example_url": "https://wody-alias.ezamawiajacy.pl/pn/pgwwp/demand/1/notice/public/details"},
]
WODY_IDS = ["241360", "241364", "241357", "241350", "241344", "241328", "241325"]


def html(name: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=load_text(SRC, name), headers={"content-type": "text/html;charset=UTF-8"})


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr(Ezamawiajacy, "tenants", lambda self: TENANTS)
    monkeypatch.setattr(mod, "PAGE_SIZE", 5)
    with respx.mock(assert_all_called=False) as router:
        # slug discovery for tenants whose example URL has no /pn/<slug>/ (single un-retried request each);
        # tenants with a known slug are probed by their first list request instead
        router.get("https://szpitalstw.ezamawiajacy.pl/servlet/HomeServlet").mock(
            return_value=html("dead_503.html", 503))
        router.get(f"{JUTROSIN}/servlet/HomeServlet").mock(return_value=html("jutrosin_home.html"))
        # lists
        router.get(f"{WODY}/current/list", params={"GD_pagestart": "0"}).mock(
            return_value=html("wodypolskie_list_p1.html"))
        router.get(f"{WODY}/current/list", params={"GD_pagestart": "5"}).mock(
            return_value=html("wodypolskie_list_p2.html"))
        router.get(f"{JUTROSIN}/pn/jutrosin/demand/notice/public/current/list").mock(
            return_value=html("jutrosin_list.html"))
        router.get("https://scinawa.ezamawiajacy.pl/pn/scinawa/demand/notice/public/current/list").mock(
            return_value=html("scinawa_list_empty.html"))
        # notice cards
        for i in WODY_IDS:
            router.get(f"{WODY}/{i}/details").mock(return_value=html(f"wodypolskie_details_{i}.html"))
        router.get(f"{JUTROSIN}/pn/jutrosin/demand/notice/public/241043/details").mock(
            return_value=html("jutrosin_details_241043.html"))
        yield router


async def test_sync_reads_tenant_lists_newest_first_and_maps_cards(site):
    # jutrosin was last visited a day before its newest notice -> its own cutoff reaches back further
    state = {"visits": {"jutrosin": int(datetime(2026, 10, 30, 12, 0, tzinfo=UTC).timestamp())}}
    notices, checkpoints = await run_sync(Ezamawiajacy, SINCE, state)

    assert [p for n in notices for p in validate_notice(n)] == []
    ids = [n.source_id for n in notices]
    # tenants are crawled concurrently, so they interleave; within a tenant the list order is kept
    assert sorted(ids) == sorted(WODY_IDS + ["241043"])
    assert [i for i in ids if i != "241043"] == WODY_IDS
    # a tenant's first list request is its liveness probe: no separate request to "/"
    assert not any(c.request.url.path == "/" for c in site.calls)

    # wody: page 1 all newer than since -> page 2 read; stops at 241318 (13:28 < 13:40), no page 3
    list_calls = [c.request.url for c in site.calls if c.request.url.path.endswith("/current/list")
                  and c.request.url.host.startswith("wodypolskie")]
    assert [u.params["GD_pagestart"] for u in list_calls] == ["0", "5"]
    assert all(u.params["GD_sortCol"] == "5" and u.params["GD_sortOrder"] == "desc" for u in list_calls)
    assert not any(c.request.url.path.endswith("/241318/details") for c in site.calls)

    by_id = {n.source_id: n for n in notices}
    quads = by_id["241364"]
    assert quads.source == "ezamawiajacy"
    assert quads.url == f"{WODY}/241364/details"
    assert quads.title == "Zakup 2 szt. quadów z wyciągarką oraz napędem 4x4 na potrzeby Zarządu Zlewni w Augustowie"
    assert quads.notice_type is NoticeType.TENDER
    assert quads.buyer_name == "Państwowe Gospodarstwo Wodne Wody Polskie"
    assert quads.buyer_country == "PL"
    assert quads.procedure_type == "Tryb podstawowy"
    assert quads.published_at == datetime(2026, 10, 30, 15, 10, tzinfo=WARSAW)
    assert quads.deadline_at == datetime(2026, 11, 9, 11, 0, tzinfo=WARSAW)
    assert quads.refs == {"procedure": "B.ROZ.270.2.103.2026"}
    assert quads.language == "pl" and quads.cpv_codes == []
    assert quads.raw["procedure_kind"] == "Postępowanie krajowe"
    assert quads.raw["order_type"] == "Dostawy"

    survey = by_id["241328"]      # below-threshold request for quotations, with description + files
    assert survey.procedure_type == "Zapytanie ofertowe otwarte"
    assert survey.raw["procedure_kind"] == "Postępowanie wyłączone z PZP"
    assert survey.description.startswith("Szczegółowy zakres prac")
    assert survey.deadline_at == datetime(2026, 11, 4, 11, 0, tzinfo=WARSAW)
    assert [d.title for d in survey.documents][:2] == ["Zał 3 Wzór umowy.zip", "Zał nr 1_formularz_oferty.docx"]
    assert "Dane kontaktowe" not in [d.title for d in survey.documents]
    assert all(d.url == survey.url and d.kind == "procurement_documents" for d in survey.documents)

    road = by_id["241043"]           # slug discovered from the tenant home page
    assert road.url == f"{JUTROSIN}/pn/jutrosin/demand/notice/public/241043/details"
    assert road.buyer_name == "Gmina Jutrosin"
    assert road.title.startswith("Budowa drogi gminnej ulica Zielona")
    assert road.published_at == datetime(2026, 10, 29, 14, 4, tzinfo=WARSAW)

    final = checkpoints[-1].state
    assert final["slugs"] == {"wodypolskie": "pgwwp", "jutrosin": "jutrosin", "scinawa": "scinawa"}
    assert "szpitalstw" in final["dead"]       # 503 from the wildcard host -> skipped for a while
    assert final["alias"] == {"wody-alias": "wodypolskie"}
    assert set(final["visits"]) == {"wodypolskie", "jutrosin", "scinawa"}
    assert len(final["seen"]) == len(notices)
    # no request ever hit the dead tenant's list
    assert not any("szpitalstw" in c.request.url.host and "list" in c.request.url.path for c in site.calls)
    assert not any(c.request.url.host.startswith("wody-alias") for c in site.calls)


async def test_second_run_skips_unchanged_rows_and_known_tenants(site):
    _, checkpoints = await run_sync(Ezamawiajacy, SINCE)
    calls_before = len(site.calls)
    state = checkpoints[-1].state
    # pretend the first run happened a day after SINCE, so cutoff = last visit - overlap = SINCE again
    state["visits"] = {k: int(SINCE.timestamp()) + 86400 for k in state["visits"]}
    notices, _ = await run_sync(Ezamawiajacy, SINCE, state)
    second = site.calls[calls_before:]
    assert notices == []                                        # every row already yielded, unchanged
    assert not any(c.request.url.path.endswith("/details") for c in second)
    assert not any(c.request.url.path in ("/", "/servlet/HomeServlet") for c in second)  # slugs + dead cached


async def test_failed_card_yields_row_and_is_retried_next_run(site):
    site.get(f"{WODY}/241357/details").mock(return_value=httpx.Response(404, text="gone"))
    notices, checkpoints = await run_sync(Ezamawiajacy, SINCE)
    flaky = {n.source_id: n for n in notices}["241357"]
    assert validate_notice(flaky) == []
    assert flaky.title and flaky.buyer_name == "Państwowe Gospodarstwo Wodne Wody Polskie"
    assert flaky.documents == []
    state = checkpoints[-1].state
    assert "241357" not in state["seen"] and "241364" in state["seen"]
    site.get(f"{WODY}/241357/details").mock(return_value=html("wodypolskie_details_241357.html"))
    state["visits"] = {k: int(SINCE.timestamp()) + 86400 for k in state["visits"]}
    again, _ = await run_sync(Ezamawiajacy, SINCE, state)
    assert [n.source_id for n in again] == ["241357"] and again[0].documents


async def test_unknown_page_marks_tenant_dead_and_early_close_cancels_workers(site):
    site.get("https://scinawa.ezamawiajacy.pl/pn/scinawa/demand/notice/public/current/list").mock(
        return_value=html("root.html"))                      # not a notice list -> wrong slug / gone
    conn, client = make_connector(Ezamawiajacy)
    try:
        gen = conn.sync(SINCE, {})
        first = await anext(gen)                              # stop after one notice, like max_notices
        assert first.source_id
        await gen.aclose()                                   # must cancel the worker tasks cleanly
        notices, checkpoints = await run_sync(Ezamawiajacy, SINCE)
        final = checkpoints[-1].state
        assert "scinawa" in final["dead"] and "scinawa" not in final["slugs"]
    finally:
        await client.aclose()


def test_plan_rotates_tail(monkeypatch):
    monkeypatch.setattr(mod, "HEAD", 2)
    monkeypatch.setattr(mod, "BATCH", 3)
    conn, _ = make_connector(Ezamawiajacy)
    tenants = [{"tenant": f"t{i}.ezamawiajacy.pl"} for i in range(9)]
    state: dict = {}
    names = lambda ts: [t["tenant"].split(".")[0] for t in ts]  # noqa: E731
    assert names(conn.plan(tenants, state)) == ["t0", "t1", "t2", "t3", "t4"]
    assert names(conn.plan(tenants, state)) == ["t0", "t1", "t5", "t6", "t7"]
    assert names(conn.plan(tenants, state)) == ["t0", "t1", "t8", "t2", "t3"]


def test_parsers_on_empty_list_and_unknown_card():
    rows, total = parse_list(load_text(SRC, "scinawa_list_empty.html"))
    assert rows == [] and total == 0
    rows, total = parse_list(load_text(SRC, "wodypolskie_list_p2.html"))
    assert total == 76 and rows[0]["id"] == "241328" and rows[0]["number"] == "RJ.ZPU.270.59.2.2026"
    assert parse_details("<html><body>login</body></html>") is None


@respx.mock
async def test_refresh_rereads_card_and_handles_removed_notice():
    respx.get(f"{WODY}/241328/details").mock(return_value=html("wodypolskie_details_241328.html"))
    respx.get(f"{WODY}/1/details").mock(return_value=httpx.Response(
        302, headers={"location": "https://wodypolskie.ezamawiajacy.pl/app/login?errorMsg=jsp.statuses.expired"}))
    respx.get("https://wodypolskie.ezamawiajacy.pl/app/login").mock(return_value=html("root.html"))
    conn, client = make_connector(Ezamawiajacy)
    try:
        raw = {"tenant": "wodypolskie.ezamawiajacy.pl", "slug": "pgwwp", "tenant_buyer": "PGW Wody Polskie",
               "row": {"id": "241328", "organization": "Państwowe Gospodarstwo Wodne Wody Polskie",
                       "stageEndDate": "2026-11-01 10:00"}}
        n = await conn.refresh(StoredRef("241328", f"{WODY}/241328/details", {}, raw))
        assert n is not None and validate_notice(n) == []
        assert n.source_id == "241328"
        assert n.deadline_at == datetime(2026, 11, 4, 11, 0, tzinfo=WARSAW)   # card wins over the stale row
        assert n.buyer_name == "Państwowe Gospodarstwo Wodne Wody Polskie"
        gone = await conn.refresh(StoredRef("1", f"{WODY}/1/details", {}, {**raw, "row": {"id": "1"}}))
        assert gone is None
    finally:
        await client.aclose()
