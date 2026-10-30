"""SICAP (e-licitatie.ro) connector against recorded list-endpoint pages."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from tenderhub.connector import Context
from tenderhub.connectors import sicap_ro
from tenderhub.connectors.sicap_ro import CAN_API, CN_API, SicapRo, split_buyer
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_json, run_sync

SINCE = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)     # 12:00 Bucharest on 29 Oct
NOW = datetime(2026, 10, 30, 15, 0, tzinfo=UTC)       # 17:00 Bucharest on 30 Oct
EMPTY = {"total": 0, "items": [], "searchTooLong": False}


def _replay(pages: dict[tuple[str, int], str]):
    """respx side effect: answer by (publication day, pageIndex); unknown windows are empty."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("referer", "").startswith("https://www.e-licitatie.ro/pub/")
        body = json.loads(request.content)
        assert body["startPublicationDate"] == body["endPublicationDate"]
        key = (body["startPublicationDate"][:10], body["pageIndex"])
        name = pages.get(key)
        return httpx.Response(200, json=load_json("sicap_ro", name) if name else EMPTY)
    return handler


@pytest.fixture
def fixed_now(monkeypatch):
    monkeypatch.setattr(Context, "now", lambda self: NOW)
    monkeypatch.setattr(sicap_ro, "PAGE_SIZE", 2)


@respx.mock
async def test_sync_walks_days_pages_both_lists_and_maps_fields(fixed_now):
    cn = respx.post(CN_API).mock(side_effect=_replay({
        ("2026-10-30", 0): "cn_page0.json", ("2026-10-30", 1): "cn_page1.json"}))
    can = respx.post(CAN_API).mock(side_effect=_replay({
        ("2026-10-30", 0): "can_page0.json", ("2026-10-30", 1): "can_page0.json"}))

    notices, checkpoints = await run_sync(SicapRo, SINCE)

    # one request per day for 29 Oct (empty), two pages for 30 Oct (total 3, page size 2)
    cn_calls = sorted((json.loads(c.request.content)["startPublicationDate"][:10],
                       json.loads(c.request.content)["pageIndex"]) for c in cn.calls)
    assert cn_calls == [("2026-10-29", 0), ("2026-10-30", 0), ("2026-10-30", 1)]
    assert can.call_count == 3
    assert len(checkpoints) == 1 and checkpoints[0].state == {"last_day": "2026-10-30"}
    # CAN page replayed twice -> deduplicated by notice number
    assert sorted(n.source_id for n in notices) == [
        "CAN1160267", "CAN1176684", "CN1097904", "RFQ1000548", "SCN1181612", "SCNA1138672"]
    assert [p for n in notices for p in validate_notice(n)] == []

    by_id = {n.source_id: n for n in notices}
    hood = by_id["SCN1181612"]   # simplified procedure by an MApN military unit
    assert hood.source == "sicap_ro"
    assert hood.url == "https://www.e-licitatie.ro/pub/notices/simplified-notice/v2/view/100246682"
    assert hood.title == "HOTA PROFESIONALA PENTRU BUCATARIE - 12 CPL"
    assert hood.notice_type is NoticeType.TENDER and hood.status is Status.OPEN
    assert hood.buyer_id == "4270783"
    assert hood.buyer_name == "Ministerul Apararii - Unitatea Militara 01434"
    assert hood.buyer_country == "RO"
    assert hood.cpv_codes == ["39141500"]
    assert hood.procedure_type == "Procedura simplificata"
    assert hood.published_at == datetime(2026, 10, 30, 13, 52, tzinfo=UTC)
    assert hood.deadline_at == datetime(2026, 11, 13, 13, 0, tzinfo=UTC)
    assert hood.value_amount == 277561.99 and hood.value_currency == "RON"
    assert hood.language == "ro"
    assert hood.refs == {"notice": "SCN1181612", "procedure": "100335277", "sicap_notice_id": "101414958"}

    cn1 = by_id["CN1097904"]     # open procedure, framework agreement
    assert cn1.url == "https://www.e-licitatie.ro/pub/notices/c-notice/v2/view/100213671"
    assert cn1.procedure_type == "Licitatie deschisa"
    assert cn1.cpv_codes == ["31400000"]
    assert cn1.deadline_at == datetime(2026, 12, 3, 13, 0, tzinfo=UTC)

    rfq = by_id["RFQ1000548"]
    assert rfq.url == "https://www.e-licitatie.ro/pub/notices/rfq-invitation/v2/view/100246662"
    assert rfq.title == "Contract furnizare anvelope aeronave F-16"
    assert rfq.cpv_codes == ["34731800"]

    award = by_id["CAN1160267"]
    assert award.notice_type is NoticeType.AWARD and award.status is Status.AWARDED
    assert award.url == "https://www.e-licitatie.ro/pub/notices/ca-notices/view-c/100665003"
    assert award.buyer_id == "13624359" and award.buyer_name == "UM 0929 Bucuresti"
    assert award.value_amount == 19848213.3 and award.value_currency == "RON"
    assert award.deadline_at is None
    assert "procedure" not in award.refs   # procedureId is null for offline procedures

    cancelled = by_id["SCNA1138672"]
    assert cancelled.notice_type is NoticeType.CANCELLATION and cancelled.status is Status.CANCELLED
    assert cancelled.url == "https://www.e-licitatie.ro/pub/notices/ca-notices/view-snc/100664941"
    assert cancelled.value_amount is None and cancelled.value_currency is None

    assert by_id["CAN1176684"].buyer_id == "1102556"   # 'RO 1102556 - RATBV S.A.'


@respx.mock
async def test_empty_days_still_checkpoint(fixed_now):
    cn = respx.post(CN_API).mock(return_value=httpx.Response(200, json=EMPTY))
    can = respx.post(CAN_API).mock(return_value=httpx.Response(200, json=EMPTY))
    notices, checkpoints = await run_sync(SicapRo, NOW)
    assert notices == [] and len(checkpoints) == 1
    assert cn.call_count == 1 and can.call_count == 1


def test_split_buyer():
    assert split_buyer("RO 1102556 - RATBV S.A.") == ("1102556", "RATBV S.A.")
    assert split_buyer("r1890420 - RAJA S.A CONSTANTA") == ("1890420", "RAJA S.A CONSTANTA")
    assert split_buyer("Primaria X") == (None, "Primaria X")
    assert split_buyer(None) == (None, None)
