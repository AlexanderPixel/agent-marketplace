"""BOAMP connector against recorded Opendatasoft bulk exports.

Fixtures are six real records (2026-10-27..29) fetched from the DILA open-data API (`exports/json`), one
file per publication day, with long free-text fields inside `donnees` truncated.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from tenderhub.connector import Context
from tenderhub.connectors import boamp
from tenderhub.connectors.boamp import EXPORT, Boamp
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_json, run_sync

SINCE = datetime(2026, 10, 26, 22, 30, tzinfo=UTC)   # 2026-10-26 23:30 in Paris
NOW = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
PARIS = ZoneInfo("Europe/Paris")
FIXTURE_DAYS = {"2026-10-27", "2026-10-28", "2026-10-29"}


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(Context, "now", lambda self: NOW)


def by_day(request: httpx.Request) -> httpx.Response:
    where = request.url.params["where"]
    day = where.rsplit("'", 2)[-2]
    return httpx.Response(200, json=load_json("boamp", f"export-{day}.json") if day in FIXTURE_DAYS else [])


@respx.mock
async def test_sync_fetches_one_export_per_day_and_maps_fields():
    route = respx.get(EXPORT).mock(side_effect=by_day)

    notices, checkpoints = await run_sync(Boamp, SINCE)

    wheres = sorted(c.request.url.params["where"] for c in route.calls)
    assert wheres == ["dateparution = date'2026-10-26'", "dateparution = date'2026-10-27'",
                      "dateparution = date'2026-10-28'", "dateparution = date'2026-10-29'",
                      "dateparution >= date'2026-10-30'"]   # last slice open-ended
    assert all(c.request.url.params["order_by"] == "dateparution, idweb" for c in route.calls)
    assert [n.source_id for n in notices] == ["26-103999", "26-104262", "26-104791",
                                              "26-104964", "26-105041", "26-104613"]
    assert [c.state for c in checkpoints] == [{"last_date": d} for d in
                                              ("2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29",
                                               "2026-10-30")]
    assert [p for n in notices for p in validate_notice(n)] == []
    by_id = {n.source_id: n for n in notices}

    # eForms notice under the defence directive (Armée de Terre maintenance base), with real UTC offset
    army = by_id["26-103999"]
    assert army.source == "boamp" and army.language == "fr"
    assert army.url == "https://www.boamp.fr/pages/avis/?q=idweb:26-103999"
    assert army.title.startswith("Acquisition d’un parc machines de confection métal")
    assert army.notice_type is NoticeType.TENDER
    assert army.buyer_name == "12ème BSMAT NEUVY PAILLOUX"
    assert army.buyer_id == "13000873300011"
    assert (army.buyer_country, army.buyer_city) == ("FR", "Neuvy pailloux")
    assert army.cpv_codes == ["42611000", "42612000"]
    assert army.legal_basis == "32009L0081"
    assert army.published_at == datetime(2026, 10, 27, tzinfo=PARIS)
    assert army.deadline_at == datetime(2026, 11, 29, 17, 0, tzinfo=UTC)
    assert (army.value_amount, army.value_currency) == (395750.0, "EUR")
    assert army.platform_url.startswith("https://www.marches-publics.gouv.fr/entreprise/consultation/3092012")
    assert army.refs == {"boamp": "26-103999", "procedure": "e3098722-fd8f-4ac9-a078-cb21851e095e",
                         "notice": "392f0f37-444c-457d-bb75-7c71ebe54c5e"}
    assert army.procedure_type is None   # "Procédure NC" = not communicated
    assert "donnees" not in army.raw and army.raw["schema"] == "EFORMS"

    # national FNSimple tender: naive deadline is Paris wall-clock time, CPV from main object + every lot
    college = by_id["26-105041"]
    assert college.title == "Auchy Les Mines - College Joliot Curie - Mise En Accessibilite - 7 Lots"
    assert college.buyer_name == "Conseil Départemental du Pas-de-Calais"
    assert (college.buyer_id, college.buyer_city) == ("22620001200012", "ARRAS")
    assert college.cpv_codes == ["45111291", "45223220", "45410000", "45442100", "45232141", "45317000",
                                 "45313100"]
    assert college.deadline_at == datetime(2026, 11, 25, 16, 0, tzinfo=PARIS)
    assert college.deadline_at.utcoffset() == timedelta(hours=1)
    assert (college.value_amount, college.value_currency) == (656963.0, "EUR")
    assert college.procedure_type == "Procédure Ouverte"
    assert college.platform_url == "https://marchespublics596280.fr"

    # MAPA (<90k EUR): no CPV in this schema; the 'Z' on the deadline is bogus, it is local time
    mapa = by_id["26-104262"]
    assert mapa.notice_type is NoticeType.TENDER and mapa.cpv_codes == []
    assert mapa.deadline_at == datetime(2026, 11, 17, 17, 0, tzinfo=PARIS)
    assert mapa.buyer_city == "Die"
    assert mapa.platform_url.startswith("https://pays-diois.e-marchespublics.com/")

    # corrigendum / cancellation / award are typed and linked to the original notice
    rect = by_id["26-104964"]
    assert rect.notice_type is NoticeType.MODIFICATION
    assert rect.refs["boamp_previous"] == "26-91018" and rect.cpv_codes == ["45214200"]
    cancel = by_id["26-104791"]
    assert (cancel.notice_type, cancel.status) == (NoticeType.CANCELLATION, Status.CANCELLED)
    assert cancel.refs["boamp_previous"] == "26-100093"
    award = by_id["26-104613"]
    assert (award.notice_type, award.status) == (NoticeType.AWARD, Status.AWARDED)
    assert award.buyer_id is None   # eForms CompanyID "54135" is not a SIREN/SIRET
    assert award.legal_basis == "32014L0024" and award.cpv_codes == ["45233221"]
    assert award.value_amount == 7000000.0 and award.deadline_at is None
    assert award.published_at.astimezone(timezone.utc) == datetime(2026, 10, 28, 23, 0, tzinfo=UTC)


@respx.mock
async def test_days_are_fetched_concurrently_but_yielded_in_order(monkeypatch):
    monkeypatch.setattr(boamp, "CONCURRENCY", 3)
    in_flight = peak = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        day = request.url.params["where"].rsplit("'", 2)[-2]
        await asyncio.sleep(0.05 if day == "2026-10-27" else 0.01)   # first day answers last
        in_flight -= 1
        return by_day(request)

    respx.get(EXPORT).mock(side_effect=slow)
    notices, checkpoints = await run_sync(Boamp, SINCE)
    assert peak == 3
    assert [n.published_at.date().isoformat() for n in notices] == sorted(
        n.published_at.date().isoformat() for n in notices)
    assert [c.state["last_date"] for c in checkpoints][-1] == "2026-10-30"


@respx.mock
async def test_since_today_is_a_single_open_ended_request():
    route = respx.get(EXPORT).mock(return_value=httpx.Response(200, json=[]))
    notices, checkpoints = await run_sync(Boamp, NOW)
    assert [c.request.url.params["where"] for c in route.calls] == ["dateparution >= date'2026-10-30'"]
    assert notices == [] and [c.state for c in checkpoints] == [{"last_date": "2026-10-30"}]


@respx.mock
async def test_error_propagates():
    respx.get(EXPORT).mock(return_value=httpx.Response(404, json={"error": "nope"}))
    with pytest.raises(Exception):
        await run_sync(Boamp, SINCE)
