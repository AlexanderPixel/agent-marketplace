"""achats.defense.gouv.fr connector against recorded (trimmed) HTML pages."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import respx

from tenderhub.connector import StoredRef
from tenderhub.connectors.achats_defense_fr import (
    NODE_URL,
    RFIS,
    SORTS,
    TENDERS,
    TERM_URL,
    AchatsDefenseFr,
    parse_rfi_listing,
    parse_tender_listing,
)
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import collect, load_text, make_connector

SRC = "achats_defense_fr"
SINCE = datetime(2026, 10, 28, tzinfo=UTC)
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
PARIS = ZoneInfo("Europe/Paris")
TENDER_NODES = ["413552", "413585", "413549", "413683"]
RFI_NODES = ["147825", "412295", "412460"]
TERMS = ["141968", "145761", "149027"]


def html(name: str) -> httpx.Response:
    return httpx.Response(200, text=load_text(SRC, name), headers={"content-type": "text/html; charset=UTF-8"})


def mock_site() -> dict[str, respx.Route]:
    routes = {
        "asc0": respx.get(TENDERS, params={"sort_by": SORTS[0], "page": "0"}).mock(return_value=html("tenders_asc_p0.html")),
        "asc1": respx.get(TENDERS, params={"sort_by": SORTS[0], "page": "1"}).mock(return_value=html("tenders_asc_p1.html")),
        "desc0": respx.get(TENDERS, params={"sort_by": SORTS[1], "page": "0"}).mock(return_value=html("tenders_desc_p0.html")),
        "rfi0": respx.get(RFIS, params={"page": "0"}).mock(return_value=html("rfi_p0.html")),
        "rfi1": respx.get(RFIS, params={"page": "1"}).mock(return_value=html("rfi_p1.html")),
    }
    for nid in TENDER_NODES + RFI_NODES:
        routes[nid] = respx.get(NODE_URL.format(nid)).mock(return_value=html(f"node_{nid}.html"))
    for tid in TERMS:
        routes[f"term{tid}"] = respx.get(TERM_URL.format(tid)).mock(return_value=html(f"term_{tid}.html"))
    return routes


async def run(state: dict | None = None):
    conn, client = make_connector(AchatsDefenseFr)
    conn.ctx.now = lambda: NOW
    try:
        return await collect(conn.sync(SINCE, state or {}))
    finally:
        await client.aclose()


@respx.mock
async def test_sync_walks_both_sorts_filters_by_date_and_maps_fields():
    routes = mock_site()

    notices, checkpoints = await run()

    # 4 unique consultations after the ascending pass < "5 Résultat(s)", so the descending pass runs too
    assert routes["asc0"].call_count == routes["asc1"].call_count == routes["desc0"].call_count == 1
    # 413714 was published 14/08/2026 (before `since`): its detail page is never fetched (respx would fail)
    assert routes["term141968"].call_count == 1  # CPV term shared by two notices is cached
    assert [p for n in notices for p in validate_notice(n)] == []
    by_id = {n.source_id: n for n in notices}
    # the 2024 MEDEVAC RFI closed before `since` and is skipped
    assert sorted(by_id) == sorted(TENDER_NODES + ["412295", "412460"])

    tce = by_id["413552"]
    assert tce.source == "achats_defense_fr"
    assert tce.url == "https://achats.defense.gouv.fr/node/413552"
    assert tce.title == "Accord-cadre Travaux TCE au profit de l'USID de Bordeaux (Hors DGA)"
    assert tce.notice_type is NoticeType.TENDER and tce.status is Status.OPEN
    assert tce.buyer_name == "MINARM / SGA / SID / SID Sud-Ouest - Service d'infrastructure de la défense Sud-Ouest"
    assert tce.buyer_country == "FR" and tce.language == "fr"
    assert tce.cpv_codes == ["45000000"]
    assert tce.procedure_type == "Procédure concurrentielle avec négociation (PCN)"
    assert tce.published_at == datetime(2026, 10, 29, tzinfo=PARIS)
    assert tce.deadline_at == datetime(2026, 12, 16, 23, 59, tzinfo=PARIS)
    assert tce.platform_url == ("https://www.marches-publics.gouv.fr/?page=Entreprise.EntrepriseAdvancedSearch"
                                "&AllCons&id=3095817&orgAcronyme=g7h")
    assert tce.refs == {"place": "3095817", "procedure": "DAF_2026_000856"}
    assert tce.description.splitlines()[0].startswith("Lot 1 travaux intérieur du bâtiment — Travaux intérieur")
    assert tce.raw["departments"] == ["Gironde", "Lot-et-Garonne"]

    assert by_id["413549"].cpv_codes == ["65120000"]
    assert by_id["413683"].cpv_codes == ["35521000"]          # found only by the descending pass
    assert by_id["413683"].buyer_name.startswith("MINARM / SSF / DSSF Brest")
    assert by_id["413585"].refs["procedure"] == "P26028"

    rfi = by_id["412460"]
    assert rfi.notice_type is NoticeType.PLAN
    assert rfi.title == "Construction d’infrastructures dans un environnement nucléaire"
    assert rfi.deadline_at == datetime(2026, 11, 13, 23, 59, tzinfo=PARIS)
    assert rfi.buyer_name == "Ministère des Armées" and rfi.published_at is None
    assert rfi.description.startswith("Dans le cadre du programme d’infrastructure CANADA")
    assert rfi.documents[0].url.endswith("/sites/default/files/di/2026-10/Questionnaire%20sourcing%20PI%20CANADA_0.pdf")

    final = checkpoints[-1].state
    assert final["cpv_terms"] == {"141968": "45000000", "145761": "65120000", "149027": "35521000"}
    assert final["rfi_seen"] == ["147825", "412295", "412460"]


@respx.mock
async def test_second_sync_reuses_state_and_skips_seen_rfis_and_cached_terms():
    routes = mock_site()
    _, checkpoints = await run()
    for r in routes.values():
        r.reset()

    notices, _ = await run(checkpoints[-1].state)

    assert sorted(n.source_id for n in notices) == sorted(TENDER_NODES)
    assert all(routes[n].call_count == 0 for n in RFI_NODES)
    assert all(routes[f"term{t}"].call_count == 0 for t in TERMS)


def test_listing_parses_total_and_pager():
    first = parse_tender_listing(load_text(SRC, "tenders_asc_p0.html"))
    last = parse_tender_listing(load_text(SRC, "tenders_asc_p1.html"))
    assert first.total == 5 and first.has_next and not last.has_next
    assert first.last_page == 1 and last.last_page is None   # "dernière page" link drives the parallel walk
    assert parse_rfi_listing(load_text(SRC, "rfi_p0.html")).last_page == 1
    assert [c["nid"] for c in first.cards] == ["413552", "413714", "413585"]
    assert first.cards[1]["published"] == "14/08/2026" and first.cards[1]["procedure"] == "AOO"


@respx.mock
async def test_refresh_refetches_node_and_handles_removed_pages():
    respx.get(NODE_URL.format("413552")).mock(return_value=html("node_413552.html"))
    respx.get(TERM_URL.format("141968")).mock(return_value=html("term_141968.html"))
    respx.get(NODE_URL.format("999999")).mock(return_value=httpx.Response(404, text="Page introuvable"))
    conn, client = make_connector(AchatsDefenseFr)
    try:
        n = await conn.refresh(StoredRef("413552", NODE_URL.format("413552"), {}, {"kind": "tender", "card": {}}))
        gone = await conn.refresh(StoredRef("999999", NODE_URL.format("999999"), {}, {"kind": "tender"}))
    finally:
        await client.aclose()
    assert n is not None and validate_notice(n) == []
    assert n.deadline_at == datetime(2026, 12, 16, 23, 59, tzinfo=PARIS) and n.cpv_codes == ["45000000"]
    assert n.platform_url.endswith("id=3095817&orgAcronyme=g7h")
    assert gone is None


@respx.mock
async def test_detail_pages_are_fetched_concurrently_and_cpv_terms_shared():
    routes = mock_site()
    in_flight = peak = 0

    def slow(name: str):
        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return html(name)
        return handler

    for nid in TENDER_NODES:
        routes[nid].mock(side_effect=slow(f"node_{nid}.html"))
    routes["term141968"].mock(side_effect=slow("term_141968.html"))

    notices, _ = await run()

    assert peak >= 2                                   # detail pages overlap instead of running one by one
    assert routes["term141968"].call_count == 1        # two notices share CPV term 141968: one request
    assert {n.source_id: n.cpv_codes for n in notices if n.source_id in ("413552", "413585")} == {
        "413552": ["45000000"], "413585": ["45000000"]}
