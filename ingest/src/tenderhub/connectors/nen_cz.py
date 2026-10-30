"""Czechia — Věstník veřejných zakázek (VVZ), the national gazette fed by NEN and the other Czech e-tools.

Every statutory notice of a Czech procurement (above- and below-EU-threshold, whichever e-tool the buyer
uses — NEN, Tender arena, E-ZAK, ...) is published in the VVZ. Its public React frontend
(https://vvz.nipez.cz) reads an anonymous JSON API that we use directly:

* list:   GET https://api.vvz.nipez.cz/api/submissions/search?formGroup=vz&form=vz
              &workflowPlace=UVEREJNENO_VVZ&data.datumUverejneniVvz[gte]=<iso datetime>
              &order[data.datumUverejneniVvz]=ASC&page=N&limit=M
          -> summary records (form number, contract number, title, buyer name + IČO, form type,
             publication time, tender deadline, TED number).
* detail: GET https://api.vvz.nipez.cz/api/submissions/children/search?submission=<uuid>
          -> the full eForms notice as JSON keyed by BT codes (CPV, buyer address, procedure type,
             values, lot deadlines, document / submission URLs, NIPEZ "RVZ" procedure id).

The list has no stable tie-breaker, so we page with a keyset cursor on the publication timestamp
(restarting at page 1 from the last seen timestamp; same-timestamp overlap is de-duplicated).
The children endpoint accepts exactly one submission per request, so speed comes from concurrency:
the next list page is prefetched while the current page's details download in parallel (bounded by
DETAIL_CONCURRENCY and the shared rate limiter), and notices whose list record is unchanged since an
earlier run (kept in the checkpoint as variableId -> fingerprint) are skipped without a detail request.
NEN itself (nen.nipez.cz) only exposes a server-driven widget API; its notices reach us via the VVZ.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

from dateutil.parser import isoparse

from ..connector import Checkpoint, Connector, Context, StoredRef
from ..countries import iso3_to_iso2, lang3_to_lang2
from ..http import HttpError
from ..models import Document, Notice, NoticeType, Status, absolute_url, normalize_cpv

API = "https://api.vvz.nipez.cz"
SEARCH = f"{API}/api/submissions/search"
CHILDREN = f"{API}/api/submissions/children/search"
NOTICE_URL = "https://vvz.nipez.cz/vyhledat-formular/{id}"
PAGE_SIZE = 100
DETAIL_CONCURRENCY = 8
PUBLISHED = "data.datumUverejneniVvz"

# Czech form numbers (`druhFormulare`) are the eForms notice subtypes plus a few national forms.
_PLAN = {"E1", "4", "5", "6", "7", "8", "9"}
_TENDER = {str(i) for i in range(10, 25)} | {"CZ07"}
_AWARD = {str(i) for i in range(25, 38)}         # 25-28 voluntary ex-ante transparency, 29-37 results
_MODIF = {"38", "39", "40", "E6"}                # contract modification
_FORM_TYPES = {
    "planning": NoticeType.PLAN,
    "consultation": NoticeType.PLAN,
    "competition": NoticeType.TENDER,
    "result": NoticeType.AWARD,
    "dir-awa-pre": NoticeType.AWARD,
    "cont-modif": NoticeType.MODIFICATION,
    "change": NoticeType.MODIFICATION,
}
# keep only these list fields in `raw` (the rest is workflow / uploader metadata, incl. personal data)
_RAW_KEYS = ("id", "variableId", "publicId", "workflowPlaceCode", "version", "updatedAt")


# ---------------------------------------------------------------- eForms JSON helpers

def _find(obj: Any, key: str) -> list[Any]:
    """All values stored under `key` anywhere in a nested eForms JSON tree (document order)."""
    out: list[Any] = []
    stack = [obj]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            for k, x in reversed(list(v.items())):
                if k == key:
                    out.append(x)
                else:
                    stack.append(x)
        elif isinstance(v, list):
            stack.extend(reversed(v))
    flat: list[Any] = []
    for x in out:
        flat.extend(x if isinstance(x, list) else [x])
    return flat


def _first(obj: Any, *keys: str) -> Any:
    for k in keys:
        for v in _find(obj, k):
            if v not in (None, ""):
                return v
    return None


def _str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _dt(v: Any) -> datetime | None:
    try:
        d = isoparse(v) if isinstance(v, str) and v else None
    except ValueError:
        return None
    return d if d and d.tzinfo else None


def _eforms_datetime(day: str | None, time: str | None) -> datetime | None:
    """eForms split date '2026-12-02+01:00' + time '09:00:00+01:00' -> aware datetime."""
    if not day or len(day) < 10:
        return None
    offset = day[10:] or "+00:00"
    if offset == "Z":
        offset = "+00:00"
    if not time:
        return _dt(f"{day[:10]}T23:59:00{offset}")
    t = time if any(c in time[8:] for c in "+-Z") else time + offset
    return _dt(f"{day[:10]}T{t}")


def _deadline(form: dict[str, Any]) -> datetime | None:
    """Earliest lot deadline for tenders (BT-131), else for requests to participate (BT-1311)."""
    for d_key, t_key in (("BT-131(d)-Lot", "BT-131(t)-Lot"), ("BT-1311(d)-Lot", "BT-1311(t)-Lot")):
        found = []
        for lot in _find(form, "ND-Lot"):
            day, time = _first(lot, d_key), _first(lot, t_key)
            if dt := _eforms_datetime(day, time):
                found.append(dt)
        if found:
            return min(found)
    return None


def _money(v: Any) -> tuple[float | None, str | None]:
    if isinstance(v, dict) and v.get("_value") is not None:
        try:
            return float(v["_value"]), _str(v.get("_currencyID"))
        except (TypeError, ValueError):
            return None, None
    return None, None


def _organizations(form: dict[str, Any]) -> dict[str, dict[str, Any]]:
    orgs = {}
    for org in _find(form, "ND-Organization"):
        company = (org or {}).get("ND-Company") or {}
        oid = company.get("OPT-200-Organization-Company")
        if oid:
            orgs[oid] = company
    return orgs


def _fingerprint(item: dict[str, Any]) -> str:
    return f"{item.get('dataHash') or ''}|{item.get('updatedAt') or ''}|{item.get('version') or ''}"


def _unique(xs: list[str | None]) -> list[str]:
    return list(dict.fromkeys(x for x in xs if x))


class NenCz(Connector):
    name = "nen_cz"
    title = "Czechia — NEN / Věstník veřejných zakázek"
    countries = ("CZ",)
    homepage = "https://vvz.nipez.cz"
    kind = "portal"
    poll_interval = timedelta(hours=1)        # the VVZ publishes continuously during the day
    first_sync_lookback = timedelta(days=3)   # ~250 notices per working day, one detail request each
    requests_per_second = 5.0                 # anonymous JSON API, ~0.25 s per request; details run in parallel
    supports_refresh = True

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        # in-flight detail requests; the shared rate limiter still caps throughput at `requests_per_second`
        self._detail_slots = asyncio.Semaphore(DETAIL_CONCURRENCY)

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        # notices already delivered in an earlier run whose list record (dataHash/updatedAt) is unchanged are
        # skipped: their eForms detail is immutable, so an overlap re-read costs one list request per page only
        known: dict[str, list[str]] = {k: v for k, v in (state.get("known") or {}).items()
                                       if isinstance(v, list) and len(v) == 2 and (_dt(v[1]) or since) >= since}
        cursor, page = since.isoformat(), 1
        delivered: set[str] = set()
        building: list[asyncio.Task[tuple[Notice, bool]]] = []
        next_page = asyncio.create_task(self._search(self._page_params(cursor, page)))
        try:
            while True:
                items = await next_page
                more = len(items) >= PAGE_SIZE
                if more:
                    last = (items[-1].get("data") or {}).get("datumUverejneniVvz")
                    if not last:
                        more = False
                    elif last == cursor:   # a full page of identical timestamps: fall back to page numbers
                        page += 1
                    else:
                        cursor, page = last, 1
                if more:   # prefetch the next list page while this page's details download
                    next_page = asyncio.create_task(self._search(self._page_params(cursor, page)))

                todo = []
                for item in items:
                    vid = item.get("variableId")
                    if not vid or not item.get("id") or vid in delivered:
                        continue
                    delivered.add(vid)
                    fp = _fingerprint(item)
                    if (known.get(vid) or [None])[0] != fp:
                        todo.append((vid, fp, item))
                # start every detail download now, yield in list order as each one lands
                building = [asyncio.create_task(self._build(item)) for _, _, item in todo]
                for (vid, fp, item), task in zip(todo, building, strict=True):
                    notice, complete = await task
                    yield notice
                    if complete:
                        known[vid] = [fp, (item.get("data") or {}).get("datumUverejneniVvz") or since.isoformat()]
                building = []
                yield Checkpoint({"cursor": cursor, "known": known})
                if not more:
                    break
        finally:
            for task in [next_page, *building]:   # closed early (or failed): stop pending downloads
                task.cancel()

    @staticmethod
    def _page_params(cursor: str, page: int) -> dict[str, Any]:
        return {f"{PUBLISHED}[gte]": cursor, f"order[{PUBLISHED}]": "ASC", "page": page, "limit": PAGE_SIZE}

    async def refresh(self, ref: StoredRef) -> Notice | None:
        items = await self._search({"variableId": ref.source_id, "page": 1, "limit": 1})
        return (await self._build(items[0]))[0] if items and items[0].get("id") else None

    async def _search(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        params = {"formGroup": "vz", "form": "vz", "workflowPlace": "UVEREJNENO_VVZ", **params}
        data = await self.http.get_json(SEARCH, params=params)
        return data if isinstance(data, list) else []

    async def _build(self, item: dict[str, Any]) -> tuple[Notice, bool]:
        """Notice for one list record + whether its eForms detail was available (False -> retry next run)."""
        form: dict[str, Any] = {}
        complete = True
        if (item.get("data") or {}).get("verzeSdk"):   # eForms notice -> full form lives in a child submission
            async with self._detail_slots:
                try:
                    children = await self.http.get_json(CHILDREN, params={"submission": item["id"]})
                except HttpError as e:
                    self.log.warning("nen_cz: no eForms detail for %s: %s", item["variableId"], e)
                    children = []
            if isinstance(children, list) and children:
                form = children[0].get("data") or {}
            else:
                complete = False
        return self.to_notice(item, form), complete

    def to_notice(self, item: dict[str, Any], form: dict[str, Any] | None = None) -> Notice:
        d = item.get("data") or {}
        form = form or {}
        root = form.get("ND-Root") or {}
        meta = form.get("metadata") or {}
        kind = str(d.get("druhFormulare") or meta.get("subType") or "")

        # buyer: the eForms organisation referenced as Buyer, falling back to the list's first zadavatel
        orgs = _organizations(root)
        buyer_ref = _first(root, "OPT-300-Procedure-Buyer")
        company = orgs.get(buyer_ref) or {}
        zadavatel = (d.get("zadavatele") or [{}])[0] or {}
        buyer_name = _str(company.get("BT-500-Organization-Company")) or _str(zadavatel.get("nazev"))
        buyer_id = _str(_first(company, "BT-501-Organization-Company")) or _str(zadavatel.get("ico"))
        address = company.get("ND-CompanyAddress") or {}
        country = iso3_to_iso2(address.get("BT-514-Organization-Company")) or "CZ"

        title = _str(_first(root, "BT-21-Procedure")) or _str(d.get("nazevZakazky")) or item["variableId"]
        description = _str(_first(root, "BT-24-Procedure"))

        cpvs = _unique([normalize_cpv(str(c)) for k in ("BT-262-Procedure", "BT-263-Procedure",
                                                       "BT-262-Lot", "BT-263-Lot")
                        for c in _find(root, k)])

        # notice type: form number first, the eForms form type (BT-03) as a fallback
        if kind in _PLAN:
            notice_type = NoticeType.PLAN
        elif kind in _TENDER:
            notice_type = NoticeType.TENDER
        elif kind in _AWARD:
            notice_type = NoticeType.AWARD
        elif kind in _MODIF:
            notice_type = NoticeType.MODIFICATION
        else:
            notice_type = _FORM_TYPES.get(_first(root, "BT-03-notice") or "", NoticeType.OTHER)
        if d.get("formularOpravuje"):        # corrigendum of an earlier form
            notice_type = NoticeType.MODIFICATION
        if d.get("zakazkaZrusena"):
            notice_type = NoticeType.CANCELLATION

        published = _dt(d.get("datumUverejneniVvz"))
        deadline = _dt(d.get("lhutaNabidkyZadosti")) or _deadline(root)

        if d.get("zakazkaZrusena") or d.get("formularZneplatnen"):
            status = Status.CANCELLED
        elif notice_type is NoticeType.AWARD:
            status = Status.AWARDED
        elif deadline and kind in _TENDER:
            status = Status.OPEN if deadline > self.ctx.now() else Status.CLOSED
        else:
            status = Status.UNKNOWN

        value, currency = _money(_first(root, "BT-161-NoticeResult"))
        if value is None:
            value, currency = _money(_first(root, "BT-27-Procedure"))
        if value is None:
            value, currency = _money(_first(root, "BT-27-Lot"))

        docs = _unique([absolute_url(_str(u)) for u in _find(root, "BT-15-Lot")])
        submit = _unique([absolute_url(_str(u)) for u in _find(root, "BT-18-Lot")])
        profile = absolute_url(_str(_first(root, "BT-508-Procedure-Buyer")))

        nipez = meta.get("identifikatoryNipez") or {}
        refs = {
            "vvz_form": item["variableId"],
            "vvz_id": item["id"],
            "vvz": _str(d.get("evCisloZakazkyVvz")),
            "vvz_related_form": _str(d.get("evCisloVvzSouvisejicihoFormulare")),
            "ted": _str(d.get("evCisloTed")),
            "ted_previous": _str(_first(root, "BT-758-notice")),
            "procedure": _str(_first(root, "BT-04-notice")),
            "nipez": _str(nipez.get("identifikatorNipez")) if isinstance(nipez, dict) else None,
        }

        raw = {k: item[k] for k in _RAW_KEYS if k in item}
        raw["data"] = {k: v for k, v in d.items() if k != "zdrojPodani"}
        if root:
            raw["eforms"] = {k: _first(root, k) for k in ("BT-02-notice", "BT-03-notice", "BT-01-notice",
                                                          "BT-105-Procedure", "BT-23-Procedure",
                                                          "OPT-300-Procedure-Buyer")}

        return Notice(
            source=self.name,
            source_id=item["variableId"],
            url=NOTICE_URL.format(id=item["id"]),
            title=title,
            notice_type=notice_type,
            status=status,
            description=description,
            buyer_name=buyer_name,
            buyer_id=buyer_id,
            buyer_country=country,
            buyer_city=_str(address.get("BT-513-Organization-Company")),
            cpv_codes=cpvs,
            procedure_type=_str(_first(root, "BT-105-Procedure")),
            legal_basis=_str(_first(root, "BT-01-notice")),
            published_at=published,
            deadline_at=deadline,
            value_amount=value,
            value_currency=currency,
            platform_url=(submit or docs or [profile])[0],
            language=lang3_to_lang2(_first(root, "BT-702(a)-notice")) or "cs",
            documents=[Document(u, kind="procurement_documents") for u in docs],
            refs={k: v for k, v in refs.items() if v},
            raw=raw,
        )
