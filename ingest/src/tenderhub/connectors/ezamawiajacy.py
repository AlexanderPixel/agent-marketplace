"""eZamawiający (Marketplanet) — multi-tenant e-procurement platform, one subdomain per buyer.

`<tenant>.ezamawiajacy.pl` hosts the proceedings of one buyer (hospitals, Wody Polskie, Poczta Polska,
municipalities, military academies, ...). There is no API, feed or cross-tenant listing (Marketplanet's
OnePlace aggregator uses its own ids and does not link back), so this connector reads each tenant's
server-rendered public list:

    GET https://<tenant>/pn/<SLUG>/demand/notice/public/current/list
        ?GD_sortCol=5&GD_sortOrder=desc&GD_pagesize=50&GD_pagestart=<row offset>

"current" = notices whose submission deadline has not passed (PZP and below-threshold proceedings),
sorted newest-published first, so a tenant is read until rows are older than the cutoff. For every new or
changed row the notice card `/pn/<SLUG>/demand/notice/public/<id>/details` is read for the procedure
type, description and document list; the same card is what `refresh` re-reads.

The `<SLUG>` path segment is case-sensitive and differs from the subdomain for some tenants
(`poczta-polska` -> `pp`), so it comes from the tenant list's example URL or is discovered once from the
tenant home page and cached in `state`. Unknown subdomains answer 503 (wildcard DNS), which the shared
client would retry for a minute, so a tenant is probed once with a single un-retried request before use.

Coverage: ~1086 tenants are too many to poll hourly (each list page is ~140 kB of uncompressed HTML).
Each run reads the HEAD most active tenants (from the bundled tenant list, ~45% of all historical notices)
plus the next BATCH tenants of the long tail in rotation (cursor in `state`), so the whole tail is visited
about every 7 hours — well inside typical submission periods, and notices stay on the "current" list until
their deadline. Per tenant the cutoff reaches back to that tenant's previous visit, so rotation gaps lose
nothing.

Speed: every tenant lives on the same Marketplanet host, so the shared per-connector rate limit is the real
ceiling and the connector's job is to keep it saturated with as few requests as possible:
  * WORKERS tenants are crawled at once from a shared queue (no chunk barriers: a slow tenant never stalls
    the others), and the notice cards of one list page are fetched concurrently (CARD_PARALLEL in flight);
  * notices are streamed to the runner as soon as their card arrives; `seen`/`visits` are only advanced
    after the runner has stored them, so a checkpoint never skips an unsaved notice;
  * a tenant's first list request doubles as its liveness probe (no extra request per tenant), and only
    tenants whose slug is not in the example URL cost one extra HomeServlet request — once, then cached;
  * unchanged rows (fingerprint in `state["seen"]`) never trigger a card request.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..connector import Checkpoint, Connector, StoredRef
from ..data import load_tenants
from ..http import HttpError
from ..models import Document, Notice, NoticeType

DOMAIN = "ezamawiajacy.pl"
WARSAW = ZoneInfo("Europe/Warsaw")
LIST_PATH = "/pn/{slug}/demand/notice/public/current/list"
DETAILS_PATH = "/pn/{slug}/demand/notice/public/{id}/details"
SORT_PUBLISHED = "5"        # GD_sortCol id of the "Termin publikacji" column
PAGE_SIZE = 50              # the grid accepts 5, 10, 25, 50, 100
MAX_PAGES = 10              # per tenant per run; a tenant never has 500 notices newer than its cutoff
HEAD = 40                   # most active tenants, read every run
BATCH = 150                 # long-tail tenants read per run, in rotation (~7 runs for the whole tail)
WORKERS = 10                # tenants crawled concurrently (all requests still share one rate limiter)
CARD_PARALLEL = 12          # notice cards in flight across all tenants
CHECKPOINT_EVERY = 3.0      # seconds between checkpoints while streaming
DEAD_FOR = 3 * 86400        # skip a tenant that did not answer for this long (seconds)
SEEN_FOR = 10 * 86400       # remember yielded rows this long to avoid re-reading unchanged cards

PLAN_MODES = ("konsultacje rynkowe", "badanie rynku", "dialog techniczny")  # market research -> PLAN

_SLUG = re.compile(r"/pn/([A-Za-z0-9_.\-]+)/")
_DATETIME = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{2}):(\d{2}))?")
_COUNTER = re.compile(r"Pozycje\D*?(\d+)\D+?(\d+)\D+?(\d+)")


def _clean(s: str | None) -> str | None:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if s and s not in ("-", "--") else None


def _dt(v: str | None) -> datetime | None:
    """'2026-11-03 09:30' (Warsaw local time) -> aware datetime; a bare date means end of that day."""
    m = _DATETIME.search(v or "")
    if not m:
        return None
    y, mo, d, h, mi = m.groups()
    if h is None:
        return datetime(int(y), int(mo), int(d), 23, 59, tzinfo=WARSAW)
    return datetime(int(y), int(mo), int(d), int(h), int(mi), tzinfo=WARSAW)


def _sub(host: str) -> str:
    return host.removesuffix("." + DOMAIN)


def _fingerprint(row: dict[str, Any]) -> str:
    key = "|".join(str(row.get(k) or "") for k in ("number", "name", "realizationType", "stageEndDate"))
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def parse_slug(html: str) -> str | None:
    """Tenant slug from any page's menu links: '/pn/SPWSZ/demand/...' -> 'SPWSZ'."""
    slugs = [s for s in _SLUG.findall(html) if s.lower() not in ("demand", "homeservlet")]
    return max(set(slugs), key=slugs.count) if slugs else None


def parse_list(html: str) -> tuple[list[dict[str, Any]], int | None]:
    """Public grid -> ([{id, number, name, organization, realizationType, type, publicationDate, ...}], total).
    total is None when the page is not a notice list (wrong slug, login page)."""
    tree = HTMLParser(html)
    table = tree.css_first("table#publicList")
    m = _COUNTER.search(tree.css_first("p.mp_controls").text() if tree.css_first("p.mp_controls") else "")
    total = int(m.group(3)) if m else None
    if table is None:      # "Brak wyników" = an empty list; anything else is not a notice list at all
        return [], (total or 0) if tree.css_first(".no_content_info") or m else None
    header = table.css_first("tr")
    cols = [th.attributes.get("data-mpgrid-id") or f"col{i}" for i, th in enumerate(header.css("th"))] if header else []
    rows = []
    for tr in table.css("tr.dataRow"):
        cells = [_clean(td.text()) for td in tr.css("td")]
        row = {c: v for c, v in zip(cols, cells, strict=False) if v}
        row["id"] = tr.attributes.get("id")
        if row["id"] and row["id"].isdigit():
            rows.append(row)
    return rows, total


def parse_details(html: str) -> dict[str, Any] | None:
    """Notice card -> {title, info: {label: value}, fields: {label: value}, messages: [...], documents: [...]}."""
    tree = HTMLParser(html)
    info_list = tree.css_first("ul.mp_infolist")
    if info_list is None:
        return None
    h2 = tree.css_first(".box-header h2")
    info = {}
    for li in info_list.css("li"):
        parts = [_clean(x.text()) for x in li.css(".col")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            info[parts[0]] = parts[1]
    fields = {}
    for fs in tree.css("fieldset.mp_common_field"):
        label, section = fs.css_first(".h4span"), fs.css_first("section")
        if label is None or section is None:
            continue
        k, v = _clean(label.text()), _clean(section.text())
        if k and v and not fs.css_first("input, textarea, select"):
            fields[k.rstrip(":")] = v
    messages = [m for m in (_clean(p.text()) for p in tree.css("p.message")) if m]
    documents = []
    for box in tree.css("div.mp_box"):
        rows = box.css("tr.fileDataRow")
        head = box.css_first(".header h2")
        if not rows or head is None:
            continue
        section = _clean(head.text())
        for tr in rows:
            fid = (tr.attributes.get("id") or "").lstrip("_")
            name = _clean(tr.css_first("td.text").text()) if tr.css_first("td.text") else None
            if not fid.isdigit() or not name:      # "_-2" is the pseudo-file "Dane kontaktowe"
                continue
            date = tr.css_first("td.date-time")
            documents.append({"id": fid, "name": name, "section": section,
                              "date": _clean(date.text()) if date else None})
    return {"title": _clean(h2.text()) if h2 else None, "info": info, "fields": fields,
            "messages": messages, "documents": documents}


def _doc_kind(section: str | None) -> str:
    s = (section or "").lower()
    if "zmian" in s:
        return "amendment"
    if "udostępnion" in s:
        return "shared"
    return "procurement_documents"


class Ezamawiajacy(Connector):
    name = "ezamawiajacy"
    title = "eZamawiający (Marketplanet) — buyer subdomains *.ezamawiajacy.pl"
    countries = ("PL",)
    homepage = "https://oneplace.marketplanet.pl"
    kind = "platform"
    poll_interval = timedelta(hours=1)
    first_sync_lookback = timedelta(days=7)
    requests_per_second = 5.0   # one shared host; 5 rps is the core's politeness cap
    supports_refresh = True

    # --- tenants ---------------------------------------------------------------------------------

    def tenants(self) -> list[dict[str, Any]]:
        """Bundled tenant list, most active first: [{tenant, notices, example_url, buyer}, ...]."""
        return [t for t in load_tenants("ezamawiajacy") if t.get("tenant", "").endswith("." + DOMAIN)]

    def plan(self, tenants: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
        """HEAD most active tenants + the next BATCH of the rotating tail; advances state['pos']."""
        head, tail = tenants[:HEAD], tenants[HEAD:]
        if not tail:
            return head
        pos = int(state.get("pos", 0)) % len(tail)
        batch = (tail[pos:] + tail[:pos])[:BATCH]
        state["pos"] = (pos + len(batch)) % len(tail)
        return head + batch

    async def _probe(self, url: str, **kw: Any) -> httpx.Response | None:
        """One request without the shared client's retries (dead tenants answer 503 forever).
        Uses the shared client and rate limiter; returns None when the tenant does not answer."""
        await self.http.limiter.wait()
        self.http.requests += 1
        try:
            r = await self.http.client.get(url, **kw)
        except httpx.HTTPError as e:
            self.log.info("probe %s failed: %s", url, e)
            return None
        if r.status_code >= 400 or not (r.url.host or "").endswith("." + DOMAIN) or "/app/login" in r.url.path:
            self.log.info("probe %s -> %s %s", url, r.status_code, r.url)
            return None
        return r

    def _dead(self, sub: str, st: dict[str, Any]) -> None:
        st["dead"][sub] = int(time.time()) + DEAD_FOR
        st["slugs"].pop(sub, None)

    async def _slug(self, t: dict[str, Any], st: dict[str, Any]) -> tuple[str | None, bool]:
        """(slug, verified). A slug from the example URL is unverified until its list answers."""
        sub = _sub(t["tenant"])
        if slug := st["slugs"].get(sub):
            return slug, True
        if slug := parse_slug(t.get("example_url") or ""):
            return slug, False
        r = await self._probe(f"https://{t['tenant']}/servlet/HomeServlet")
        slug = parse_slug(r.text) if r is not None else None
        if not slug:
            self._dead(sub, st)
            return None, False
        st["slugs"][sub] = slug
        return slug, True

    def _is_alias(self, sub: str, slug: str, state: dict[str, Any]) -> bool:
        """Some buyers answer on two subdomains (cpk / portpolska -> slug 'cpk'); read only the first-ranked."""
        owner = self._owners.setdefault(slug.lower(), sub)
        if owner != sub:
            state["alias"][sub] = owner
            return True
        return False

    # --- sync ------------------------------------------------------------------------------------

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        now = time.time()
        st: dict[str, Any] = {
            "origin": state.get("origin") or since.timestamp(),
            "pos": state.get("pos", 0),
            "slugs": dict(state.get("slugs") or {}),
            "dead": {k: v for k, v in (state.get("dead") or {}).items() if v > now},
            "visits": dict(state.get("visits") or {}),
            "seen": {k: v for k, v in (state.get("seen") or {}).items() if v[1] > now - SEEN_FOR},
            "alias": dict(state.get("alias") or {}),
        }
        everyone = self.tenants()
        self._owners: dict[str, str] = {}
        for t in everyone:   # slug -> highest-ranked subdomain using it
            sub = _sub(t["tenant"])
            if slug := st["slugs"].get(sub) or parse_slug(t.get("example_url") or ""):
                self._owners.setdefault(slug.lower(), sub)
        skip = st["dead"].keys() | st["alias"].keys()
        tenants = [t for t in self.plan(everyone, st) if _sub(t["tenant"]) not in skip]
        if not tenants:
            yield Checkpoint(dict(st))
            return

        overlap = self.overlap.total_seconds()
        self._cards = asyncio.Semaphore(CARD_PARALLEL)
        out: asyncio.Queue[tuple[str, str, Any]] = asyncio.Queue()
        todo = iter(tenants)            # shared by the workers; next() never awaits, so no lock needed

        async def worker() -> None:
            for t in todo:
                sub = _sub(t["tenant"])
                try:
                    started = await self._sync_tenant(t, since, st, overlap, out.put_nowait)
                except (HttpError, httpx.HTTPError) as e:
                    out.put_nowait(("failed", sub, e))
                except Exception as e:  # noqa: BLE001 — re-raised by the consumer
                    out.put_nowait(("crash", sub, e))
                    return
                else:
                    out.put_nowait(("done", sub, started))

        tasks = [asyncio.create_task(worker()) for _ in range(min(WORKERS, len(tenants)))]
        remaining, ok, failed, last_cp = len(tenants), 0, 0, time.monotonic()
        try:
            while remaining:
                kind, sub, payload = await out.get()
                if kind == "notice":
                    n, key, mark = payload
                    yield n
                    if mark:                         # stored by the runner -> safe to skip next time
                        st["seen"][key] = mark
                    continue
                remaining -= 1
                if kind == "crash":
                    raise payload
                if kind == "failed":
                    failed += 1
                    self.log.warning("tenant %s failed: %s", sub, str(payload)[:200])
                    continue
                ok += 1
                if payload is not None:              # all of this tenant's notices are already yielded
                    st["visits"][sub] = payload
                if time.monotonic() - last_cp >= CHECKPOINT_EVERY:
                    last_cp = time.monotonic()
                    yield Checkpoint(dict(st))
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        yield Checkpoint(dict(st))
        if failed and not ok:
            raise RuntimeError(f"all {failed} ezamawiajacy tenants failed")

    async def _sync_tenant(self, t: dict[str, Any], since: datetime, st: dict[str, Any], overlap: float,
                           emit: Any) -> int | None:
        """Read one tenant's list newest-first down to its cutoff; emit ("notice", sub, (n, key, mark)).
        Returns the visit timestamp, or None when the tenant was skipped (alias / dead)."""
        host, sub = t["tenant"], _sub(t["tenant"])
        known = st["slugs"].get(sub) or parse_slug(t.get("example_url") or "")
        if known and self._is_alias(sub, known, st):
            return None
        slug, verified = await self._slug(t, st)
        if not slug or self._is_alias(sub, slug, st):
            return None
        started = int(time.time())
        last = st["visits"].get(sub)
        cutoff = min(since.timestamp(), (last - overlap) if last else st["origin"])
        url = f"https://{host}{LIST_PATH.format(slug=slug)}"
        for page in range(MAX_PAGES):
            params = {"GD_sortCol": SORT_PUBLISHED, "GD_sortOrder": "desc", "GD_pagesize": PAGE_SIZE,
                      "GD_pagestart": page * PAGE_SIZE}
            if verified:
                html = await self.http.get_text(url, params=params)
            else:                       # first visit: the list request is also the liveness probe
                r = await self._probe(url, params=params)
                html = r.text if r is not None else ""
            rows, total = parse_list(html)
            if not verified:
                if total is None:
                    self._dead(sub, st)
                    return None
                st["slugs"][sub], verified = slug, True
            older = False
            todo = []
            for row in rows:
                pub = _dt(row.get("publicationDate"))
                if pub is not None and pub.timestamp() < cutoff:
                    older = True
                    break
                fp = _fingerprint(row)
                seen = st["seen"].get(row["id"])
                if not (seen and seen[0] == fp):
                    todo.append((row, fp, pub.timestamp() if pub else started))
            cards = await asyncio.gather(*(self._read(t, slug, row) for row, _, _ in todo))
            for (row, fp, ts), (n, complete) in zip(todo, cards, strict=True):
                emit(("notice", sub, (n, row["id"], [fp, ts] if complete else None)))
            if older or len(rows) < PAGE_SIZE or (total is not None and (page + 1) * PAGE_SIZE >= total):
                break
        return started

    async def _read(self, t: dict[str, Any], slug: str, row: dict[str, Any]) -> tuple[Notice, bool]:
        """Notice card -> (notice, complete). A card that fails still yields the row-only notice, but is
        not marked seen so the next run retries it."""
        url = f"https://{t['tenant']}{DETAILS_PATH.format(slug=slug, id=row['id'])}"
        details = None
        async with self._cards:
            try:
                r = await self.http.get(url)
            except HttpError as e:
                self.log.info("card %s failed: %s", url, str(e)[:120])
                r = None
        if r is not None:
            details = None if "/app/login" in r.url.path else parse_details(r.text)
            if details is None:
                self.log.info("no public card for %s (%s)", url, r.url)
        return self.to_notice(t["tenant"], slug, row, details, buyer=t.get("buyer")), r is not None

    # --- refresh ---------------------------------------------------------------------------------

    async def refresh(self, ref: StoredRef) -> Notice | None:
        raw = ref.raw or {}
        host, slug, row = raw.get("tenant"), raw.get("slug"), dict(raw.get("row") or {})
        if not (host and slug and row.get("id")):
            return None
        r = await self.http.get(f"https://{host}{DETAILS_PATH.format(slug=slug, id=row['id'])}")
        if "/app/login" in r.url.path:
            return None
        details = parse_details(r.text)
        if details is None:
            return None
        return self.to_notice(host, slug, row, details, buyer=raw.get("tenant_buyer"))

    # --- mapping ---------------------------------------------------------------------------------

    def to_notice(self, host: str, slug: str, row: dict[str, Any], details: dict[str, Any] | None,
                  buyer: str | None = None) -> Notice:
        d = details or {"info": {}, "fields": {}, "messages": [], "documents": []}
        info, fields = d["info"], d["fields"]
        url = f"https://{host}{DETAILS_PATH.format(slug=slug, id=row['id'])}"
        mode = info.get("Tryb udzielania zamówienia") or row.get("realizationType")
        kind = info.get("Typ postępowania")
        number = info.get("Numer postępowania") or row.get("number")
        is_plan = any(p in f"{mode or ''} {kind or ''}".lower() for p in PLAN_MODES)
        published = _dt(fields.get("Publikacja w strefie publicznej")) or _dt(row.get("publicationDate"))
        deadline = _dt(fields.get("Data składania ofert/opracowań studialnych")) or _dt(row.get("stageEndDate"))
        documents = [Document(url, title=doc["name"], kind=_doc_kind(doc["section"])) for doc in d["documents"]]
        return Notice(
            source=self.name,
            source_id=row["id"],          # notice ids are one platform-wide sequence
            url=url,
            title=d.get("title") or row.get("name") or number or row["id"],
            notice_type=NoticeType.PLAN if is_plan else NoticeType.TENDER,
            description=fields.get("Opis przedmiotu postępowania"),
            buyer_name=row.get("organization") or buyer,
            buyer_country="PL",
            procedure_type=mode,
            published_at=published,
            deadline_at=deadline,
            platform_url=url,
            language="pl",
            documents=documents,
            refs={"procedure": number} if number else {},
            raw={"tenant": host, "slug": slug, "tenant_buyer": buyer, "row": row, "procedure_kind": kind,
                 "order_type": fields.get("Rodzaj zamówienia") or row.get("type"),
                 "opening_at": fields.get("Data otwarcia ofert/opracowań studialnych"),
                 "messages": d["messages"], "documents": d["documents"]},
        )
