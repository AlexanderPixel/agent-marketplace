"""Tender brief: a clean, cited summary of one captured tender (the viewer's "Brief" mode).

Built from what `enrich` captured — the tender's pages (markdown) and its attachments (extracted text) —
by one extractor module per section. Every fact carries a citation (`cite()`): the file (attachment filename or
page url), the page number when known, and a short verbatim quote, so the platform's grounding rule holds:
no claim about a tender without its source.

Section modules (`brief/<section>.py`) each define `extract(ctx: TenderContext) -> list[dict]`.
The item fields for each section are documented in docs/brief-format.md.
"""

from __future__ import annotations

import importlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SECTIONS = ("lots", "deadlines", "submission", "criteria", "checklist", "requirements", "amendments", "contacts")
_PAGE_MARK = re.compile(r"^\[page (\d+)\]$", re.M)
_VERSION = re.compile(r"Version (\d+)")


@dataclass
class Doc:
    """One attachment with its extracted text."""
    id: int | None
    filename: str
    folder: str | None
    text: str
    pages: int | None = None

    @property
    def version(self) -> int:
        """Amendment round from the folder ("Vergabeunterlagen/Version 2" -> 2); 0 when not versioned."""
        m = _VERSION.search(self.folder or "")
        return int(m.group(1)) if m else 0

    def page_texts(self) -> list[tuple[int | None, str]]:
        """[(page number, text)] using the "[page N]" markers PDF extraction writes; one chunk otherwise."""
        marks = list(_PAGE_MARK.finditer(self.text))
        if not marks:
            return [(None, self.text)]
        return [(int(m.group(1)), self.text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(self.text)])
                for i, m in enumerate(marks)]

    def find(self, pattern: str | re.Pattern, flags: int = re.I) -> list[tuple[int | None, re.Match]]:
        """Every match with the page it is on."""
        rx = re.compile(pattern, flags) if isinstance(pattern, str) else pattern
        return [(page, m) for page, chunk in self.page_texts() for m in rx.finditer(chunk)]


@dataclass
class TenderContext:
    notice: dict[str, Any]                       # the stored notice row (title, buyer_name, deadline_at, cpv_codes, ...)
    pages: list[dict[str, Any]] = field(default_factory=list)   # [{"url", "title", "markdown"}]
    docs: list[Doc] = field(default_factory=list)

    def docs_matching(self, pattern: str, latest_only: bool = True) -> list[Doc]:
        """Attachments whose filename matches; with latest_only, only the newest version of each file name."""
        rx = re.compile(pattern, re.I)
        found = [d for d in self.docs if rx.search(d.filename)]
        if not latest_only:
            return found
        newest: dict[str, Doc] = {}
        for d in sorted(found, key=lambda d: d.version):
            newest[d.filename] = d
        return list(newest.values())

    def doc(self, pattern: str) -> Doc | None:
        found = self.docs_matching(pattern)
        return found[-1] if found else None


def cite(source: Doc | str, page: int | None = None, quote: str | None = None) -> dict[str, Any]:
    """Citation for an item: the attachment (or page url) it comes from, page, and a short verbatim quote."""
    if isinstance(source, Doc):
        c = {"file": source.filename, "folder": source.folder, "page": page}
    else:
        c = {"file": source, "folder": None, "page": page}
    if quote:
        c["quote"] = " ".join(quote.split())[:300]
    return c


def build(ctx: TenderContext) -> dict[str, Any]:
    """Run every section extractor; a failing extractor reports its error instead of breaking the brief."""
    brief: dict[str, Any] = {"built_at": datetime.now(UTC).isoformat(timespec="seconds"), "errors": {}}
    for name in SECTIONS:
        try:
            brief[name] = importlib.import_module(f"{__name__}.{name}").extract(ctx)
        except Exception as e:  # noqa: BLE001
            brief[name] = []
            brief["errors"][name] = f"{type(e).__name__}: {e}"
    n = ctx.notice
    brief["summary"] = {
        "title": n.get("title"), "buyer": n.get("buyer_name"), "country": n.get("buyer_country"),
        "procedure": n.get("procedure_type"), "cpv": n.get("cpv_codes") or [], "deadline": n.get("deadline_at"),
        "value": n.get("value_amount"), "currency": n.get("value_currency"), "platform_url": n.get("platform_url"),
        "lots": len(brief.get("lots") or []), "documents": len(ctx.docs),
        "latest_version": max((d.version for d in ctx.docs), default=0),
    }
    return brief


def load_context(db: sqlite3.Connection, notice_id: int) -> TenderContext:
    """Context from the store: the notice plus pages/attachments captured for it or any copy of the same tender."""
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM notices WHERE id=?", (notice_id,)).fetchone()
    notice = dict(row)
    notice["cpv_codes"] = [r[0] for r in db.execute("SELECT cpv FROM notice_cpv WHERE notice_id=?", (notice_id,))]
    notice.pop("raw", None)
    ids = [r[0] for r in db.execute("SELECT id FROM notices WHERE cluster_id = COALESCE(?, ?) OR id = ?",
                                    (row["cluster_id"], notice_id, notice_id))]
    marks = ",".join("?" * len(ids))
    pages = [dict(r) for r in db.execute(
        f"SELECT url, title, markdown FROM notice_pages WHERE notice_id IN ({marks}) ORDER BY length(markdown) DESC", ids)]
    docs = [Doc(r["id"], r["filename"], r["folder"], r["text"] or "", r["pages"]) for r in db.execute(
        f"SELECT id, filename, folder, text, pages FROM attachments WHERE notice_id IN ({marks}) ORDER BY folder, filename",
        ids)]
    return TenderContext(notice, pages, docs)


def load_fixture_context(path: str | Path) -> TenderContext:
    """Context from a dumped fixture directory (tests/fixtures/vm191): notice.json, pages.json, attachments.json."""
    p = Path(path)
    notice = json.loads((p / "notice.json").read_text())
    notice.setdefault("cpv_codes", ["33000000"])
    pages = [{**pg, "markdown": (p / pg["markdown_file"]).read_text()} for pg in json.loads((p / "pages.json").read_text())]
    docs = [Doc(a["id"], a["filename"], a["folder"], (p / a["text_file"]).read_text(), a["pages"])
            for a in json.loads((p / "attachments.json").read_text())]
    return TenderContext(notice, pages, docs)
