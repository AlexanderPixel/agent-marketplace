"""TED connector against recorded API pages. Pattern every connector test follows."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import respx

from tenderhub.connectors.ted import API, Ted
from tenderhub.models import NoticeType, validate_notice

from ..conftest import load_json, run_sync

SINCE = datetime(2026, 10, 28, tzinfo=UTC)


@respx.mock
async def test_sync_pages_through_iteration_tokens_and_maps_fields():
    route = respx.post(API).mock(side_effect=[
        httpx.Response(200, json=load_json("ted", "page1.json")),
        httpx.Response(200, json=load_json("ted", "page2.json")),
    ])

    notices, checkpoints = await run_sync(Ted, SINCE)

    assert route.call_count == 2
    first_body, second_body = (json.loads(c.request.content) for c in route.calls)
    assert first_body["query"] == "publication-date>=20261028"
    assert "iterationNextToken" not in first_body
    assert second_body["iterationNextToken"] == "TOKEN-PAGE-2"
    assert len(notices) == 5 and len(checkpoints) == 1
    assert [p for n in notices for p in validate_notice(n)] == []

    by_id = {n.source_id: n for n in notices}
    sight = by_id["745279-2026"]  # Lithuanian defence-directive tender for a weapon sight
    assert sight.source == "ted"
    assert sight.url == "https://ted.europa.eu/en/notice/-/detail/745279-2026"
    assert sight.notice_type is NoticeType.TENDER
    assert sight.buyer_country == "LT"
    assert sight.legal_basis == "32009L0081"
    assert sight.cpv_codes == ["35340000"]
    assert sight.language == "lt"
    assert sight.title.startswith("Kolimatorinis taikiklis")
    assert sight.platform_url.startswith("https://viesiejipirkimai.lt/")
    assert sight.published_at == datetime(2026, 10, 27, 23, 0, tzinfo=UTC)
    assert sight.refs["ted"] == "745279-2026"


@respx.mock
async def test_empty_result_still_checkpoints():
    respx.post(API).mock(return_value=httpx.Response(200, json={"notices": [], "totalNoticeCount": 0}))
    notices, checkpoints = await run_sync(Ted, SINCE)
    assert notices == [] and len(checkpoints) == 1
