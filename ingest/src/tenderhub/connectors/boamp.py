"""BOAMP — Bulletin officiel des annonces des marchés publics (France).

Every French public buyer (State incl. Ministère des Armées, local authorities, hospitals, ...) publishes
here: MAPA (<90 k EUR), national "FNSimple" notices (90 k EUR..EU thresholds), DSP concessions and the
French copies of EU-threshold notices (eForms, incl. the defence directive 2009/81 = perimetre DIRECTIVE-81).

Source: the DILA open-data API (Opendatasoft explore v2.1), anonymous, no key:
    https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records
Each record has flat index fields (idweb, objet, nomacheteur, dateparution, nature, ...) plus two JSON
strings: `gestion` (indexing data; carries the deadline with its real UTC offset) and `donnees` (the full
notice in one of the schemas EFORMS / FNSimple / MAPA / DSP / ...), from which CPV, SIRET, city, value,
description and document links are read.

Fetching: one bulk `exports/json` request per publication day (no 10 000 offset cap, no paging, ~1 s and
~400 notices per day), up to CONCURRENCY days in flight at once; results are yielded in date order and a
Checkpoint follows each completed day. The last slice is open-ended (`>= today`) so nothing dated later is missed.
Notices are immutable once published (corrections come as separate RECTIFICATIF notices with their own
idweb, linked through `annonce_lie`), so there is no refresh.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from collections import deque
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.parser import isoparse

from ..connector import Checkpoint, Connector
from ..countries import iso3_to_iso2
from ..models import Document, Notice, NoticeType, Status, absolute_url, normalize_cpv

EXPORT = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/exports/json"
NOTICE_URL = "https://www.boamp.fr/pages/avis/?q=idweb:{}"
CONCURRENCY = 4          # day slices fetched in parallel
PARIS = ZoneInfo("Europe/Paris")

# overseas departments/collectivities publish deadlines in their own local time
DEPT_TZ = {
    "971": "America/Guadeloupe", "972": "America/Martinique", "973": "America/Cayenne",
    "974": "Indian/Reunion", "975": "America/Miquelon", "976": "Indian/Mayotte",
    "977": "America/St_Barthelemy", "978": "America/Marigot", "986": "Pacific/Wallis",
    "987": "Pacific/Tahiti", "988": "Pacific/Noumea",
}

NATURES = {
    "APPEL_OFFRE": NoticeType.TENDER,
    "ATTRIBUTION": NoticeType.AWARD,
    "EX_ANTE_VOLONTAIRE": NoticeType.AWARD,     # voluntary ex-ante transparency (direct award announced)
    "RECTIFICATIF": NoticeType.MODIFICATION,
    "MODIFICATION": NoticeType.MODIFICATION,
    "ANNULATION": NoticeType.CANCELLATION,
    "PRE-INFORMATION": NoticeType.PLAN,
    "PERIODIQUE": NoticeType.PLAN,               # periodic indicative notice (utilities)
    "QUALIFICATION": NoticeType.OTHER,           # qualification system (utilities)
}
STATUSES = {NoticeType.AWARD: Status.AWARDED, NoticeType.CANCELLATION: Status.CANCELLED}
LEGAL_BASIS = {
    "DIRECTIVE-24": "32014L0024", "DIRECTIVE-25": "32014L0025",
    "DIRECTIVE-23": "32014L0023", "DIRECTIVE-81": "32009L0081",
}
PLATFORM_KEYS = ("urlParticipation", "urlDocument", "urlProfilAch", "urlProfilAcheteur")

_C1 = re.compile(r"[\x80-\x9f]")
_CP1252 = {"\x92": "’", "\x91": "‘", "\x93": "“", "\x94": "”", "\x96": "–",
           "\x97": "—", "\x85": "…", "\x9c": "œ", "\x8c": "Œ", "\x80": "€"}


def _clean(s: Any) -> str | None:
    """Collapse whitespace; repair stray windows-1252 control chars ('d\\x92inspection' -> 'd’inspection')."""
    if not isinstance(s, str):
        return None
    s = _C1.sub(lambda m: _CP1252.get(m.group(0), ""), s)
    s = " ".join(s.split())
    return s or None


def _url(v: Any) -> str | None:
    return absolute_url(html.unescape(v)) if isinstance(v, str) else None


def _siret(v: Any) -> str | None:
    """Keep French SIREN (9 digits) / SIRET (14 digits); eForms often carries free-text ids instead."""
    v = re.sub(r"[\s.]", "", v) if isinstance(v, str) else ""
    return v if re.fullmatch(r"\d{9}|\d{14}", v) else None


def _first(v: Any) -> Any:
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _txt(v: Any) -> str | None:
    """eForms leaf {"@languageID": .., "#text": ..} / plain str / list of either -> str."""
    v = _first(v)
    if isinstance(v, dict):
        v = v.get("#text")
    return v.strip() if isinstance(v, str) and v.strip() else None


def _get(d: Any, *path: str) -> Any:
    for k in path:
        d = _first(d)
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _walk(obj: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Depth-first (path, value) over every dict entry of a nested JSON document."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield (*path, k), v
            yield from _walk(v, (*path, k))
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, path)


def _find(obj: Any, key: str) -> Any:
    return next((v for p, v in _walk(obj) if p[-1] == key), None)


def _unique(xs: Any) -> list[str]:
    return list(dict.fromkeys(x for x in xs if x))


def _loads(s: Any) -> dict[str, Any]:
    if isinstance(s, dict):
        return s
    try:
        v = json.loads(s) if s else {}
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _tz(x: dict[str, Any]) -> ZoneInfo:
    dep = _first(x.get("code_departement") or [])
    return ZoneInfo(DEPT_TZ[dep]) if dep in DEPT_TZ else PARIS


def _local_dt(v: str | None, tz: ZoneInfo) -> datetime | None:
    """'2026-11-30T12:00:00+01:00' keeps its offset; naive or 'Z' values are French wall-clock time
    (buyers type local time and several schemas append a bogus 'Z')."""
    if not v:
        return None
    try:
        d = isoparse(v)
    except ValueError:
        return None
    if d.tzinfo is None or (v.endswith("Z") and d.utcoffset() == timedelta(0)):
        return d.replace(tzinfo=tz)
    return d


def _eforms_end(period: Any, tz: ZoneInfo) -> datetime | None:
    """eForms period {"cbc:EndDate": "2026-12-01+01:00", "cbc:EndTime": "10:00:00.000+01:00"}."""
    day = _txt(_get(period, "cbc:EndDate"))
    if not day:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})(.*)$", day)
    if not m:
        return None
    t = _txt(_get(period, "cbc:EndTime"))
    if t:
        return _local_dt(f"{m.group(1)}T{t}", tz)
    d = _local_dt(f"{m.group(1)}T23:59:00{m.group(2)}", tz)
    return d


def _cpvs(donnees: dict[str, Any]) -> list[str]:
    """National schemas: ...CPV/codeCPV -> objetPrincipal -> classPrincipale; eForms: ItemClassificationCode[@listName=cpv]."""
    out = []
    for p, v in _walk(donnees):
        if p[-1] == "classPrincipale" and any("cpv" in k.lower() for k in p):
            out.append(normalize_cpv(_txt(v) or ""))
        elif p[-1] == "cbc:ItemClassificationCode":
            for c in v if isinstance(v, list) else [v]:
                if isinstance(c, dict) and c.get("@listName", "cpv") == "cpv":
                    out.append(normalize_cpv(c.get("#text") or ""))
    return _unique(out)


def _amount(v: Any) -> tuple[float | None, str | None]:
    """eForms {"@currencyID": "EUR", "#text": "1530000"} or national {"@devise": "EUR", "valeur": "656963"}."""
    v = _first(v)
    if not isinstance(v, dict):
        return None, None
    raw, cur = (v.get("#text"), v.get("@currencyID")) if "#text" in v else (v.get("valeur"), v.get("@devise"))
    try:
        return float(str(raw).replace(" ", "").replace(",", ".")), cur
    except (TypeError, ValueError):
        return None, None


def _eforms(root: dict[str, Any], tz: ZoneInfo) -> dict[str, Any]:
    """Pull buyer / value / deadline / documents out of an eForms notice (ContractNotice, ContractAwardNotice, ...)."""
    out: dict[str, Any] = {"legal_basis": _txt(root.get("cbc:RegulatoryDomain")), "notice": _txt(root.get("cbc:ID"))}
    party = _get(root, "cac:ContractingParty")
    buyer_ref = _txt(_get(party, "cac:Party", "cac:PartyIdentification", "cbc:ID"))
    orgs = _find(root, "efac:Organization") or []
    for org in orgs if isinstance(orgs, list) else [orgs]:
        company = _get(org, "efac:Company")
        if _txt(_get(company, "cac:PartyIdentification", "cbc:ID")) == buyer_ref:
            out["buyer_id"] = _siret(_txt(_get(company, "cac:PartyLegalEntity", "cbc:CompanyID")))
            out["buyer_city"] = _clean(_txt(_get(company, "cac:PostalAddress", "cbc:CityName")))
            out["buyer_country"] = iso3_to_iso2(_txt(_get(company, "cac:PostalAddress", "cac:Country",
                                                          "cbc:IdentificationCode")))
            break
    project = root.get("cac:ProcurementProject")
    out["description"] = _clean(_txt(_get(project, "cbc:Description")))
    value = _get(project, "cac:RequestedTenderTotal", "cbc:EstimatedOverallContractAmount") \
        or _find(root, "cbc:TotalAmount") or _find(root, "cbc:EstimatedOverallContractAmount")
    out["value_amount"], out["value_currency"] = _amount(value)
    docs = [_txt(_get(ref, "cac:Attachment", "cac:ExternalReference", "cbc:URI"))
            for p, refs in _walk(root) if p[-1] == "cac:CallForTendersDocumentReference"
            for ref in (refs if isinstance(refs, list) else [refs])]
    out["documents"] = _unique(_url(u) for u in docs)
    endpoints = [_txt(v) for p, v in _walk(root) if p[-2:] == ("cac:TenderRecipientParty", "cbc:EndpointID")]
    out["platform_url"] = next(iter(_unique(_url(u) for u in [*endpoints, *out["documents"],
                                                                        _txt(_get(party, "cbc:BuyerProfileURI"))])),
                               None)
    for p, v in _walk(root):
        if p[-1] in ("cac:TenderSubmissionDeadlinePeriod", "cac:ParticipationRequestReceptionPeriod"):
            out["deadline_at"] = _eforms_end(v, tz)
            if out["deadline_at"]:
                break
    return out


def _national(body: dict[str, Any]) -> dict[str, Any]:
    """FNSimple / MAPA / DSP / ... national schemas (French field names)."""
    org = body.get("organisme") or {}
    out: dict[str, Any] = {
        "buyer_id": _siret(_txt(org.get("codeIdentificationNational"))) or _siret(_txt(_find(org, "siret"))),
        "buyer_city": _clean(_txt(org.get("ville")) or _txt(_get(org, "adr", "ville"))),
    }
    nature = _find(body, "natureMarche")
    desc = _get(nature, "description") if isinstance(_first(nature), dict) else None
    out["description"] = _clean(_txt(desc) if isinstance(desc, (str, list)) else None) \
        or _clean(_txt(_get(body, "initial", "descriptionMarche", "lot", "description")))
    out["value_amount"], out["value_currency"] = _amount(_find(body, "valeurEstimee"))
    urls = _unique(_url(_txt(v)) for p, v in _walk(body) if p[-1] in PLATFORM_KEYS)
    out["platform_url"] = urls[0] if urls else None
    out["documents"] = _unique(_url(_txt(v)) for p, v in _walk(body) if p[-1] == "urlDocument")
    return out


class Boamp(Connector):
    name = "boamp"
    title = "BOAMP — Bulletin officiel des annonces des marchés publics"
    countries = ("FR",)
    homepage = "https://www.boamp.fr"
    kind = "portal"
    poll_interval = timedelta(hours=2)         # BOAMP publishes several batches per day, incl. weekends
    first_sync_lookback = timedelta(days=7)    # ~400 notices/day, one request per day
    requests_per_second = 3.0                  # bulk export: one ~1 s request per day

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        start = since.astimezone(PARIS).date()
        today = self.ctx.now().astimezone(PARIS).date()
        days = [start + timedelta(days=i) for i in range((today - start).days)] + [max(start, today)]
        slices = [(d, f"dateparution = date'{d.isoformat()}'") for d in days[:-1]]
        slices.append((days[-1], f"dateparution >= date'{days[-1].isoformat()}'"))

        # order-preserving prefetch: keep up to CONCURRENCY day exports in flight, yield them in date order
        pending: deque[asyncio.Task[list[dict[str, Any]]]] = deque()
        todo = iter(slices)
        try:
            for day, _ in slices:
                while len(pending) < CONCURRENCY and (nxt := next(todo, None)):
                    pending.append(asyncio.create_task(self._export(nxt[1])))
                records = await pending.popleft()
                for x in records:
                    yield self.to_notice(x)
                yield Checkpoint({"last_date": day.isoformat()})
        finally:
            for t in pending:
                t.cancel()

    async def _export(self, where: str) -> list[dict[str, Any]]:
        data = await self.http.get_json(EXPORT, params={"where": where, "order_by": "dateparution, idweb"})
        if not isinstance(data, list):
            raise ValueError(f"boamp export: expected a JSON list, got {type(data).__name__}")
        return data

    def to_notice(self, x: dict[str, Any]) -> Notice:
        idweb = x["idweb"]
        tz = _tz(x)
        donnees = _loads(x.get("donnees"))
        gestion = _loads(x.get("gestion"))
        schema, body = next(iter(donnees.items()), ("", {}))
        if schema == "EFORMS" and isinstance(body, dict):
            extra = _eforms(next(iter(body.values()), {}), tz)
        else:
            extra = _national(body if isinstance(body, dict) else {})

        notice_type = NATURES.get(x.get("nature") or "", NoticeType.OTHER)
        if x.get("etat") == "ANNULATION":
            notice_type = NoticeType.CANCELLATION
        elif x.get("etat") == "RECTIFICATIF" and notice_type is not NoticeType.CANCELLATION:
            notice_type = NoticeType.MODIFICATION
        deadline = _local_dt(_get(gestion, "INDEXATION", "DATE_LIMITE_REPONSE"), tz) or extra.get("deadline_at")
        if deadline is None and x.get("datelimitereponse"):
            # flat field is local wall-clock time mislabelled +00:00
            deadline = _local_dt(str(x["datelimitereponse"])[:19], tz)
        published = x.get("dateparution")
        procedure = _clean(x.get("procedure_libelle"))   # "Procédure NC" = not communicated
        title = _clean(x.get("objet")) or _clean(_txt(_find(body, "intitule"))) or idweb
        previous = ",".join(x.get("annonce_lie") or [])
        refs = {"boamp": idweb, "procedure": x.get("contractfolderid"), "boamp_previous": previous or None,
                "notice": extra.get("notice")}
        raw = {k: v for k, v in x.items() if k not in ("donnees", "gestion")}
        raw["schema"] = schema
        return Notice(
            source=self.name,
            source_id=idweb,
            url=absolute_url(x.get("url_avis")) or NOTICE_URL.format(idweb),
            title=title,
            notice_type=notice_type,
            status=STATUSES.get(notice_type, Status.UNKNOWN),
            description=extra.get("description"),
            buyer_name=_clean(x.get("nomacheteur")),
            buyer_id=extra.get("buyer_id"),
            buyer_country=extra.get("buyer_country") or "FR",
            buyer_city=extra.get("buyer_city"),
            cpv_codes=_cpvs(donnees),
            procedure_type=procedure if procedure and procedure != "Procédure NC" else None,
            legal_basis=extra.get("legal_basis") or LEGAL_BASIS.get(x.get("perimetre") or ""),
            published_at=datetime.combine(date.fromisoformat(str(published)[:10]), time(), PARIS) if published else None,
            deadline_at=deadline,
            value_amount=extra.get("value_amount"),
            value_currency=extra.get("value_currency") if extra.get("value_amount") is not None else None,
            platform_url=extra.get("platform_url"),
            language="fr",
            documents=[Document(u, kind="procurement_documents") for u in extra.get("documents") or []],
            refs={k: v for k, v in refs.items() if isinstance(v, str) and v},
            raw=raw,
        )
