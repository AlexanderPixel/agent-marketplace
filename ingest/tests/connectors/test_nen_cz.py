"""nen_cz (Věstník veřejných zakázek API) against recorded list pages + eForms detail records."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import respx

from tenderhub.connector import StoredRef
from tenderhub.connectors import nen_cz
from tenderhub.connectors.nen_cz import CHILDREN, SEARCH, NenCz
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_json, make_connector, run_sync

SINCE = datetime(2026, 10, 29, 23, 0, tzinfo=UTC)
IDS = ["F2026-059405", "F2026-059579", "F2026-059731", "F2026-059744", "F2026-059777"]


def _children_route():
    by_submission = {}
    for vid in IDS:
        child = load_json("nen_cz", f"child_{vid}.json")
        by_submission[child[0]["parent"].rsplit("/", 1)[1]] = child

    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=by_submission.get(request.url.params["submission"], []))

    return respx.get(CHILDREN).mock(side_effect=reply)


@respx.mock
async def test_sync_pages_with_timestamp_cursor_and_maps_eforms(monkeypatch):
    monkeypatch.setattr(nen_cz, "PAGE_SIZE", 3)
    search = respx.get(SEARCH).mock(side_effect=[
        httpx.Response(200, json=load_json("nen_cz", "page1.json")),
        httpx.Response(200, json=load_json("nen_cz", "page2.json")),   # overlaps page1 by one record
        httpx.Response(200, json=load_json("nen_cz", "page3.json")),   # short page -> stop
    ])
    children = _children_route()

    notices, checkpoints = await run_sync(NenCz, SINCE)

    # pagination: keyset on publication timestamp, restarting at page 1, ascending order
    assert search.call_count == 3
    p1, p2, p3 = (c.request.url.params for c in search.calls)
    assert p1["formGroup"] == "vz" and p1["form"] == "vz" and p1["workflowPlace"] == "UVEREJNENO_VVZ"
    assert p1["data.datumUverejneniVvz[gte]"] == "2026-10-29T23:00:00+00:00"
    assert p1["order[data.datumUverejneniVvz]"] == "ASC" and p1["page"] == "1" and p1["limit"] == "3"
    assert p2["data.datumUverejneniVvz[gte]"] == "2026-10-30T13:40:01+01:00" and p2["page"] == "1"
    assert p3["data.datumUverejneniVvz[gte]"] == "2026-10-30T15:00:02+01:00"
    assert checkpoints[-1].state["cursor"] == "2026-10-30T15:00:02+01:00"
    assert sorted(checkpoints[-1].state["known"]) == IDS   # every detail fetched -> remembered for next run

    # overlap de-duplicated: one detail request per notice
    assert [n.source_id for n in notices] == IDS
    assert children.call_count == 5
    assert [p for n in notices for p in validate_notice(n)] == []

    by_id = {n.source_id: n for n in notices}

    # original contract notice, buyer resolved through OPT-300 (ORG-0001 is only the service provider)
    cn = by_id["F2026-059579"]
    assert cn.source == "nen_cz"
    assert cn.url == "https://vvz.nipez.cz/vyhledat-formular/ff68eb02-6915-4517-9d0f-6ecbdc001520"
    assert cn.notice_type is NoticeType.TENDER
    assert cn.title.startswith("Polyfunkční dům pro seniory")
    assert cn.buyer_name == "TOMVIK s.r.o."
    assert cn.buyer_id == "05147581"
    assert cn.buyer_country == "CZ" and cn.buyer_city == "Praha - Letňany"
    assert cn.cpv_codes == ["45000000"]
    assert cn.procedure_type == "open"
    assert cn.legal_basis == "32014L0024"
    assert cn.published_at == datetime(2026, 10, 30, 8, 0, 2, tzinfo=UTC)
    assert cn.deadline_at == datetime(2026, 12, 2, 8, 0, tzinfo=UTC)
    assert cn.platform_url == "https://nen.nipez.cz/profil/tomvik"
    assert cn.language == "cs"
    assert cn.refs["vvz"] == "Z2026-059579"
    assert cn.refs["nipez"] == "RVZ2600295922"
    assert cn.refs["vvz_id"] == "ff68eb02-6915-4517-9d0f-6ecbdc001520"
    assert cn.status in (Status.OPEN, Status.CLOSED)
    assert "owner" not in cn.raw and cn.raw["eforms"]["BT-02-notice"] == "cn-standard"

    # sectoral corrigendum also published in TED
    sect = by_id["F2026-059405"]
    assert sect.notice_type is NoticeType.MODIFICATION
    assert sect.buyer_name == "Správa železnic, státní organizace" and sect.buyer_id == "70994234"
    assert sect.cpv_codes == ["71322000", "71311230", "71335000", "71351730"]
    assert sect.deadline_at == datetime(2027, 1, 4, 8, 0, tzinfo=UTC)
    assert sect.refs["ted"] == "755935-2026" and sect.refs["ted_previous"] == "739097-2026"
    assert sect.legal_basis == "32014L0025"
    assert sect.platform_url.startswith("https://zakazky.spravazeleznic.cz/")
    assert sect.documents[0].url == sect.platform_url

    # contract award with total value
    award = by_id["F2026-059777"]
    assert award.notice_type is NoticeType.AWARD and award.status is Status.AWARDED
    assert award.buyer_name == "Tělocvičná jednota Sokol Jablonec n. Nisou"
    assert award.value_amount == 14201687.76 and award.value_currency == "CZK"
    assert award.cpv_codes[0] == "45000000" and "45453000" in award.cpv_codes
    assert award.deadline_at is None

    assert by_id["F2026-059731"].notice_type is NoticeType.MODIFICATION   # contract modification (form 38)
    assert by_id["F2026-059744"].notice_type is NoticeType.MODIFICATION   # corrigendum of form 16


@respx.mock
async def test_empty_result_still_checkpoints():
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json=[]))
    notices, checkpoints = await run_sync(NenCz, SINCE)
    assert notices == [] and len(checkpoints) == 1


@respx.mock
async def test_missing_detail_falls_back_to_list_fields():
    item = load_json("nen_cz", "page1.json")[1]
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json=[item]))
    respx.get(CHILDREN).mock(return_value=httpx.Response(404, json={"title": "Not Found"}))
    notices, _ = await run_sync(NenCz, SINCE)
    (n,) = notices
    assert validate_notice(n) == []
    assert n.title.startswith("Polyfunkční dům pro seniory")
    assert n.buyer_name == "TOMVIK s.r.o." and n.buyer_country == "CZ"
    assert n.deadline_at == datetime(2026, 12, 2, 8, 0, tzinfo=UTC)


@respx.mock
async def test_refresh_refetches_by_form_number():
    item = load_json("nen_cz", "page3.json")[0]
    search = respx.get(SEARCH).mock(return_value=httpx.Response(200, json=[item]))
    _children_route()
    conn, client = make_connector(NenCz)
    try:
        n = await conn.refresh(StoredRef(source_id="F2026-059777", url="", refs={}, raw={}))
    finally:
        await client.aclose()
    assert search.calls[0].request.url.params["variableId"] == "F2026-059777"
    assert n is not None and n.source_id == "F2026-059777" and n.notice_type is NoticeType.AWARD


@respx.mock
async def test_second_run_skips_unchanged_notices_but_refetches_changed_ones():
    pages = [load_json("nen_cz", f"page{i}.json") for i in (1, 2, 3)]
    items = {it["variableId"]: it for p in pages for it in p}
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json=list(items.values())))
    children = _children_route()
    _, checkpoints = await run_sync(NenCz, SINCE)
    assert children.call_count == 5
    state = checkpoints[-1].state

    # second run: same list, except one form changed (new dataHash) -> only that one is re-fetched
    changed = dict(items["F2026-059744"], dataHash="changed", updatedAt="2026-10-30T16:00:00+01:00")
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json=[*(v for k, v in items.items()
                                                                      if k != "F2026-059744"), changed]))
    children.reset()
    notices, checkpoints = await run_sync(NenCz, SINCE, state=state)
    assert [n.source_id for n in notices] == ["F2026-059744"] and children.call_count == 1
    assert sorted(checkpoints[-1].state["known"]) == IDS

    # entries older than `since` are pruned from the remembered set
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json=[]))
    _, checkpoints = await run_sync(NenCz, datetime(2026, 10, 30, 12, 0, tzinfo=UTC), state=state)
    assert sorted(checkpoints[-1].state["known"]) == ["F2026-059731", "F2026-059744", "F2026-059777"]


@respx.mock
async def test_failed_detail_is_not_remembered():
    item = load_json("nen_cz", "page1.json")[1]
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json=[item]))
    respx.get(CHILDREN).mock(return_value=httpx.Response(404, json={"title": "Not Found"}))
    _, checkpoints = await run_sync(NenCz, SINCE)
    assert checkpoints[-1].state["known"] == {}


@respx.mock
async def test_details_are_fetched_concurrently_and_yielded_in_list_order():
    import asyncio

    items = [it for i in (1, 2, 3) for it in load_json("nen_cz", f"page{i}.json")]
    items = list({it["variableId"]: it for it in items}.values())
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json=items))
    by_submission = {}
    for vid in IDS:
        child = load_json("nen_cz", f"child_{vid}.json")
        by_submission[child[0]["parent"].rsplit("/", 1)[1]] = child
    in_flight = peak = 0

    async def reply(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return httpx.Response(200, json=by_submission[request.url.params["submission"]])

    respx.get(CHILDREN).mock(side_effect=reply)
    notices, _ = await run_sync(NenCz, SINCE)
    assert [n.source_id for n in notices] == IDS
    assert peak > 1
