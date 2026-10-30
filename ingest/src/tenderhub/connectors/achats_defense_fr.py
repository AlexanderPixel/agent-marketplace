"""achats.defense.gouv.fr — purchasing portal of the French Ministry of the Armed Forces (MINARM).

The site is a Drupal 10 install with no public API: `/jsonapi` is 404, `?_format=json` is 406 and the
only RSS feed (`/rss.xml`) carries promoted "données essentielles" rows, not calls for tender. So this
connector reads the server-rendered HTML (checked 2026-10-30):

* `/les-appels-doffres?sort_by=field_datecloture_croissant&page=N` — every open consultation the ministry
  publishes on PLACE (marches-publics.gouv.fr), 10 cards per page, sortable only by closing date. Each card
  has the Drupal node id, title, publication date, closing date, procedure code and the PLACE link
  ("Candidater"). Cards render their node as `about="/node/N"` or `about="/index.php/node/N"`. The sort key
  (closing date) is not unique, so as a safety net against pager drift, when one pass yields fewer unique
  nodes than the "N Résultat(s)" total we also walk the descending sort until the union is complete.
* `/node/<nid>` — the consultation itself: PLACE reference + id, notice type (AAPC), procedure, dates,
  the issuing service (our buyer), departments, lots and CPV taxonomy links.
* `/taxonomy/term/<tid>` — a CPV term; its header carries the 8-digit code (cached in the checkpoint
  state, since the detail page only shows the label).
* `/nouvelles-demandes-dinformations?page=N` — requests for information (RFIs / sourcing). Cards have no
  dates, so new node ids are tracked in the checkpoint state and their detail page gives the response
  deadline, description and questionnaire PDF.

`/previsions-dachats` (2660 purchase forecasts) is deliberately not read: every entry carries the same
2023-11-29 date and a rolling "notification" date, i.e. it is a static 2023 import that never has
anything inside a `since` window, and walking its 266 pages would cost minutes per sync.

Listing cards only give a publication *date*; times are taken as midnight Europe/Paris, and closing dates
without a time as 23:59 Europe/Paris.

Speed: page 0 of each listing names its last page (`.fr-pagination__link--last`), so the remaining pages
are fetched concurrently; the RFI walk runs in the background while the consultation listing is read, and
detail pages + CPV terms are fetched concurrently (bounded, CPV terms de-duplicated across notices). The
shared rate limiter still caps the request rate at `requests_per_second`, so concurrency only removes the
idle round-trip time between requests.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from ..connector import Checkpoint, Connector, StoredRef
from ..http import HttpError
from ..models import Document, Notice, NoticeType, Status, absolute_url, normalize_cpv

BASE = "https://achats.defense.gouv.fr"
TENDERS = f"{BASE}/les-appels-doffres"
RFIS = f"{BASE}/nouvelles-demandes-dinformations"
NODE_URL = BASE + "/node/{}"
TERM_URL = BASE + "/taxonomy/term/{}"
PLACE_URL = "https://www.marches-publics.gouv.fr/?page=Entreprise.EntrepriseAdvancedSearch&AllCons&id={}&orgAcronyme={}"
PLACE_ORG = "g7h"  # MINARM's organisation acronym on PLACE (every card seen uses it)
SORTS = ("field_datecloture_croissant", "field_datecloture_decroissant")
MAX_PAGES = 100
CONCURRENCY = 8          # requests in flight; the shared rate limiter still spaces their start times
T = TypeVar("T")
R = TypeVar("R")
PARIS = ZoneInfo("Europe/Paris")
BUYER = "Ministère des Armées"

PROCEDURES = {
    "AOO": "Appel d'offres ouvert",
    "AOR": "Appel d'offres restreint",
    "PCN": "Procédure concurrentielle avec négociation",
    "MAPA": "Procédure adaptée",
    "DCO": "Dialogue compétitif",
    "SAD-A": "Système d'acquisition dynamique",
    "AUT": "Autre procédure",
}
NOTICE_TYPES = {
    "AAPC": NoticeType.TENDER,       # avis d'appel public à la concurrence
    "AAC": NoticeType.TENDER,
    "AR": NoticeType.MODIFICATION,   # avis rectificatif
    "AA": NoticeType.AWARD,          # avis d'attribution
    "AI": NoticeType.PLAN,           # avis d'information / préinformation
}

_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_TOTAL = re.compile(r"(\d+)\s*Résultat\(s\)")
_NODE = re.compile(r"^(?:/index\.php)?/node/(\d+)$")   # some cards say about="/index.php/node/N"
_TERM = re.compile(r"/taxonomy/term/(\d+)")
_PAGE = re.compile(r"[?&]page=(\d+)")


# ---------------------------------------------------------------- small helpers

def _clean(s: str | None) -> str | None:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s or None


def _text(n: Node | None) -> str | None:
    return _clean(n.text(separator=" ")) if n is not None else None


def _date(s: str | None) -> date | None:
    m = _DATE.search(s or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _published(d: date | None) -> datetime | None:
    return datetime.combine(d, time(), PARIS) if d else None


def _deadline(d: date | None) -> datetime | None:
    return datetime.combine(d, time(23, 59), PARIS) if d else None


def _split(s: str | None) -> list[str]:
    """'Gironde / Lot-et-Garonne' -> ['Gironde', 'Lot-et-Garonne']."""
    return [x for x in (_clean(p) for p in (s or "").split(" / ")) if x]


def _lot_line(lot: dict[str, str]) -> str:
    """{'numero du lot': '1', 'titre': 'Lot 1 Enveloppe', 'sujet': '...'} -> 'Lot 1 Enveloppe — ...'."""
    title = lot.get("titre") or ""
    if not re.match(r"(?i)lot\b", title):
        title = f"Lot {lot.get('numero du lot', '?')}: {title}".rstrip(": ")
    subject = lot.get("sujet")
    return f"{title} — {subject}" if subject and subject != lot.get("titre") else title


def _nid(n: Node) -> str | None:
    m = _NODE.match(n.attributes.get("about") or "")
    return m.group(1) if m else None


@dataclass
class Listing:
    cards: list[dict[str, Any]] = field(default_factory=list)
    total: int | None = None
    has_next: bool = False
    last_page: int | None = None   # from the pager's "dernière page" / numbered links, when present


def _pager_next(tree: HTMLParser) -> bool:
    a = tree.css_first('a[rel="next"]')
    return bool(a is not None and a.attributes.get("href"))


def _pager_last(tree: HTMLParser) -> int | None:
    pages = [int(m.group(1)) for a in tree.css("a.fr-pagination__link[href]")
             if (m := _PAGE.search(a.attributes.get("href") or ""))]
    return max(pages) if pages else None


def _new_listing(tree: HTMLParser) -> Listing:
    return Listing(has_next=_pager_next(tree), last_page=_pager_last(tree))


# ---------------------------------------------------------------- parsers (pure, unit-tested)

def parse_tender_listing(html: str) -> Listing:
    tree = HTMLParser(html)
    out = _new_listing(tree)
    m = _TOTAL.search(_text(tree.css_first(".view-header__title")) or "")
    out.total = int(m.group(1)) if m else None
    for card in tree.css('div[about*="/node/"]'):
        nid = _nid(card)
        if not nid or card.css_first(".fr-card") is None:
            continue
        txt = _clean(card.text(separator=" ")) or ""
        pub = re.search(r"Date de publication :\s*(\d{2}/\d{2}/\d{4})", txt)
        proc = re.search(r"Type de procédure :\s*(\S+)", txt)
        deadline = None
        icon = card.css_first(".fr-card__end .fr-icon-time-line")
        if icon is not None and icon.parent is not None:
            deadline = _text(icon.parent)
        place = next((a.attributes.get("href") for a in card.css("a[href]")
                      if "marches-publics.gouv.fr" in (a.attributes.get("href") or "")
                      and not (a.attributes.get("href") or "").startswith("mailto:")), None)
        badge = card.css_first(".fr-badge--purple-glycine")
        out.cards.append({
            "nid": nid,
            "title": _text(card.css_first(".fr-card__title")),
            "category": _text(badge),
            "published": pub.group(1) if pub else None,
            "deadline": deadline,
            "procedure": proc.group(1) if proc else None,
            "platform_url": absolute_url(place),
        })
    return out


def parse_rfi_listing(html: str) -> Listing:
    tree = HTMLParser(html)
    out = _new_listing(tree)
    for card in tree.css('div[about*="/node/"]'):
        nid = _nid(card)
        if nid and nid not in {c["nid"] for c in out.cards}:
            out.cards.append({"nid": nid, "title": _text(card.css_first(".fr-card__title"))})
    return out


def _lots(value: Node) -> list[dict[str, str]]:
    lots = []
    for p in value.css(".paragraph--type--lot-marche-public"):
        lot: dict[str, str] = {}
        for label in p.css(".visually-hidden"):
            box = label.parent
            if box is None:
                continue
            vals = [t for t in (_text(c) for c in box.iter() if "visually-hidden" not in (c.attributes.get("class") or ""))
                    if t]
            if vals:
                lot[(_text(label) or "").lower()] = " / ".join(vals)
        if lot:
            lots.append(lot)
    return lots


def parse_tender_detail(html: str) -> dict[str, Any] | None:
    """Fields of a consultation page; None if the page is not a consultation."""
    tree = HTMLParser(html)
    fields: dict[str, Node] = {}
    for col in tree.css("div.fr-col-4"):
        label, value = col.css_first("span.fr-text--bold"), col.css_first("div.fr-ml-7v")
        if label is not None and value is not None:
            fields.setdefault(_text(label) or "", value)
    if "Reference ID" not in fields and "Date de clôture" not in fields:
        return None

    def f(name: str) -> str | None:
        return _text(fields.get(name))

    cpv = fields.get("CPV")
    terms: dict[str, str] = {}
    if cpv is not None:
        for a in cpv.css("a[href]"):
            m = _TERM.search(a.attributes.get("href") or "")
            if m:
                terms[m.group(1)] = (_text(a) or "").rstrip(".")
    lots_node = fields.get("Lots")
    return {
        "kind": "tender",
        "title": _text(tree.css_first(".fr-highlight-title")),
        "reference": f("Reference"),
        "place_id": f("Reference ID"),
        "type": f("Type"),
        "procedure": f("Type de procédure"),
        "published": f("Date de mise en ligne"),
        "deadline": f("Date de clôture"),
        "cpv_terms": terms,
        "segments": _split(f("Segment d'achat")),
        "service": f("Service"),
        "social_clauses": f("Clauses sociales"),
        "categories": _split(f("Catégories de marché")),
        "entity": f("Entité public"),
        "departments": _split(f("Département")),
        "lots": _lots(lots_node) if lots_node is not None else [],
    }


def parse_rfi_detail(html: str) -> dict[str, Any] | None:
    tree = HTMLParser(html)
    card = tree.css_first('div[about*="/node/"]')
    if card is None:
        return None
    txt = _clean(card.text(separator=" ")) or ""

    def after_icon(cls: str) -> str | None:
        icon = card.css_first(f".fr-card__end .{cls}")
        box = icon.parent if icon is not None else None
        nxt = box.next if box is not None else None
        while nxt is not None and nxt.tag != "div":
            nxt = nxt.next
        return _text(nxt)

    deadline = re.search(r"Date limite de remise\s*:\s*(\d{2}/\d{2}/\d{4})", txt)
    proc = re.search(r"Type de procédure :\s*(.*?)\s*Code CPV", txt)
    cpv = re.search(r"Code CPV :\s*(.*?)\s*(?:Fichier pdf :|$)", txt)
    docs = []
    for a in card.css("a[href]"):
        href = a.attributes.get("href") or ""
        if "/sites/default/files/" in href:
            docs.append({"url": absolute_url(href, BASE + "/"), "title": _text(a)})
    return {
        "kind": "rfi",
        "title": _text(card.css_first(".fr-card__title")),
        "segment": _text(card.css_first(".fr-badge--purple-glycine")),
        "description": after_icon("fr-icon-article-line"),
        "location": after_icon("fr-icon-map-pin-2-fill"),
        "deadline": deadline.group(1) if deadline else None,
        "procedure": _clean(proc.group(1)) if proc else None,
        "cpv_labels": [x.rstrip(".") for x in _split(cpv.group(1) if cpv else None)],
        "documents": [d for d in docs if d["url"]],
    }


def parse_term_cpv(html: str) -> str | None:
    """A CPV taxonomy term page; its header's first div holds the 8-digit code."""
    tree = HTMLParser(html)
    head = tree.css_first('div[about*="/taxonomy/term/"]')
    if head is None:
        return None
    for d in head.css("div"):
        t = _clean(d.text(deep=False))
        if t and re.fullmatch(r"\d{8}(-\d)?", t):
            return normalize_cpv(t)
    return None


# ---------------------------------------------------------------- connector

class AchatsDefenseFr(Connector):
    name = "achats_defense_fr"
    title = "achats.defense.gouv.fr — Ministère des Armées"
    countries = ("FR",)
    homepage = "https://achats.defense.gouv.fr"
    kind = "defence_agency"
    poll_interval = timedelta(hours=3)       # a handful of new consultations per working day
    first_sync_lookback = timedelta(days=7)
    requests_per_second = 4.0                # small Drupal site (~0.4 s/page); pages are fetched concurrently
    supports_refresh = True

    def __init__(self, ctx):
        super().__init__(ctx)
        self._cpv: dict[str, str] = {}
        self._cpv_pending: dict[str, asyncio.Future[str]] = {}

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        self._cpv = dict(state.get("cpv_terms") or {})
        rfi_seen: set[str] = set(state.get("rfi_seen") or [])
        since_day = since.astimezone(PARIS).date()

        # The RFI listing + new RFI details are independent of the consultations: run them in the background.
        rfis = asyncio.ensure_future(self._new_rfis(rfi_seen))
        try:
            fresh = [c for c in await self._tender_cards()
                     if (pub := _date(c.get("published"))) is None or pub >= since_day]
            async for n in self._imap(lambda c: self._tender(c["nid"], c), fresh):
                if n is not None:
                    yield n
            yield Checkpoint(self._state(rfi_seen))

            for nid, n in await rfis:
                rfi_seen.add(nid)
                if n is not None and (n.deadline_at is None or n.deadline_at >= since):
                    yield n
            yield Checkpoint(self._state(rfi_seen))
        finally:
            if not rfis.done():
                rfis.cancel()
                await asyncio.gather(rfis, return_exceptions=True)

    async def refresh(self, ref: StoredRef) -> Notice | None:
        nid = ref.source_id
        self._cpv.update(ref.raw.get("cpv_codes") or {})
        try:
            if ref.raw.get("kind") == "rfi":
                return await self._rfi(nid)
            return await self._tender(nid, ref.raw.get("card") or {}, refreshing=True)
        except HttpError as e:
            if e.status in (403, 404, 410):
                return None
            raise

    # -- concurrency

    async def _imap(self, fn: Callable[[T], Awaitable[R]], items: Iterable[T]) -> AsyncIterator[R]:
        """Run fn over items with at most CONCURRENCY in flight; yield results as they complete."""
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(x: T) -> R:
            async with sem:
                return await fn(x)

        tasks = [asyncio.ensure_future(one(x)) for x in items]
        try:
            for fut in asyncio.as_completed(tasks):
                yield await fut
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _all(self, fn: Callable[[T], Awaitable[R]], items: Iterable[T]) -> list[R]:
        return [r async for r in self._imap(fn, items)]

    # -- listing walks

    async def _walk(self, url: str, params: dict[str, Any], parse: Callable[[str], Listing]) -> list[Listing]:
        """Page 0, then every remaining page at once (the pager names the last page); sequential fallback."""
        async def page(p: int) -> Listing:
            return parse(await self.http.get_text(url, params={**params, "page": p}))

        first = await page(0)
        pages = [first]
        if not first.cards or not first.has_next:
            return pages
        last = first.last_page
        if last is None and first.total is not None:   # no pager links: estimate from "N Résultat(s)"
            last = math.ceil(first.total / len(first.cards)) - 1
        last = last or 0
        last = min(last, MAX_PAGES - 1)
        if last >= 1:
            pages += await asyncio.gather(*(page(p) for p in range(1, last + 1)))
        # the pager did not say (or the list grew meanwhile): continue one page at a time
        p = last
        while pages[-1].cards and pages[-1].has_next and p < MAX_PAGES - 1:
            p += 1
            pages.append(await page(p))
        return pages

    async def _tender_cards(self) -> list[dict[str, Any]]:
        cards: dict[str, dict[str, Any]] = {}
        total: int | None = None
        for sort in SORTS:
            for listing in await self._walk(TENDERS, {"sort_by": sort}, parse_tender_listing):
                total = listing.total if listing.total is not None else total
                for c in listing.cards:
                    cards.setdefault(c["nid"], c)
            if total is None or len(cards) >= total:
                break
            self.log.info("tender pager unstable: %d of %d after %s, walking %s", len(cards), total, sort, SORTS[-1])
        return list(cards.values())

    async def _rfi_cards(self) -> list[dict[str, Any]]:
        cards: dict[str, dict[str, Any]] = {}
        for listing in await self._walk(RFIS, {}, parse_rfi_listing):
            for c in listing.cards:
                cards.setdefault(c["nid"], c)
        return list(cards.values())

    async def _new_rfis(self, seen: set[str]) -> list[tuple[str, Notice | None]]:
        new = [c["nid"] for c in await self._rfi_cards() if c["nid"] not in seen]

        async def one(nid: str) -> tuple[str, Notice | None]:
            return nid, await self._rfi(nid)

        return sorted(await self._all(one, new), key=lambda x: int(x[0]))

    # -- detail pages

    async def _tender(self, nid: str, card: dict[str, Any], refreshing: bool = False) -> Notice | None:
        detail = parse_tender_detail(await self.http.get_text(NODE_URL.format(nid)))
        if detail is None:
            if refreshing:
                return None         # the node is no longer a consultation page
            detail = {"kind": "tender"}   # keep what the listing card says
        tids = list(detail.get("cpv_terms") or {})
        found = await asyncio.gather(*(self._cpv_code(t) for t in tids))
        codes = {t: c for t, c in zip(tids, found, strict=True) if c}
        return self.tender_notice(nid, card, detail, codes)

    async def _rfi(self, nid: str) -> Notice | None:
        url = NODE_URL.format(nid)
        detail = parse_rfi_detail(await self.http.get_text(url))
        if detail is None or not detail.get("title"):
            return None
        return self.rfi_notice(nid, detail)

    async def _cpv_code(self, tid: str) -> str | None:
        """8-digit code of a CPV term; concurrent callers asking for the same term share one request."""
        if tid in self._cpv:
            return self._cpv[tid] or None
        fut = self._cpv_pending.get(tid)
        if fut is None:
            fut = self._cpv_pending[tid] = asyncio.ensure_future(self._fetch_term(tid))
        try:
            return await asyncio.shield(fut) or None
        finally:
            if fut.done():
                self._cpv_pending.pop(tid, None)

    async def _fetch_term(self, tid: str) -> str:
        try:
            code = parse_term_cpv(await self.http.get_text(TERM_URL.format(tid))) or ""
        except HttpError as e:
            if e.status not in (403, 404, 410):
                raise
            code = ""
        self._cpv[tid] = code
        return code

    def _state(self, rfi_seen: set[str]) -> dict[str, Any]:
        return {"cpv_terms": dict(self._cpv), "rfi_seen": sorted(rfi_seen, key=int)}

    # -- mappers (pure)

    def tender_notice(self, nid: str, card: dict[str, Any], d: dict[str, Any],
                      cpv_codes: dict[str, str]) -> Notice:
        now = self.ctx.now()
        deadline = _deadline(_date(d.get("deadline") or card.get("deadline")))
        published = _published(_date(d.get("published") or card.get("published")))
        place_id = d.get("place_id")
        platform = card.get("platform_url") or (PLACE_URL.format(place_id, PLACE_ORG) if place_id else None)
        if not place_id and platform:
            m = re.search(r"[?&]id=(\d+)", platform)
            place_id = m.group(1) if m else None
        code = d.get("procedure") or card.get("procedure")
        label = PROCEDURES.get(code or "")
        lots = d.get("lots") or []
        description = "\n".join(_lot_line(x) for x in lots) or None
        raw_card = {k: v for k, v in card.items() if v is not None}
        raw = {**{k: v for k, v in d.items() if v not in (None, [], {}, "")}, "card": raw_card,
               "cpv_codes": cpv_codes, "nid": nid, "kind": "tender", "platform_url": platform}
        return Notice(
            source=self.name,
            source_id=nid,
            url=NODE_URL.format(nid),
            title=d.get("title") or card.get("title") or d.get("reference") or nid,
            notice_type=NOTICE_TYPES.get(d.get("type") or "", NoticeType.TENDER),
            status=Status.OPEN if deadline is None or deadline >= now else Status.CLOSED,
            description=description,
            buyer_name=d.get("service") or d.get("entity") or BUYER,
            buyer_country="FR",
            cpv_codes=list(dict.fromkeys(cpv_codes.values())),
            procedure_type=f"{label} ({code})" if label else code,
            published_at=published,
            deadline_at=deadline,
            platform_url=platform,
            language="fr",
            refs={k: v for k, v in {"place": place_id, "procedure": d.get("reference")}.items() if v},
            raw=raw,
        )

    def rfi_notice(self, nid: str, d: dict[str, Any]) -> Notice:
        deadline = _deadline(_date(d.get("deadline")))
        docs = [Document(x["url"], title=x.get("title"), kind="questionnaire") for x in d.get("documents") or []]
        return Notice(
            source=self.name,
            source_id=nid,
            url=NODE_URL.format(nid),
            title=d["title"],
            notice_type=NoticeType.PLAN,   # demande d'informations = market consultation / sourcing
            status=Status.OPEN if deadline is None or deadline >= self.ctx.now() else Status.CLOSED,
            description=d.get("description"),
            buyer_name=BUYER,
            buyer_country="FR",
            procedure_type=d.get("procedure") or "Demande d'information",
            deadline_at=deadline,
            language="fr",
            documents=docs,
            raw={**{k: v for k, v in d.items() if v not in (None, [], "")}, "nid": nid},
        )
