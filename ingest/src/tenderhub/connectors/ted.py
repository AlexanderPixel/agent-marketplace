"""TED — Tenders Electronic Daily: every above-threshold notice in the EU/EEA (eForms).

Reference connector: the other connectors follow the same shape (constants, small parse helpers,
`sync` that pages through the source and yields Notices + Checkpoints, a pure `to_notice` mapper).
API docs: https://docs.ted.europa.eu/api/latest/search.html — anonymous, no key needed.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

from dateutil.parser import isoparse

from ..connector import Checkpoint, Connector
from ..countries import iso3_to_iso2, lang3_to_lang2
from ..models import Document, Notice, NoticeType, absolute_url, normalize_cpv

API = "https://api.ted.europa.eu/v3/notices/search"
PAGE_SIZE = 250
FIELDS = [
    "publication-number", "publication-date", "notice-type", "form-type", "notice-title", "title-proc",
    "description-proc", "buyer-name", "buyer-country", "buyer-city", "organisation-identifier-buyer",
    "buyer-internet-address", "classification-cpv", "procedure-type", "legal-basis",
    "deadline-receipt-tender-date-lot", "deadline-receipt-tender-time-lot", "estimated-value-proc",
    "estimated-value-cur-proc", "document-url-lot", "submission-url-lot", "procedure-identifier",
    "notice-identifier",
]
FORM_TYPES = {
    "planning": NoticeType.PLAN,
    "competition": NoticeType.TENDER,
    "result": NoticeType.AWARD,
    "dir-awa-pre": NoticeType.AWARD,      # voluntary ex-ante transparency (direct award announced)
    "change": NoticeType.MODIFICATION,
    "cont-modif": NoticeType.MODIFICATION,
    "consultation": NoticeType.PLAN,
}


def _first(v: Any) -> Any:
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _text(v: Any, prefer: str = "eng") -> tuple[str | None, str | None]:
    """Multilingual field {"pol": "..."} or {"pol": ["..."]} -> (text, 3-letter language)."""
    if not v:
        return None, None
    if not isinstance(v, dict):
        return _first(v), None
    lang = prefer if prefer in v else next(iter(v))
    return _first(v[lang]), lang


def _unique(xs: list[str] | None) -> list[str]:
    return list(dict.fromkeys(x for x in xs or [] if x))


_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})(Z|[+-]\d{2}:\d{2})?$")


def _date(v: str | None) -> datetime | None:
    """TED dates look like '2026-10-28+01:00' (date with offset)."""
    m = _DATE.match(v or "")
    if not m:
        return None
    offset = m.group(2) if m.group(2) and m.group(2) != "Z" else "+00:00"
    return isoparse(f"{m.group(1)}T00:00:00{offset}")


def _deadline(dates: list[str] | None, times: list[str] | None) -> datetime | None:
    """Combine lot deadline date + time; a date without time means end of that day."""
    day = _date(dates[0]) if dates else None
    if day is None:
        return None
    if times:
        t = isoparse(f"{day.date().isoformat()}T{times[0]}")
        return t if t.tzinfo else t.replace(tzinfo=day.tzinfo)
    return day.replace(hour=23, minute=59)


class Ted(Connector):
    name = "ted"
    title = "TED — Tenders Electronic Daily"
    countries = ("EU",)
    homepage = "https://ted.europa.eu"
    kind = "aggregator"
    poll_interval = timedelta(hours=2)        # TED publishes once per working day; poll often to catch it early
    first_sync_lookback = timedelta(days=3)
    requests_per_second = 1.0                 # TED returns 429 above ~5 parallel requests

    async def sync(self, since: datetime, state: dict[str, Any]) -> AsyncIterator[Notice | Checkpoint]:
        body: dict[str, Any] = {
            "query": f"publication-date>={since:%Y%m%d}",
            "fields": FIELDS,
            "limit": PAGE_SIZE,
            "paginationMode": "ITERATION",
        }
        while True:
            data = await self.http.post_json(API, json=body)
            notices = data.get("notices") or []
            for x in notices:
                yield self.to_notice(x)
            token = data.get("iterationNextToken")
            if not token or not notices:
                break
            body["iterationNextToken"] = token
        yield Checkpoint({"last_query": body["query"]})

    def to_notice(self, x: dict[str, Any]) -> Notice:
        pn = x["publication-number"]
        title, lang = _text(x.get("title-proc"), prefer="")
        if not title:
            title, lang = _text(x.get("notice-title"))
        description, _ = _text(x.get("description-proc"), prefer=lang or "eng")
        buyer, _ = _text(x.get("buyer-name"), prefer=lang or "eng")
        city, _ = _text(x.get("buyer-city"), prefer="mul")
        docs = _unique([absolute_url(u) for u in x.get("document-url-lot") or []])
        submit = _unique([absolute_url(u) for u in x.get("submission-url-lot") or []])
        value = _first(x.get("estimated-value-proc"))
        x = {k: v for k, v in x.items() if k != "links"}
        return Notice(
            source=self.name,
            source_id=pn,
            url=f"https://ted.europa.eu/en/notice/-/detail/{pn}",
            title=title or pn,
            notice_type=FORM_TYPES.get(x.get("form-type", ""), NoticeType.OTHER),
            description=description,
            buyer_name=buyer,
            buyer_id=_first(x.get("organisation-identifier-buyer")),
            buyer_country=iso3_to_iso2(_first(x.get("buyer-country"))),
            buyer_city=city,
            cpv_codes=_unique([normalize_cpv(c) for c in x.get("classification-cpv") or []]),
            procedure_type=_first(x.get("procedure-type")),
            legal_basis=_first(x.get("legal-basis")),
            published_at=_date(x.get("publication-date")),
            deadline_at=_deadline(x.get("deadline-receipt-tender-date-lot"), x.get("deadline-receipt-tender-time-lot")),
            value_amount=float(value) if value else None,
            value_currency=_first(x.get("estimated-value-cur-proc")),
            platform_url=_first(submit) or _first(docs),
            language=lang3_to_lang2(lang),
            documents=[Document(u, kind="procurement_documents") for u in docs],
            refs={k: v for k, v in {"ted": pn, "procedure": x.get("procedure-identifier"),
                                    "notice": x.get("notice-identifier")}.items() if v},
            raw=x,
        )

