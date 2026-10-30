"""SmartPZP connector against recorded JSF partial responses and public proceeding cards."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx
import respx

from tenderhub.connector import StoredRef
from tenderhub.connectors.smartpzp import BASE, PREVIEW, SEARCH_FORM, TABLE, SmartPzp
from tenderhub.models import NoticeType, Status, validate_notice

from ..conftest import load_text, make_connector, run_sync

WARSAW = ZoneInfo("Europe/Warsaw")
SINCE = datetime(2026, 5, 31, 22, 30, tzinfo=UTC)  # 01-06-2026 00:30 in Warsaw

# row key (data-rk, per-session) -> proceeding id the "Podgląd" redirect points at
PREVIEWS = {
    "1244350305": "85010978",   # ZP/PN/32/2026/WKE (umw)
    "1607916729": "84842168",   # CUI-ZZ.3210.4.2026 (cui)
    "211939073": "85018518",    # CUI-ZZ.3211.126a.2026 (cui)
    "-2022634429": "84890467",  # ZP/TP/31/2026/WPM (umw)
    "-502420329": "84847292",   # ZP/TP/30/2026/WPM (umw)
}
TENANTS = {"85010978": "umw", "84842168": "cui", "85018518": "cui", "84890467": "umw", "84847292": "umw"}

EMPTY_SEARCH = (
    "<?xml version='1.0' encoding='UTF-8'?>\n<partial-response id=\"j_id1\"><changes>"
    "<update id=\"listaPostepowanForm:postepowaniaTabela\"><![CDATA[<div></div><script>"
    "PrimeFaces.cw(\"DataTable\",\"w\",{paginator:{rows:10,rowCount:0,page:0}});</script>]]></update>"
    "</changes></partial-response>"
)


def _card_url(pid: str) -> str:
    return f"{BASE}{TENANTS[pid]}/public/postepowanie?postepowanie={pid}"


def _ajax_router(posts: list[dict[str, str]], search_file: str = "search.xml"):
    def handler(request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()}
        form["_cookie"] = request.headers.get("Cookie", "")
        posts.append(form)
        assert request.headers["Faces-Request"] == "partial/ajax"
        assert form["javax.faces.ViewState"] == "-6232972009861563020:2282241092352186183"
        source = form["javax.faces.source"]
        if source == f"{SEARCH_FORM}:wyszukaj":
            return httpx.Response(200, text=load_text("smartpzp", search_file))
        if source == TABLE:
            return httpx.Response(200, text=load_text("smartpzp", "page2.xml"))
        if source == PREVIEW:
            pid = PREVIEWS[form[f"{TABLE}_selection"]]
            return httpx.Response(200, text=load_text("smartpzp", f"preview_{pid}.xml"))
        raise AssertionError(f"unexpected JSF source {source}")

    return handler


def _mock_cards(card_cookies: list[str] | None = None):
    """Cards hand out a new session cookie to a request without one (as the live server does)."""

    def card(pid: str):
        def handler(request: httpx.Request) -> httpx.Response:
            cookie = request.headers.get("Cookie", "")
            if card_cookies is not None:
                card_cookies.append(cookie)
            headers = {} if "JSESSIONID" in cookie else {
                "Set-Cookie": f'JSESSIONID="card-{pid}:server-one"; Version=1; Path=/; Secure; HttpOnly'}
            return httpx.Response(200, headers=headers, text=load_text("smartpzp", f"card_{pid}.html"))
        return handler

    for pid in PREVIEWS.values():
        respx.get(_card_url(pid)).mock(side_effect=card(pid))


def _mock_home():
    """Every GET of the start page opens a new JSF session."""
    n = iter(range(1, 100))
    return respx.get(BASE).mock(side_effect=lambda request: httpx.Response(
        200, headers=[("Set-Cookie", f"JSESSIONID=\"jsf-{next(n)}\"; Version=1; Path=/"),
                      ("Set-Cookie", "SERVERID=app02; path=/")],
        text=load_text("smartpzp", "home.html")))


@respx.mock
async def test_sync_searches_by_start_date_pages_previews_and_maps_cards():
    home = _mock_home()
    posts: list[dict[str, str]] = []
    respx.post(BASE).mock(side_effect=_ajax_router(posts))
    card_cookies: list[str] = []
    _mock_cards(card_cookies)

    notices, checkpoints = await run_sync(SmartPzp, SINCE)

    searches = [p for p in posts if p["javax.faces.source"] == f"{SEARCH_FORM}:wyszukaj"]
    # rows 0-2 come with the first search; rows 3-4 go to a second JSF view that repeats the search
    assert home.call_count == 2 and len(searches) == 2
    for search in searches:
        assert search[f"{SEARCH_FORM}:dataWszczeciaOd_input"] == "01-06-2026"  # since, in Warsaw local date
        assert f"{SEARCH_FORM}:fc236_input" not in search  # "only unfinished" unchecked: finished ones too
    pages = [p for p in posts if p["javax.faces.source"] == TABLE]
    assert len(pages) == 1 and (pages[0][f"{TABLE}_first"], pages[0][f"{TABLE}_rows"]) == ("3", "2")
    assert sum(p["javax.faces.source"] == PREVIEW for p in posts) == 5

    # each JSF view keeps its own session; pagination + previews run on the view that searched
    assert {p["_cookie"] for p in searches} == {'JSESSIONID="jsf-1"; SERVERID=app02',
                                                'JSESSIONID="jsf-2"; SERVERID=app02'}
    second = 'JSESSIONID="jsf-2"; SERVERID=app02'
    assert pages[0]["_cookie"] == second
    preview_sessions = {PREVIEWS[p[f"{TABLE}_selection"]]: p["_cookie"] for p in posts if p["javax.faces.source"] == PREVIEW}
    assert {pid for pid, c in preview_sessions.items() if c == second} == {"84890467", "84847292"}
    # cards run on their own sessions, never on a JSF one (concurrent use of one session gets a 302/500)
    assert len(card_cookies) == 5 and not any("jsf-" in c for c in card_cookies)

    assert len(notices) == 5
    assert [n.source_id for n in notices] == ["85010978", "84842168", "85018518", "84890467", "84847292"]
    assert [p for n in notices for p in validate_notice(n)] == []
    assert [c.state for c in checkpoints] == [{"started_from": "01-06-2026", "done": 3, "total": 5},
                                              {"started_from": "01-06-2026", "done": 5, "total": 5}]

    by_id = {n.source_id: n for n in notices}
    assert set(by_id) == set(PREVIEWS.values())

    gas = by_id["85010978"]
    assert gas.source == "smartpzp"
    assert gas.url == "https://portal.smartpzp.pl/umw/public/postepowanie?postepowanie=85010978"
    assert gas.title.startswith("Kompleksowa dostawa paliwa gazowego")
    assert gas.notice_type is NoticeType.TENDER
    assert gas.status is Status.AWARDED  # "Po wyborze najkorzystniejszej oferty"
    assert gas.buyer_name == "Urząd Miasta Wrocław"
    assert gas.buyer_country == "PL"
    assert gas.cpv_codes == ["09123000"]
    assert gas.procedure_type == "Przetarg nieograniczony"
    assert gas.published_at == datetime(2026, 6, 29, 10, 11, tzinfo=WARSAW)
    assert gas.deadline_at == datetime(2026, 7, 17, 12, 0, tzinfo=WARSAW)
    assert gas.value_amount == 20982253.07 and gas.value_currency == "PLN"
    assert gas.refs == {"procedure": "ZP/PN/32/2026/WKE", "smartpzp_tenant": "umw"}
    assert gas.language == "pl"
    assert gas.documents and gas.documents[0].title.startswith("D32_wke_informacja_o_ponownym_wyborze")
    assert gas.documents[0].url.startswith(gas.url)

    campaign = by_id["84890467"]  # from the second (pagination) page
    assert campaign.refs["bzp"] == "2026/BZP 00280868"
    assert campaign.cpv_codes == ["79342200"]
    assert campaign.value_amount == 33825.0
    assert campaign.deadline_at == datetime(2026, 6, 17, 10, 0, tzinfo=WARSAW)
    assert campaign.status is Status.UNKNOWN  # "W trakcie": left to the runner/deadline

    man = by_id["84842168"]  # no CPV on this card; deadline was extended on the card vs the list
    assert man.cpv_codes == []
    assert man.buyer_name == "Centrum Usług Informatycznych we Wrocławiu"
    assert man.procedure_type == "Zgodny z regulaminem otwarty"
    assert man.deadline_at == datetime(2026, 7, 13, 11, 0, tzinfo=WARSAW)
    assert man.value_amount is None and man.value_currency == "PLN"
    assert man.description.startswith("1. Przedmiotem zamówienia jest sukcesywna dostawa")


@respx.mock
async def test_empty_search_still_checkpoints():
    _mock_home()
    route = respx.post(BASE).mock(return_value=httpx.Response(200, text=EMPTY_SEARCH))

    notices, checkpoints = await run_sync(SmartPzp, datetime(2026, 10, 27, tzinfo=UTC))

    assert route.call_count == 1
    assert notices == []
    assert [c.state for c in checkpoints] == [{"started_from": "27-10-2026", "done": 0, "total": 0}]


@respx.mock
async def test_refresh_rereads_card_and_list_row():
    _mock_home()
    posts: list[dict[str, str]] = []
    respx.post(BASE).mock(side_effect=_ajax_router(posts, "search_by_number.xml"))
    _mock_cards()
    conn, client = make_connector(SmartPzp)
    try:
        n = await conn.refresh(StoredRef(
            source_id="84890467", url=_card_url("84890467"), refs={},
            raw={"row": {"number": "ZP/TP/31/2026/WPM", "status": "Ogłoszone", "buyer": "Urząd Miasta Wrocław",
                         "started": "09-06-2026 12:32"}},
        ))
    finally:
        await client.aclose()

    assert posts[0][f"{SEARCH_FORM}:numerPostepowania"] == "ZP/TP/31/2026/WPM"
    assert n is not None and validate_notice(n) == []
    assert n.source_id == "84890467"
    assert n.title.startswith("Realizacja kampanii wizerunkowej Wrocławia")
    assert n.raw["row"]["status"] == "W trakcie"  # picked up from the current list
    assert n.published_at == datetime(2026, 6, 9, 12, 32, tzinfo=WARSAW)


@respx.mock
async def test_refresh_unknown_id_returns_none():
    gone = f"{BASE}umw/public/postepowanie?postepowanie=1"
    respx.get(gone).mock(return_value=httpx.Response(302, headers={"Location": f"{BASE}umw"}))
    tenant_list = respx.get(f"{BASE}umw").mock(return_value=httpx.Response(200, text=load_text("smartpzp", "home.html")))
    _mock_home()
    conn, client = make_connector(SmartPzp)
    try:
        assert await conn.refresh(StoredRef(source_id="1", url=gone, refs={}, raw={})) is None
    finally:
        await client.aclose()
    assert not tenant_list.called  # the redirect itself says "gone"; not followed
