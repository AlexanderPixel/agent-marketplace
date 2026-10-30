"""Deep capture of one tender: its web pages as readable text and its attachments with extracted text.

Sync stores the structured notice. Enrichment goes further for tenders someone actually looks at:
- every page about the tender (each copy's notice page, the bid/documents platform) is fetched and turned
  into readable markdown, the way Firecrawl does: navigation, scripts and chrome dropped, headings, lists,
  tables and links kept;
- every attachment is downloaded, stored on disk, and its text extracted (PDF, DOCX, XLSX, ZIP, plain text),
  so it can be previewed, searched and fed to the SWZ parser.

Platforms that hide documents behind session-bound links get a handler (see HANDLERS); everything else
falls back to the documents listed on the notice plus document-looking links on the captured pages.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from lxml import etree
from selectolax.lexbor import LexborHTMLParser, LexborNode

from .http import DEFAULT_USER_AGENT

log = logging.getLogger("tenderhub.enrich")

MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_TEXT_CHARS = 300_000
MAX_ZIP_MEMBERS = 60
DOC_EXT = re.compile(r"\.(pdf|docx?|xlsx?|odt|ods|rtf|txt|zip|xml|csv|aidf|d8[1-6]|x8[1-6])(\?|$)", re.I)
NUMBERED_SECTION = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.? +[^\W\d][^:]{0,80}$")
SKIP_MEMBER = re.compile(r"\.(xsl|xslt|xsd|css|js|png|jpe?g|gif|ico|woff2?)$", re.I)
DROP_TAGS = ("script", "style", "noscript", "svg", "nav", "header", "footer", "form", "iframe", "button",
             "select", "template", "dialog")
DROP_CLASSES = re.compile(r"(cookie|modal|navbar|breadcrumb|skip-?link|sr-only|visually-hidden|footer|header|menu)",
                          re.I)


# ---------------------------------------------------------------- page -> markdown

def _inline(node: LexborNode, base: str) -> str:
    out = []
    for child in node.iter(include_text=True):
        if child.tag == "-text":
            out.append(child.text_content or "")
        elif child.tag == "br":
            out.append("\n")
        elif child.tag == "a":
            text = " ".join(child.text(separator=" ").split())
            href = child.attributes.get("href") or ""
            if text and href and not href.startswith(("javascript:", "#")):
                out.append(f"[{text}]({urljoin(base, href)})")
            else:
                out.append(text)
        elif child.tag in ("strong", "b"):
            t = " ".join(child.text(separator=" ").split())
            out.append(f"**{t}**" if t else "")
        else:
            out.append(_inline(child, base))
    return re.sub(r"[ \t]+", " ", "".join(out))


def _table(node: LexborNode, base: str) -> str:
    rows = []
    for tr in node.css("tr"):
        cells = [_inline(c, base).replace("|", "\\|").replace("\n", " ").strip() for c in tr.css("th,td")]
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    if width == 2 and len(rows) > 1 and not node.css_first("th"):
        return "\n".join(f"- **{k}**: {v}" for k, v in rows if k or v)  # key/value layout tables
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


BLOCK_TAGS = {"div", "section", "article", "main", "aside", "p", "ul", "ol", "li", "table", "dl", "dt", "dd",
              "h1", "h2", "h3", "h4", "h5", "h6", "form", "fieldset", "blockquote", "pre", "hr", "header", "footer",
              "nav", "figure", "details", "summary"}


def _inline_only(node: LexborNode) -> bool:
    """True when an element holds only inline content (spans, links, emphasis, text) — render it as one line."""
    return not any(d.tag in BLOCK_TAGS for d in node.traverse(include_text=False) if d is not node)


def _flush(run: list[LexborNode], base: str, out: list[str]) -> None:
    """A run of inline siblings (text, spans, links) becomes one line: "1.1 Offizielle Bezeichnung: value"."""
    parts = []
    for n in run:
        if n.tag == "-text":
            parts.append(n.text_content or "")
        elif n.tag == "a":
            parts.append(_inline_single(n, base))
        else:
            parts.append(_inline(n, base))
    t = " ".join("".join(parts).split())
    t = re.sub(r"\s+([:;,.)])", r"\1", t).replace("( ", "(")
    if t:
        out.append(t)
    run.clear()


def _blocks(node: LexborNode, base: str, out: list[str]) -> None:
    run: list[LexborNode] = []
    for child in node.iter(include_text=True):
        tag = child.tag
        if tag == "-text" or tag not in BLOCK_TAGS:
            run.append(child)
            continue
        _flush(run, base, out)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            t = " ".join(child.text(separator=" ").split())
            if t:
                out.append("#" * int(tag[1]) + " " + t)
        elif tag == "table":
            if t := _table(child, base):
                out.append(t)
        elif tag in ("ul", "ol"):
            for i, li in enumerate(child.css("li"), 1):
                t = " ".join(_inline(li, base).split())
                if t:
                    out.append(f"{i}. {t}" if tag == "ol" else f"- {t}")
        elif tag == "dt":
            t = _inline(child, base).strip()
            if t:
                out.append(f"**{t}**")
        elif tag == "hr":
            out.append("---")
        elif _inline_only(child):
            _flush([child], base, out)
        else:
            _blocks(child, base, out)
    _flush(run, base, out)


def _inline_single(a: LexborNode, base: str) -> str:
    text = " ".join(a.text(separator=" ").split())
    href = a.attributes.get("href") or ""
    if not text:
        return ""
    return f"[{text}]({urljoin(base, href)})" if href and not href.startswith(("javascript:", "#")) else text


def html_to_markdown(html: str, base: str) -> tuple[str, str]:
    """Readable markdown of a page's main content. Returns (title, markdown)."""
    tree = LexborHTMLParser(html)
    title = (tree.css_first("title").text(strip=True) if tree.css_first("title") else "")[:300]
    for sel in DROP_TAGS:
        for n in tree.css(sel):
            n.decompose()
    for n in tree.css("[hidden], [aria-hidden=true], [style*='display:none'], [style*='display: none']"):
        n.decompose()
    for n in tree.css("[class], [id]"):
        if DROP_CLASSES.search((n.attributes.get("class") or "") + " " + (n.attributes.get("id") or "")):
            n.decompose()
    root = tree.css_first("main") or tree.css_first("article") or tree.css_first("#content") or tree.body
    out: list[str] = []
    if root is not None:
        _blocks(root, base, out)
    lines, seen = [], set()
    merged: list[str] = []
    for block in out:  # "1.1." on its own line followed by "Buyer" -> "1.1. Buyer"
        if merged and re.fullmatch(r"\d{1,2}(?:\.\d{1,2}){0,3}\.?", merged[-1].strip()) and len(block) < 90:
            merged[-1] = f"{merged[-1].strip()} {block.strip()}"
        else:
            merged.append(block)
    for block in merged:
        block = block.strip()
        if m := NUMBERED_SECTION.match(block):  # "2.1.1 Zweck" -> "### 2.1.1 Zweck"
            block = "#" * min(2 + m.group(1).count("."), 5) + " " + block
        key = block.lower()
        if block and (key not in seen or block.startswith("|")):  # drop repeated chrome lines
            seen.add(key)
            lines.append(block)
    return title, "\n\n".join(lines)


# ---------------------------------------------------------------- attachment text

def _xml_text(data: bytes, para_tag: str, text_tag: str) -> str:
    root = etree.fromstring(data, parser=etree.XMLParser(recover=True, huge_tree=True))
    paras = []
    for p in root.iter(f"{{*}}{para_tag}"):
        t = "".join(x.text or "" for x in p.iter(f"{{*}}{text_tag}"))
        if t.strip():
            paras.append(t)
    return "\n".join(paras)


def _xlsx_text(z: zipfile.ZipFile) -> str:
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = etree.fromstring(z.read("xl/sharedStrings.xml"), parser=etree.XMLParser(recover=True))
        shared = ["".join(t.text or "" for t in si.iter("{*}t")) for si in root.iter("{*}si")]
    out = []
    for name in sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
        out.append(f"## {name.rsplit('/', 1)[-1]}")
        root = etree.fromstring(z.read(name), parser=etree.XMLParser(recover=True, huge_tree=True))
        for row in root.iter("{*}row"):
            cells = []
            for c in row.iter("{*}c"):
                v = c.find("{*}v")
                val = v.text if v is not None else "".join(t.text or "" for t in c.iter("{*}t"))
                if c.get("t") == "s" and val and val.isdigit() and int(val) < len(shared):
                    val = shared[int(val)]
                cells.append((val or "").strip())
            if any(cells):
                out.append("\t".join(cells))
    return "\n".join(out)


def extract_text(name: str, data: bytes, depth: int = 0) -> tuple[str, int | None]:
    """(text, pages) for a downloaded file; best effort, never raises."""
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    try:
        if data[:4] == b"%PDF" or ext == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join(f"[page {i}]\n{(p.extract_text() or '').strip()}" for i, p in enumerate(reader.pages, 1))
            return text, len(reader.pages)
        if data[:2] == b"PK":
            z = zipfile.ZipFile(io.BytesIO(data))
            names = z.namelist()
            if "word/document.xml" in names:
                return _xml_text(z.read("word/document.xml"), "p", "t"), None
            if "xl/workbook.xml" in names:
                return _xlsx_text(z), None
            if "content.xml" in names:  # ODF
                return _xml_text(z.read("content.xml"), "p", "span") or _xml_text(z.read("content.xml"), "p", "p"), None
            if depth == 0:  # plain zip: list members and extract each
                parts = []
                for info in z.infolist()[:MAX_ZIP_MEMBERS]:
                    if info.is_dir() or info.file_size > MAX_FILE_BYTES or SKIP_MEMBER.search(info.filename):
                        continue
                    sub, _ = extract_text(info.filename, z.read(info), depth + 1)
                    parts.append(f"===== {info.filename} =====\n{sub}")
                return "\n\n".join(parts), None
        if data.lstrip()[:1] == b"<" and ext not in ("html", "htm"):
            try:  # XML (eForms, GAEB bills of quantities, catalogue data): keep the text, drop the markup
                root = etree.fromstring(data, parser=etree.XMLParser(recover=True, huge_tree=True))
                if root is not None:
                    return "\n".join(t.strip() for t in root.itertext() if t.strip()), None
            except etree.LxmlError:
                pass
        if ext in ("txt", "csv", "json", "md", "html", "htm") or _looks_textual(data):
            for enc in ("utf-8", "cp1250", "latin-1"):
                try:
                    return data.decode(enc), None
                except UnicodeDecodeError:
                    continue
    except Exception as e:  # noqa: BLE001 — a broken file must not stop the rest
        return f"[text extraction failed: {type(e).__name__}: {e}]", None
    return "", None


def _looks_textual(data: bytes) -> bool:
    sample = data[:4096]
    return bool(sample) and sum(b < 9 or 13 < b < 32 for b in sample) < len(sample) * 0.01


# ---------------------------------------------------------------- platform handlers

@dataclass
class Found:
    url: str                       # where it came from (for display; may be session-bound)
    filename: str
    folder: str | None = None      # e.g. "Vergabeunterlagen/Version 2" (amendment rounds)
    listed_size: str | None = None
    listed_date: str | None = None
    data: bytes | None = None
    content_type: str | None = None
    error: str | None = None


async def evergabe_online(client: httpx.AsyncClient, url: str) -> tuple[list[tuple[str, str, str]], list[Found]]:
    """e-Vergabe Bund: documents sit behind Wicket session links on tenderdocuments.html; download them in the
    same session with a Referer. Also captures the details page."""
    tid = (parse_qs(urlparse(url).query).get("id") or [None])[0]
    if not tid:
        return [], []
    pages = []
    for page in ("tenderdetails", "tenderdocuments"):
        r = await client.get(f"https://www.evergabe-online.de/{page}.html?id={tid}")
        title, md = html_to_markdown(r.text, str(r.url))
        pages.append((f"https://www.evergabe-online.de/{page}.html?id={tid}", title, md))
    docs_page = r
    tree = LexborHTMLParser(docs_page.text)
    found = []
    for row in tree.css("table tr"):
        cells = row.css("td")
        a = row.css_first("a[href*=downloadLink]")
        if not cells or a is None:
            continue
        path = cells[0].text(strip=True)
        folder, _, filename = path.rpartition("/")
        f = Found(url=f"https://www.evergabe-online.de/tenderdocuments.html?id={tid}#{path}", filename=filename,
                  folder=folder or None, listed_size=cells[1].text(strip=True) if len(cells) > 1 else None,
                  listed_date=cells[2].text(strip=True) if len(cells) > 2 else None)
        try:
            d = await client.get(str(docs_page.url.join(a.attributes["href"])), headers={"Referer": str(docs_page.url)})
            if d.status_code == 200 and "text/html" not in d.headers.get("content-type", ""):
                f.data, f.content_type = d.content, d.headers.get("content-type")
                if m := re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", d.headers.get("content-disposition", "")):
                    f.filename = unquote(m.group(1))
            else:
                f.error = f"HTTP {d.status_code}"
        except httpx.HTTPError as e:
            f.error = f"{type(e).__name__}: {e}"
        found.append(f)
    return pages, found


def _ted_html(url: str) -> str | None:
    """TED's detail page is a JavaScript app; its server-rendered notice lives at /<lang>/notice/<pn>/html."""
    if m := re.search(r"ted\.europa\.eu/\w+/notice/-/detail/(\d+-\d{4})", url):
        return f"https://ted.europa.eu/en/notice/{m.group(1)}/html"
    return None


def _ted_page(html: str, base: str) -> tuple[str, str]:
    """TED's server-rendered notice: the generic conversion drops its header (class "header"), which holds the
    notice number, OJ issue and notice type, and the <title> is only the OJ issue ("210/2026"). Put the header
    back as the lead and title the page with the procedure title (BT-21)."""
    title, md = html_to_markdown(html, base)
    tree = LexborHTMLParser(html)
    lead: list[str] = []
    if (head := tree.css_first(".header-content")) is not None:
        for n in head.css(".bold"):
            t = " ".join(n.text(separator=" ").split())
            if t and t not in lead:
                lead.append(t)
    name = None
    for key in ("BT-21-Procedure", "BT-21-Lot", "BT-21-Part"):
        if (label := tree.css_first(f'[data-labels-key="field|name|{key}"]')) is not None:
            data = label.parent.css_first(".data") if label.parent else None
            if data is not None and (name := " ".join(data.text(separator=" ").split())):
                break
    pn = re.search(r"/notice/(\d+-\d{4})/", base)
    if not name and len(lead) > 1:  # "Germany – Medical equipments … – <title>"
        name = lead[1].split(" – ")[-1].strip()
    if name:
        # "Germany – <CPV> – <title>": the title is the heading already
        lead = [x[: -len(name)].rstrip(" –") if x.endswith(name) and x != name else x for x in lead if x != name]
        title = f"{name} – TED {pn.group(1)}" if pn else name
        md = f"# {name}\n\n" + (f"_{' · '.join(lead)}_\n\n" if lead else "") + md
    return title[:300], md


# ---------------------------------------------------------------- eForms notice -> markdown
#
# The Bekanntmachungsservice notice page (oeffentlichevergabe.de/ui/...) is a JavaScript app, but the same notice
# is served as eForms XML by its open API. Any eForms notice (TED, national German notices, sdk-0.1 below-threshold
# notices) is rendered here as a readable page: a summary, a lot overview, the terms common to all lots, each lot,
# results, review bodies, organisations, and — for change notices — what changed against the previous version.

OV_NOTICE_API = "https://oeffentlichevergabe.de/api/notices"
MAX_HISTORY = 5  # previous versions followed through ChangedNoticeIdentifier
EF_NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "efac": "http://data.europa.eu/p27/eforms-ubl-extension-aggregate-components/1",
    "efbc": "http://data.europa.eu/p27/eforms-ubl-extension-basic-components/1",
    "efext": "http://data.europa.eu/p27/eforms-ubl-extensions/1",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
}
EF_EXT = "ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent/efext:EformsExtension"
_EF_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, remove_comments=True)

_PERMISSION = {"allowed": "Allowed", "not-allowed": "Not allowed", "required": "Required"}
CODE_LABELS: dict[str, dict[str, str]] = {
    "notice-type": {  # NoticeTypeCode
        "pin-only": "Prior information notice", "pin-buyer": "Prior information notice (buyer profile)",
        "pin-rtl": "Prior information notice (shortening time limits)", "pin-cfc-standard": "Prior information notice "
        "used as a call for competition", "pin-cfc-social": "Prior information notice used as a call for competition "
        "(light regime)", "qu-sy": "Qualification system notice", "pin-tran": "Pre-information notice (public "
        "passenger transport)", "cn-standard": "Contract or concession notice – standard regime",
        "cn-social": "Contract notice – light regime", "cn-desg": "Design contest notice", "subco": "Subcontracting "
        "notice", "veat": "Voluntary ex-ante transparency notice", "can-standard": "Contract or concession award notice "
        "– standard regime", "can-social": "Contract award notice – light regime", "can-desg": "Design contest result "
        "notice", "can-tran": "Contract award notice (public passenger transport)", "can-modif": "Contract "
        "modification notice", "corr": "Change notice"},
    "procurement-procedure-type": {
        "open": "Open procedure", "restricted": "Restricted procedure", "neg-w-call": "Negotiated procedure with prior "
        "call for competition", "neg-wo-call": "Negotiated procedure without prior call for competition",
        "comp-dial": "Competitive dialogue", "innovation": "Innovation partnership", "comp-tend": "Competitive "
        "tendering", "oth-single": "Other single stage procedure", "oth-mult": "Other multiple stage procedure"},
    "contract-nature": {"supplies": "Supplies", "services": "Services", "works": "Works", "combined": "Combined"},
    "award-criterion-type": {"price": "Price", "cost": "Cost", "quality": "Quality"},
    "framework-agreement": {
        "fa-mix": "Framework agreement, partly without reopening and partly with reopening of competition",
        "fa-w-rc": "Framework agreement, with reopening of competition",
        "fa-wo-rc": "Framework agreement, without reopening of competition", "none": "None"},
    "dps-usage": {"dps-list": "Dynamic purchasing system, only usable by buyers listed in this notice",
                  "dps-nlist": "Dynamic purchasing system, also usable by buyers not listed", "none": "None"},
    "buyer-legal-type": {
        "cga": "Central government authority", "ra": "Regional authority", "la": "Local authority",
        "body-pl": "Body governed by public law", "body-pl-cga": "Body governed by public law, controlled by a "
        "central government authority", "body-pl-ra": "Body governed by public law, controlled by a regional "
        "authority", "body-pl-la": "Body governed by public law, controlled by a local authority",
        "pub-undert": "Public undertaking", "pub-undert-cga": "Public undertaking, controlled by a central government "
        "authority", "pub-undert-ra": "Public undertaking, controlled by a regional authority",
        "pub-undert-la": "Public undertaking, controlled by a local authority", "def-cont": "Defence contractor",
        "eu-ins-bod-ag": "EU institution, body or agency", "int-org": "International organisation",
        "grp-p-aut": "Group of public authorities", "spec-rights-entity": "Entity with special or exclusive rights",
        "org-sub": "Organisation awarding a contract subsidised by a contracting authority",
        "rl-aut": "Regional or local authority",
        "omu-bbeh": "Central government authority (omu-bbeh)"},  # eForms-DE code; TED shows it as cga
    "authority-activity": {
        "gen-pub": "General public services", "defence": "Defence", "pub-os": "Public order and safety",
        "env": "Environmental protection", "econ-aff": "Economic affairs", "health": "Health",
        "hc-am": "Housing and community amenities", "soc-pro": "Social protection",
        "rcr": "Recreation, culture and religion", "educ": "Education"},
    "winner-selection-status": {
        "selec-w": "At least one winner was chosen", "clos-nw": "No winner was chosen and the competition is closed",
        "open-nw": "No winner was chosen yet, the competition is still open"},
    "non-award-justification": {
        "no-rece": "No tenders, requests to participate or projects were received",
        "all-rej": "All tenders, requests to participate or projects were withdrawn or found inadmissible",
        "chan-need": "Decision of the buyer, because of a change in needs", "ins-fund": "Insufficient funds",
        "oth-int": "Other reason (interruption of the procedure)"},
    "received-submission-type": {
        "tenders": "Tenders received", "t-sme": "Tenders from SMEs", "t-esubm": "Tenders submitted electronically",
        "t-no-eea": "Tenders from outside the EEA", "t-oth-eea": "Tenders from other EEA countries",
        "t-verif-inad": "Tenders verified and found inadmissible", "t-no-verif": "Tenders not verified",
        "t-med": "Tenders with medium/high value", "part-req": "Requests to participate received"},
    "change-corrig-justification": {
        "update-add": "Updated or additional information", "cor-buy": "Correction by the buyer",
        "cor-esen": "Correction by the eSender", "cor-pub": "Correction by the Publications Office",
        "info-release": "Information released", "add-info": "Additional information"},
    "esubmission": _PERMISSION, "permission": _PERMISSION, "einvoicing": _PERMISSION,
    "ecatalog-submission": _PERMISSION,
    "eu-funded": {"no-eu-funds": "No", "eu-funds": "Yes"},
    "missing-info-submission": {
        "late-all": "Tenderers may be asked to submit missing tenderer-related documents later",
        "late-none": "Missing tenderer-related documents may not be submitted later",
        "late-some": "Some missing tenderer-related documents may be submitted later"},
    "reserved-procurement": {"none": "Not reserved", "res-pub-ser": "Reserved to organisations pursuing a public "
                             "service mission", "res-ws": "Reserved to sheltered workshops and social businesses"},
    "selection-criterion": {"ef-stand": "Economic and financial standing", "sui-act": "Suitability to pursue the "
                            "professional activity", "tp-abil": "Technical and professional ability", "other": "Other"},
    "strategic-procurement": {"none": "None", "env-imp": "Reduction of environmental impacts",
                              "inn-pur": "Innovative purchase", "soc-obj": "Social objective"},
    "exclusion-ground": {
        "bankr-nat": "Bankruptcy", "corruption": "Corruption", "crime-org": "Participation in a criminal organisation",
        "distorsion": "Agreements with other operators to distort competition",
        "envir-law": "Breaching of obligations in the fields of environmental law", "finan-laund": "Money laundering "
        "or terrorist financing", "fraud": "Fraud", "human-traffic": "Child labour and human trafficking",
        "insolvency": "Insolvency", "labour-law": "Breaching of obligations in the fields of labour law",
        "liq-admin": "Assets being administered by a liquidator", "misrepresent": "Misrepresentation, withheld "
        "information, unable to provide required documents", "nati-ground": "Purely national exclusion grounds",
        "partic-confl": "Conflict of interest due to participation", "prep-confl": "Direct or indirect involvement "
        "in the preparation of the procedure", "prof-misconduct": "Guilty of grave professional misconduct",
        "sanction": "Early termination, damages or other comparable sanctions", "socsec-law": "Breaching of "
        "obligations in the fields of social law", "socsec-pay": "Breaching of obligations relating to payment of "
        "social security contributions", "susp-act": "Business activities are suspended", "tax-pay": "Breaching of "
        "obligations relating to payment of taxes", "terr-offence": "Terrorist offences or offences linked to "
        "terrorist activities"},
}
REGIONS = {"anyw": "Anywhere", "anyw-cou": "Anywhere in the given country",
           "anyw-eea": "Anywhere in the European Economic Area"}
LEGAL_BASIS = {"32014L0024": "Directive 2014/24/EU", "32014L0025": "Directive 2014/25/EU (utilities)",
               "32014L0023": "Directive 2014/23/EU (concessions)", "32009L0081": "Directive 2009/81/EC (defence)",
               "32007R1370": "Regulation (EC) 1370/2007 (public passenger transport)", "32018R1046": "EU financial "
               "regulation 2018/1046"}
NATIONAL_BASIS = {"vgv": "VgV", "vsvgv": "VSVgV", "sektvo": "SektVO", "konzvgv": "KonzVgV", "uvgo": "UVgO",
                  "vob-a": "VOB/A", "vob-a-eu": "VOB/A-EU", "vob-a-vs": "VOB/A-VS", "svhv": "SVHV"}
COUNTRIES = {"DEU": "Germany", "AUT": "Austria", "CHE": "Switzerland", "FRA": "France", "POL": "Poland",
             "NLD": "Netherlands", "BEL": "Belgium", "LUX": "Luxembourg", "DNK": "Denmark", "CZE": "Czechia",
             "ITA": "Italy", "ESP": "Spain"}
LANGUAGES = {"DEU": "German", "ENG": "English", "FRA": "French", "POL": "Polish", "NLD": "Dutch", "ITA": "Italian",
             "SPA": "Spanish", "CES": "Czech", "DAN": "Danish"}
UNITS = {"DAY": ("day", "days"), "WEEK": ("week", "weeks"), "MONTH": ("month", "months"), "YEAR": ("year", "years")}
# "LOS 1 - 2.400 EA Endobronchialblocker-Set, 9Fr": the quantity sits in the lot title
_TITLE_QTY = re.compile(r"^(?:LOS|Lot|Teillos|Fachlos)\s*\d+\s*[-–:]\s*(\d[\d.]*(?:,\d+)?)\s+"
                        r"(EA|ST|STK|Stk\.?|Stück|PCE|PCS|PAK|Pkg\.?|VE|KG|L|M|M2|M²|M3|M³|T|Std\.?)\s+(\S.*)$", re.I)
_URL = re.compile(r"(?<![(\[])\bhttps?://[^\s<>\"')\]]+[^\s<>\"')\].,;:]")
_DIFF_SKIP = {"notice", "change"}


def _label(list_name: str | None, code: str | None) -> str | None:
    if not code:
        return None
    return CODE_LABELS.get(list_name or "", {}).get(code) or code


def _ml(s: str | None) -> str | None:
    """Multi-line text: whitespace collapsed per line, runs of empty lines dropped."""
    lines = [" ".join(line.replace("\xa0", " ").split()) for line in (s or "").splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip() or None


def _linkify(s: str) -> str:
    return _URL.sub(lambda m: f"[{m.group(0)}]({m.group(0)})", s)


def _line(s: str) -> str:
    """A line of notice text must not turn into a heading, table or emphasis line of the markdown."""
    return " " + s if s.startswith(("#", "|", "_", "---")) else s


def _when(d: str | None, t: str | None = None) -> str | None:
    """'2026-11-20+01:00', '08:00:00+01:00' -> '2026-11-20 08:00 (UTC+01:00)'."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})(Z|[+-]\d{2}:\d{2})?", (d or "").strip())
    if not m:
        return _ml(d)
    out = m.group(1)
    if tm := re.match(r"(\d{2}:\d{2})(?::\d{2})?(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?", (t or "").strip()):
        tz = tm.group(2) or m.group(2)
        out += f" {tm.group(1)}" + (f" (UTC{'' if tz == 'Z' else tz})" if tz else "")
    return out


def _num(v: str | None) -> str | None:
    try:
        f = float((v or "").strip())
    except ValueError:
        return _ml(v)
    return f"{f:,.0f}" if f == int(f) else f"{f:,.2f}"


class _Eforms:
    """XPath helpers over one eForms notice, with text picked in the notice language."""

    def __init__(self, root: Any) -> None:
        self.root = root
        self.lang = self.t(root, "cbc:NoticeLanguageCode")
        self.orgs: dict[str, Any] = {}
        for org in self.x(root, f"{EF_EXT}/efac:Organizations/efac:Organization"):
            for el in self.x(org, "efac:Company | efac:TouchPoint"):
                if oid := self.t(el, "cac:PartyIdentification/cbc:ID"):
                    self.orgs.setdefault(oid, el)

    def x(self, el: Any, path: str) -> list[Any]:
        return el.xpath(path, namespaces=EF_NS) if el is not None else []

    def first(self, el: Any, path: str) -> Any:
        return next(iter(self.x(el, path)), None)

    def t(self, el: Any, path: str) -> str | None:
        best = None
        for n in self.x(el, path):
            s = _ml(n if isinstance(n, str) else n.text)
            if not s:
                continue
            if isinstance(n, str) or not n.get("languageID") or n.get("languageID") == getattr(self, "lang", None):
                return s
            best = best or s
        return best

    def all(self, el: Any, path: str) -> list[str]:
        return list(dict.fromkeys(s for n in self.x(el, path) if (s := _ml(n if isinstance(n, str) else n.text))))

    def code(self, el: Any, path: str) -> str | None:
        n = self.first(el, path)
        return _label(n.get("listName"), _ml(n.text)) if n is not None else None

    def money(self, el: Any, path: str) -> str | None:
        n = self.first(el, path)
        if n is None or not _ml(n.text):
            return None
        return f"{_num(n.text)} {n.get('currencyID') or ''}".strip()

    def duration(self, el: Any, path: str) -> str | None:
        n = self.first(el, path)
        if n is None or not _ml(n.text):
            return None
        one, many = UNITS.get(n.get("unitCode") or "", (n.get("unitCode") or "", n.get("unitCode") or ""))
        v = _num(n.text)
        return f"{v} {one if v == '1' else many}".strip()

    def yes(self, el: Any, path: str) -> str | None:
        v = self.t(el, path)
        return {"true": "Yes", "false": "No"}.get(v or "", v)

    # --- organisations

    def org(self, oid: str | None) -> Any:
        return self.orgs.get(oid or "")

    def org_name(self, oid: str | None) -> str | None:
        el = self.org(oid)
        if el is None:
            return oid
        name = self.t(el, "cac:PartyName/cbc:Name")
        city = self.t(el, "cac:PostalAddress/cbc:CityName")
        return f"{name} ({city})" if name and city else name or oid

    def org_fields(self, el: Any) -> list[tuple[str, Any]]:
        from .connectors.oeffentlichevergabe import org_identifier
        f: list[tuple[str, Any]] = [("Official name", self.t(el, "cac:PartyName/cbc:Name"))]
        reg = org_identifier(self.t(el, "cac:PartyLegalEntity/cbc:CompanyID"),
                             [self.t(el, "cac:Contact/cbc:Telephone"), self.t(el, "cac:Contact/cbc:Telefax")])
        f.append(("Registration number", reg))
        a = self.first(el, "cac:PostalAddress")
        street = ", ".join(self.all(a, "cbc:StreetName | cbc:AdditionalStreetName | cac:AddressLine/cbc:Line"))
        town = " ".join(x for x in (self.t(a, "cbc:PostalZone"), self.t(a, "cbc:CityName")) if x)
        country = self.t(a, "cac:Country/cbc:IdentificationCode")
        nuts = self.t(a, "cbc:CountrySubentityCode")
        addr = ", ".join(x for x in (street, town, COUNTRIES.get(country or "", country)) if x)
        f.append(("Address", addr + (f" (NUTS {nuts})" if nuts and addr else "")))
        f.append(("Contact point", self.t(el, "cac:Contact/cbc:Name")))
        f.append(("Telephone", self.t(el, "cac:Contact/cbc:Telephone")))
        f.append(("Fax", self.t(el, "cac:Contact/cbc:Telefax")))
        if mail := self.t(el, "cac:Contact/cbc:ElectronicMail"):
            f.append(("Email", f"[{mail}](mailto:{mail})"))
        f.append(("Internet address", self.t(el, "cbc:WebsiteURI")))
        f.append(("Buyer profile", self.t(el, "cbc:EndpointID")))
        return f


@dataclass
class _Section:
    key: str                                   # stable across versions: "procedure", "lot:LOT-0001", ...
    heading: str
    fields: list[tuple[str, Any]] = field(default_factory=list)   # value: str or list[str] (rendered as a list)
    blocks: list[str] = field(default_factory=list)               # extra markdown after the fields (tables)


def _fields_md(fields: list[tuple[str, Any]]) -> list[str]:
    out = []
    for label, value in fields:
        if not value:
            continue
        if isinstance(value, list):
            out.append(f"{label}:\n" + "\n".join(f"- {_linkify(v)}" for v in value))
        else:
            lines = _linkify(str(value)).split("\n")
            out.append("\n".join([f"{label}: {lines[0]}", *(_line(x) for x in lines[1:] if x)]))
    return out


def _criteria(ef: _Eforms, tt: Any) -> list[str]:
    out = []
    for c in ef.x(tt, "cac:AwardingTerms/cac:AwardingCriterion/cac:SubordinateAwardingCriterion"):
        kind = ef.code(c, "cbc:AwardingCriterionTypeCode")
        name = ef.t(c, "cbc:Name")
        desc = ef.t(c, "cbc:Description")
        weight = None
        for p in ef.x(c, f"{EF_EXT}/efac:AwardCriterionParameter"):
            code, val = ef.t(p, "efbc:ParameterCode"), _num(ef.t(p, "efbc:ParameterNumeric"))
            if val:
                weight = (f"{val} %" if code in ("per-exa", "per-rou", None) else
                          f"{val} points" if code in ("poi-exa", "poi-rou") else f"{val} ({code})")
        head = name if name and (not kind or kind.lower() == name.lower()) else \
            f"{name} ({kind})" if name else kind or "Criterion"
        line = head + (f": {weight}" if weight else "")
        if desc and desc not in (name, kind):
            line += f" — {desc}".replace("\n", " ")
        out.append(line)
    if d := ef.t(tt, "cac:AwardingTerms/cac:AwardingCriterion/cbc:Description"):
        out.append(d.replace("\n", " "))
    return out


def _places(ef: _Eforms, pp: Any) -> list[str]:
    out = []
    for loc in ef.x(pp, "cac:RealizedLocation"):
        a = ef.first(loc, "cac:Address")
        parts = [", ".join(ef.all(a, "cbc:StreetName")),
                 " ".join(x for x in (ef.t(a, "cbc:PostalZone"), ef.t(a, "cbc:CityName")) if x),
                 REGIONS.get(ef.t(a, "cbc:Region") or "", ef.t(a, "cbc:Region") or ""),
                 COUNTRIES.get(c := ef.t(a, "cac:Country/cbc:IdentificationCode") or "", c)]
        line = ", ".join(p for p in parts if p)
        if nuts := ef.t(a, "cbc:CountrySubentityCode"):
            line += f" (NUTS {nuts})"
        if desc := ef.t(loc, "cbc:Description"):
            line = f"{line} — {desc}" if line else desc
        if line:
            out.append(line.replace("\n", " "))
    return out


def _cpvs(ef: _Eforms, pp: Any) -> tuple[str | None, list[str]]:
    main = ef.t(pp, "cac:MainCommodityClassification/cbc:ItemClassificationCode")
    extra = [c for c in ef.all(pp, "cac:AdditionalCommodityClassification/cbc:ItemClassificationCode") if c != main]
    return main, extra


def _lot_fields(ef: _Eforms, lot: Any) -> tuple[list[tuple[str, Any]], dict[str, Any]]:
    """Label/value fields of one lot, plus loose facts for the summary and the overview table."""
    pp = ef.first(lot, "cac:ProcurementProject")
    tt = ef.first(lot, "cac:TenderingTerms")
    tp = ef.first(lot, "cac:TenderingProcess")
    title = ef.t(pp, "cbc:Name")
    desc = ef.t(pp, "cbc:Description")
    if desc and title and desc.startswith(title):  # "LOS 1 - … 9Fr\ngemäß\nVM191_05_Anlage 2…" -> "gemäß VM191_05…"
        desc = " ".join(desc[len(title):].split()) or None
    qty = None
    if (q := ef.first(pp, "cbc:EstimatedQuantity")) is not None and _ml(q.text):
        qty = f"{_num(q.text)} {q.get('unitCode') or ''}".strip()
    short = title
    if title and (m := _TITLE_QTY.match(title)):
        qty = qty or f"{m.group(1)} {m.group(2)} (from the lot title)"
        short = m.group(3)
    main, extra = _cpvs(ef, pp)
    start, end = ef.t(pp, "cac:PlannedPeriod/cbc:StartDate"), ef.t(pp, "cac:PlannedPeriod/cbc:EndDate")
    period = (f"{_when(start)} to {_when(end)}" if start and end else
              f"from {_when(start)}" if start else f"until {_when(end)}" if end else None)
    dur = ef.duration(pp, "cac:PlannedPeriod/cbc:DurationMeasure")
    if code := ef.t(pp, "cac:PlannedPeriod/cbc:DescriptionCode"):
        dur = dur or {"UNLIMITED": "Unlimited", "UNKNOWN": "Unknown"}.get(code, code)
    deadline = _when(ef.t(tp, "cac:TenderSubmissionDeadlinePeriod/cbc:EndDate"),
                     ef.t(tp, "cac:TenderSubmissionDeadlinePeriod/cbc:EndTime"))
    requests = _when(ef.t(tp, "cac:ParticipationRequestReceptionPeriod/cbc:EndDate"),
                     ef.t(tp, "cac:ParticipationRequestReceptionPeriod/cbc:EndTime"))
    opening = _when(ef.t(tp, "cac:OpenTenderEvent/cbc:OccurrenceDate"), ef.t(tp, "cac:OpenTenderEvent/cbc:OccurrenceTime"))
    if opening and (where := ef.t(tp, "cac:OpenTenderEvent/cbc:Description")):
        opening += f" — {where}"
    docs = []
    for ref in ef.x(tt, "cac:CallForTendersDocumentReference"):
        for u in ef.all(ref, "cac:Attachment/cac:ExternalReference/cbc:URI"):
            kind = ef.t(ref, "cbc:DocumentType")
            docs.append(u + (" (restricted access)" if kind == "restricted-document" else ""))
    submit = ef.all(tt, "cac:TenderRecipientParty/cbc:EndpointID")
    criteria = _criteria(ef, tt)
    fa = ef.first(tp, "cac:ContractingSystem/cbc:ContractingSystemTypeCode[@listName='framework-agreement']")
    framework = _label("framework-agreement", _ml(fa.text)) if fa is not None else None
    dps = ef.first(tp, "cac:ContractingSystem/cbc:ContractingSystemTypeCode[@listName='dps-usage']")
    selection = [f"{_label('selection-criterion', ef.t(s, 'cbc:CriterionTypeCode'))}"
                 + (f": {n}" if (n := ef.t(s, "cbc:Name") or ef.t(s, "cbc:Description")) else "")
                 for s in ef.x(tt, f"{EF_EXT}/efac:SelectionCriteria")]
    reqs = {}
    for r in ef.x(tt, "cac:TendererQualificationRequest/cac:SpecificTendererRequirement"):
        n = ef.first(r, "cbc:TendererRequirementTypeCode")
        if n is not None:
            reqs[n.get("listName")] = (_ml(n.text), ef.t(r, "cbc:Description"))
    if reqs.get("selection-criteria-source", ("",))[0] == "epo-procurement-document":
        selection.append("As stated in the procurement documents")
    missing = reqs.get("missing-info-submission")
    reserved = reqs.get("reserved-procurement")
    exec_req = {n.get("listName"): _ml(n.text) for n in ef.x(tt, "cac:ContractExecutionRequirement/cbc:ExecutionRequirementCode")}
    strategic = [_label("strategic-procurement", c) for c in
                 ef.all(pp, "cac:ProcurementAdditionalType/cbc:ProcurementTypeCode[@listName='strategic-procurement']")
                 if c != "none"]
    guarantee = None
    if ef.t(tt, "cac:RequiredFinancialGuarantee/cbc:GuaranteeTypeCode") == "true":
        guarantee = ef.t(tt, "cac:RequiredFinancialGuarantee/cbc:Description") or "Required"
    max_ops = ef.t(tp, "cac:FrameworkAgreement/cbc:MaximumOperatorQuantity")
    fields: list[tuple[str, Any]] = [
        ("Title", title),
        ("Description", desc),
        ("Quantity", qty),
        ("Main classification (CPV)", main),
        ("Additional classification (CPV)", ", ".join(extra) or None),
        ("Nature of the contract", ef.code(pp, "cbc:ProcurementTypeCode")),
        ("Place of performance", _places(ef, pp)),
        ("Contract period", period),
        ("Duration", dur),
        ("Options", ef.t(pp, "cac:ContractExtension/cbc:OptionsDescription")),
        ("Maximum number of renewals", ef.t(pp, "cac:ContractExtension/cbc:MaximumNumberNumeric")),
        ("Renewals", ef.t(pp, "cac:ContractExtension/cac:Renewal/cac:Period/cbc:Description")),
        ("Estimated value", ef.money(pp, "cac:RequestedTenderTotal/cbc:EstimatedOverallContractAmount")),
        ("Framework agreement", framework if framework != "None" else None),
        ("Maximum number of framework participants",
         ("Unlimited (999)" if max_ops == "999" else max_ops) if framework and framework != "None" else None),
        ("Dynamic purchasing system", _label("dps-usage", _ml(dps.text)) if dps is not None and _ml(dps.text) != "none" else None),
        ("Electronic auction", "Yes" if ef.t(tp, "cac:AuctionTerms/cbc:AuctionConstraintIndicator") == "true" else None),
        ("Deadline for receipt of tenders", deadline),
        ("Deadline for receipt of requests to participate", requests),
        ("Deadline for questions", _when(ef.t(tp, "cac:AdditionalInformationRequestPeriod/cbc:EndDate"),
                                         ef.t(tp, "cac:AdditionalInformationRequestPeriod/cbc:EndTime"))),
        ("Opening of tenders", opening),
        ("Tenders must remain valid for", ef.duration(tt, "cac:TenderValidityPeriod/cbc:DurationMeasure")),
        ("Award criteria", criteria),
        ("Selection criteria", selection),
        ("Missing documents", "\n".join(x for x in (_label("missing-info-submission", missing[0]), missing[1]) if x)
         if missing else None),
        ("Financial guarantee (vadium)", guarantee),
        ("Variants", ef.code(tt, "cbc:VariantConstraintCode")),
        ("Several tenders per tenderer", ef.code(tt, "cbc:MultipleTendersCode")),
        ("Electronic submission", ef.code(tp, "cbc:SubmissionMethodCode")),
        ("Languages of tenders", ", ".join(LANGUAGES.get(x, x) for x in ef.all(tt, "cac:Language/cbc:ID")) or None),
        ("Electronic invoicing", _label("einvoicing", exec_req.get("einvoicing"))),
        ("Electronic catalogue", _label("ecatalog-submission", exec_req.get("ecatalog-submission"))),
        ("Electronic ordering", ef.yes(tt, "cac:PostAwardProcess/cbc:ElectronicOrderUsageIndicator")),
        ("Electronic payment", ef.yes(tt, "cac:PostAwardProcess/cbc:ElectronicPaymentUsageIndicator")),
        ("Reserved procurement", _label("reserved-procurement", reserved[0])
         if reserved and reserved[0] != "none" else None),
        ("Strategic procurement", ", ".join(strategic) or None),
        ("Suitable for SMEs", ef.yes(pp, "cbc:SMESuitableIndicator")),
        ("Covered by the GPA", ef.yes(tp, "cbc:GovernmentAgreementConstraintIndicator")),
        ("EU funds", ef.code(tt, "cbc:FundingProgramCode")),
        ("Recurring procurement", ef.yes(tt, "cbc:RecurringProcurementIndicator")),
        ("Procurement documents", docs),
        ("Submit tenders at", submit),
        ("Additional information from", ef.org_name(ef.t(tt, "cac:AdditionalInformationParty/cac:PartyIdentification/cbc:ID"))),
        ("Review body", ef.org_name(ef.t(tt, "cac:AppealTerms/cac:AppealReceiverParty/cac:PartyIdentification/cbc:ID"))),
        ("Additional information", ef.t(pp, "cbc:Note")),
    ]
    facts = {"title": title, "short": short, "qty": qty, "cpv": main, "deadline": deadline or requests,
             "period": period or dur, "docs": docs, "submit": submit, "criteria": criteria,
             "review_info": ef.t(tt, "cac:AppealTerms/cac:PresentationPeriod/cbc:Description"),
             "mediator": ef.t(tt, "cac:AppealTerms/cac:MediationParty/cac:PartyIdentification/cbc:ID"),
             "reviewer": ef.t(tt, "cac:AppealTerms/cac:AppealReceiverParty/cac:PartyIdentification/cbc:ID")}
    return [(k, v) for k, v in fields if v], facts


def _results(ef: _Eforms) -> tuple[list[_Section], list[str]]:
    """Award results: per lot the outcome, number of tenders, winners and values. Returns (sections, winners)."""
    res = ef.first(ef.root, f"{EF_EXT}/efac:NoticeResult")
    if res is None:
        return [], []
    tenders = {ef.t(t, "cbc:ID"): t for t in ef.x(res, "efac:LotTender")}
    parties = {ef.t(p, "cbc:ID"): ef.all(p, "efac:Tenderer/cbc:ID") for p in ef.x(res, "efac:TenderingParty")}
    contracts = {ef.t(c, "cbc:ID"): c for c in ef.x(res, "efac:SettledContract")}
    sections, winners = [], []
    head = _Section("results", "Results", [("Total value of the contracts", ef.money(res, "cbc:TotalAmount"))])
    sections.append(head)
    for lr in ef.x(res, "efac:LotResult"):
        lot = ef.t(lr, "efac:TenderLot/cbc:ID")
        stats = {ef.t(s, "efbc:StatisticsCode"): ef.t(s, "efbc:StatisticsNumeric")
                 for s in ef.x(lr, "efac:ReceivedSubmissionsStatistics")}
        names, values = [], []
        for tid in ef.all(lr, "efac:LotTender/cbc:ID"):
            t = tenders.get(tid)
            for oid in parties.get(ef.t(t, "efac:TenderingParty/cbc:ID"), []):
                if (n := ef.org_name(oid)) and n not in names:
                    names.append(n)
            if v := ef.money(t, "cac:LegalMonetaryTotal/cbc:PayableAmount"):
                values.append(v)
        concluded = [_when(ef.t(contracts.get(cid), "cbc:IssueDate")) for cid in ef.all(lr, "efac:SettledContract/cbc:ID")]
        winners += [n for n in names if n not in winners]
        sections.append(_Section(f"result:{lot}", f"Result: {lot}", [
            ("Outcome", ef.code(lr, "cbc:TenderResultCode")),
            ("Reason", ef.code(lr, "efac:DecisionReason/efbc:DecisionReasonCode")),
            ("Tenders received", stats.get("tenders")),
            ("Requests to participate received", stats.get("part-req")),
            ("Winner", names),
            ("Value of the winning tender", ", ".join(values) or None),
            ("Lowest / highest tender", " / ".join(x for x in (ef.money(lr, "cbc:LowerTenderAmount"),
                                                               ef.money(lr, "cbc:HigherTenderAmount")) if x) or None),
            ("Contract concluded", ", ".join(c for c in concluded if c) or None),
        ]))
    return sections, winners


def _eforms_sections(root: Any) -> tuple[dict[str, Any], list[_Section]]:
    """The notice as titled sections of label/value fields (rendered by eforms_to_markdown, diffed for changes)."""
    ef = _Eforms(root)
    nt = ef.first(root, "cbc:NoticeTypeCode")
    form = nt.get("listName") if nt is not None else None
    project = ef.first(root, "cac:ProcurementProject")
    lots = [lot for lot in ef.x(root, "cac:ProcurementProjectLot")
            if (ef.first(lot, "cbc:ID") is None or ef.first(lot, "cbc:ID").get("schemeName") != "LotsGroup")]
    title = ef.t(project, "cbc:Name") or next((t for lot in lots if (t := ef.t(lot, "cac:ProcurementProject/cbc:Name"))), None)
    meta = {"title": title, "id": ef.t(root, "cbc:ID"), "version": ef.t(root, "cbc:VersionID"),
            "issued": _when(ef.t(root, "cbc:IssueDate"), ef.t(root, "cbc:IssueTime"))
            or _when(ef.t(root, "cbc:RequestedPublicationDate")),
            "type": _label("notice-type", _ml(nt.text) if nt is not None else None), "form": form}
    sections: list[_Section] = []

    # notice
    pub = ef.first(root, f"{EF_EXT}/efac:Publication")
    ted = ef.t(pub, "efbc:NoticePublicationID")
    ted = ted if ted and not ted.startswith("00000000") else None
    reg = ef.t(root, "cbc:RegulatoryDomain")
    national = [NATIONAL_BASIS.get(x.lower(), x) for x in
                ef.all(root, "cac:TenderingTerms/cac:ProcurementLegislationDocumentReference/cbc:ID")
                if x.upper() not in ("CROSSBORDERLAW", "LOCALLEGALBASIS")]
    sections.append(_Section("notice", "Notice", [
        ("Notice type", meta["type"]),
        ("Notice subtype", ef.t(root, f"{EF_EXT}/efac:NoticeSubType/cbc:SubTypeCode")),
        ("Notice ID", meta["id"]), ("Version", meta["version"]),
        ("Procedure ID", ef.t(root, "cbc:ContractFolderID")),
        ("Issued", meta["issued"]),
        ("Language", LANGUAGES.get(ef.lang or "", ef.lang)),
        ("Legal basis", LEGAL_BASIS.get(reg or "", reg)),
        ("National legal basis", ", ".join(national) or None),
        ("Published on TED", f"[{ted.lstrip('0')}](https://ted.europa.eu/en/notice/-/detail/{ted.lstrip('0')})"
         + (f" (OJ S {g}" + (f", {_when(d)})" if (d := ef.t(pub, 'efbc:PublicationDate')) else ")")
            if (g := ef.t(pub, "efbc:GazetteID")) else "") if ted else None),
    ]))

    # buyers
    buyer_names = []
    for cp in ef.x(root, "cac:ContractingParty"):
        oid = ef.t(cp, "cac:Party/cac:PartyIdentification/cbc:ID")
        el = ef.org(oid)
        if el is None:
            el = ef.first(cp, "cac:Party")  # below-threshold notices carry the buyer inline
        f = ef.org_fields(el)
        f += [("Legal type of the buyer", ef.code(cp, "cac:ContractingPartyType/cbc:PartyTypeCode")),
              ("Activity", ef.code(cp, "cac:ContractingActivity/cbc:ActivityTypeCode")),
              ("Procurement service provider",
               ef.org_name(ef.t(cp, "cac:Party/cac:ServiceProviderParty/cac:Party/cac:PartyIdentification/cbc:ID")))]
        buyer_names.append(ef.org_name(oid) if oid in ef.orgs else ef.t(el, "cac:PartyName/cbc:Name"))
        sections.append(_Section(f"buyer:{oid or len(buyer_names)}", "Buyer", [(k, v) for k, v in f if v]))

    # procedure
    tp = ef.first(root, "cac:TenderingProcess")
    tt = ef.first(root, "cac:TenderingTerms")
    accelerated = None
    for j in ef.x(tp, "cac:ProcessJustification"):
        n = ef.first(j, "cbc:ProcessReasonCode")
        if n is not None and n.get("listName") == "accelerated-procedure" and _ml(n.text) == "true":
            accelerated = "Yes" + (f" — {r}" if (r := ef.t(j, "cbc:ProcessReason")) else "")
    main, extra = _cpvs(ef, project)
    excl = [(c, ef.t(r, "cbc:Description")) for r in ef.x(tt, "cac:TendererQualificationRequest/cac:SpecificTendererRequirement")
            if (n := ef.first(r, "cbc:TendererRequirementTypeCode")) is not None and n.get("listName") == "exclusion-ground"
            and (c := _ml(n.text))]
    descs = {d for _, d in excl}
    exclusion_proof = None
    if len(descs) == 1:  # the usual case: one declaration covers all grounds
        exclusion = ", ".join(_label("exclusion-ground", c) for c, _ in excl) or None
        exclusion_proof = descs.pop()
    else:
        exclusion = [_label("exclusion-ground", c) + (f": {d}" if d else "") for c, d in excl]
    max_sub = ef.t(tt, "cac:LotDistribution/cbc:MaximumLotsSubmittedNumeric")
    max_awd = ef.t(tt, "cac:LotDistribution/cbc:MaximumLotsAwardedNumeric")
    lot_rule = "; ".join(x for x in (f"tenders may be submitted for up to {max_sub} lots" if max_sub else "",
                                     f"at most {max_awd} lots awarded to one tenderer" if max_awd else "") if x)
    proc_fields = [
        ("Title", title),
        ("Description", d if (d := ef.t(project, "cbc:Description")) != title else None),
        ("Internal identifier", ef.t(project, "cbc:ID")),
        ("Type of procedure", ef.code(tp, "cbc:ProcedureCode")),
        ("Accelerated procedure", accelerated),
        ("Main nature of the contract", ef.code(project, "cbc:ProcurementTypeCode")),
        ("Main classification (CPV)", main),
        ("Additional classification (CPV)", ", ".join(extra) or None),
        ("Place of performance", _places(ef, project)),
        ("Estimated value", ef.money(project, "cac:RequestedTenderTotal/cbc:EstimatedOverallContractAmount")),
        ("Maximum value of the framework agreement",
         ef.money(project, f"cac:RequestedTenderTotal/{EF_EXT}/efbc:FrameworkMaximumAmount")),
        ("Lots", f"{len(lots)}" + (f" ({lot_rule})" if lot_rule else "") if len(lots) > 1 else None),
        ("Exclusion grounds", exclusion),
        ("Exclusion grounds are declared in", exclusion_proof),
        ("Additional information", ef.t(project, "cbc:Note")),
    ]
    sections.append(_Section("procedure", "Procedure", [(k, v) for k, v in proc_fields if v]))

    # lots
    lot_data = []
    for lot in lots:
        fields, facts = _lot_fields(ef, lot)
        lot_data.append((ef.t(lot, "cbc:ID") or f"LOT-{len(lot_data) + 1:04d}", fields, facts))
    for lid, fields, _ in lot_data:
        sections.append(_Section(f"lot:{lid}", f"Lot: {lid}", fields))

    results, winners = _results(ef)
    sections += results

    # review
    review_fields: list[tuple[str, Any]] = []
    for key, label in (("reviewer", "Review body"), ("mediator", "Mediation body")):
        for oid in dict.fromkeys(f[key] for *_, f in lot_data if f[key]):
            review_fields.append((label, ef.org_name(oid)))
    for info in dict.fromkeys(f["review_info"] for *_, f in lot_data if f["review_info"]):
        review_fields.append(("Information about review deadlines", info))
    if review_fields:
        sections.append(_Section("review", "Review procedures", review_fields))

    # organisations other than the buyer, with their roles
    roles: dict[str, list[str]] = {}
    for path, role in (
            (".//cac:AdditionalInformationParty", "Provides additional information"),
            (".//cac:DocumentProviderParty", "Provides the procurement documents"),
            (".//cac:TenderRecipientParty", "Receives tenders"),
            (".//cac:TenderEvaluationParty", "Evaluates tenders"),
            (".//cac:AppealReceiverParty", "Review body"),
            (".//cac:MediationParty", "Mediation body"),
            (".//cac:AppealInformationParty", "Provides information about review procedures"),
            (".//cac:FiscalLegislationDocumentReference/cac:IssuerParty", "Information about tax law"),
            (".//cac:EnvironmentalLegislationDocumentReference/cac:IssuerParty", "Information about environmental law"),
            (".//cac:EmploymentLegislationDocumentReference/cac:IssuerParty", "Information about employment law"),
            (".//efac:TenderingParty/efac:Tenderer", "Tenderer")):
        for oid in ef.all(root, f"{path}/cac:PartyIdentification/cbc:ID | {path}/cbc:ID"):
            roles.setdefault(oid, [])
            if role not in roles[oid]:
                roles[oid].append(role)
    buyers = set(ef.all(root, "cac:ContractingParty/cac:Party/cac:PartyIdentification/cbc:ID"))
    merged: dict[str, tuple[list[tuple[str, Any]], list[str]]] = {}
    for oid in ef.orgs:
        if oid in buyers:
            continue
        f = [(k, v) for k, v in ef.org_fields(ef.orgs[oid]) if v]
        sig = repr([kv for kv in f if kv[0] in ("Official name", "Address")])  # same body listed twice: show once
        if sig in merged:
            merged[sig][1].extend(r for r in roles.get(oid, []) if r not in merged[sig][1])
        else:
            merged[sig] = (f, list(roles.get(oid, [])))
    for i, (f, r) in enumerate(merged.values()):
        name = dict(f).get("Official name") or "Organisation"
        sections.append(_Section(f"org:{name}:{i}", name, [("Roles", ", ".join(r) or None), *f[1:]]))

    # change notice
    ch = ef.first(root, f"{EF_EXT}/efac:Changes")
    if ch is not None:
        prev = ef.t(ch, "efbc:ChangedNoticeIdentifier")
        meta["changes"] = prev
        desc = ef.t(ch, "efac:ChangeReason/efbc:ReasonDescription")
        sections.append(_Section("change", "Change", [
            ("Changes notice", prev),
            ("Reason", ef.code(ch, "efac:ChangeReason/cbc:ReasonCode")),
            ("Explanation", desc if desc and desc.strip("-– ") else None),
            ("Described changes", ef.all(ch, "efac:Change/efbc:ChangeDescription")),
            ("Changed sections", ", ".join(ef.all(ch, "efac:Change/efac:ChangedSection/efbc:ChangedSectionIdentifier"))
             or None),
        ]))
    meta.update(buyers=[b for b in buyer_names if b], lots=lot_data, winners=winners, ted=ted, form=form)
    return meta, sections


def _flat(sections: list[_Section]) -> dict[tuple[str, str], tuple[str, str]]:
    out = {}
    for s in sections:
        if s.key.split(":")[0] in _DIFF_SKIP:
            continue
        for label, value in s.fields:
            out[(s.key, label)] = (f"{s.heading} › {label}",
                                   "; ".join(value) if isinstance(value, list) else str(value))
    return out


def _short(s: str, n: int = 160) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def eforms_diff(old: list[_Section], new: list[_Section]) -> list[str]:
    """Field-level differences between two versions of a notice, as readable lines."""
    a, b = _flat(old), _flat(new)
    out = []
    for k in list(dict.fromkeys([*a, *b])):
        if k in a and k in b:
            if a[k][1] != b[k][1]:
                out.append(f"Changed — {b[k][0]}: {_short(a[k][1])} → {_short(b[k][1])}")
        elif k in b:
            out.append(f"Added — {b[k][0]}: {_short(b[k][1])}")
        else:
            out.append(f"Removed — {a[k][0]}: {_short(a[k][1])}")
    return out


def notice_id_version(identifier: str) -> tuple[str, str | None]:
    """'3c8f8b3f-…-edfc-01' -> ('3c8f8b3f-…-edfc', '01'); identifiers without a version suffix stay whole."""
    m = re.fullmatch(r"(.+)-(\d{1,2})", identifier.strip())
    if m and (re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", m.group(1)) or m.group(1).isdigit()):
        return m.group(1), m.group(2)
    return identifier.strip(), None


def _ov_url(notice_id: str, version: str | None = None) -> str:
    url = f"https://oeffentlichevergabe.de/ui/de/search/details?noticeId={notice_id}"
    return f"{url}&noticeVersion={version}" if version else url


def eforms_to_markdown(xml: bytes | Any, *, history: list[Any] = ()) -> tuple[str, str]:
    """Readable markdown of one eForms notice. `history` holds the previous notices it changes (newest first,
    XML bytes or parsed roots); their differences are listed in a "Change history" section. Returns (title, md)."""
    root = etree.fromstring(xml, _EF_PARSER) if isinstance(xml, bytes) else xml
    meta, sections = _eforms_sections(root)
    lots = meta["lots"]
    out: list[str] = [f"# {meta['title'] or meta['id'] or 'Notice'}"]
    out.append("_" + " · ".join(x for x in (meta["type"], f"notice {meta['id']}" if meta["id"] else "",
                                             f"version {meta['version']}" if meta["version"] else "",
                                             f"issued {meta['issued']}" if meta["issued"] else "") if x) + "_")

    # summary
    by_key = {s.key: s for s in sections}
    proc = dict(by_key["procedure"].fields)
    deadlines = list(dict.fromkeys(f["deadline"] for *_, f in lots if f["deadline"]))
    periods = list(dict.fromkeys(f["period"] for *_, f in lots if f["period"]))
    criteria = list(dict.fromkeys(tuple(f["criteria"]) for *_, f in lots if f["criteria"]))
    docs = list(dict.fromkeys(d for *_, f in lots for d in f["docs"]))
    submit = list(dict.fromkeys(d for *_, f in lots for d in f["submit"]))
    nature = proc.get("Main nature of the contract")
    procedure = " · ".join(x for x in (proc.get("Type of procedure"), "accelerated" if proc.get("Accelerated procedure")
                                       else None, nature) if x)
    summary = [
        ("Buyer", "; ".join(meta["buyers"]) or None),
        ("Procedure", procedure or None),
        ("Submission deadline", deadlines[0] if len(deadlines) == 1 else
         (f"{min(deadlines)} (earliest; differs by lot)" if deadlines else None)),
        ("Lots", proc.get("Lots")),
        ("Contract period", periods[0] if len(periods) == 1 else None),
        ("Estimated value", proc.get("Estimated value")),
        ("CPV", proc.get("Main classification (CPV)")),
        ("Award criteria", "; ".join(criteria[0]).replace(" — ", " – ") if len(criteria) == 1 else None),
        ("Procurement documents", ", ".join(_linkify(d) for d in docs) or None),
        ("Submit tenders at", ", ".join(_linkify(d) for d in submit) or None),
        ("Winner", "; ".join(meta["winners"]) or None),
    ]
    if prev := meta.get("changes"):
        pid, pver = notice_id_version(prev)
        reason = dict(by_key["change"].fields).get("Reason")
        summary.append(("Change notice", f"changes notice [{prev}]({_ov_url(pid, pver)})"
                        + (f" — {reason}" if reason else "")))
    out.append("## Summary")
    out.append("\n".join(f"- **{k}**: {v}" for k, v in summary if v))

    # lot overview
    if len(lots) > 1:
        rows = ["| Lot | Title | Quantity | CPV |", "|---|---|---|---|"]
        for lid, _, f in lots:
            qty = (f["qty"] or "").replace(" (from the lot title)", "")
            rows.append("| " + " | ".join(str(x or "").replace("|", "\\|") for x in (lid, f["short"], qty, f["cpv"])) + " |")
        out.append("## Lots\n\n" + "\n".join(rows))

    # change history: what differs from the previous version(s)
    if history:
        out.append("## Change history")
        newer_meta, newer = meta, sections
        for old in history:
            old_root = etree.fromstring(old, _EF_PARSER) if isinstance(old, bytes) else old
            old_meta, old_sections = _eforms_sections(old_root)
            diff = eforms_diff(old_sections, newer)
            head = (f"Notice {newer_meta['id']} (issued {newer_meta['issued']}) compared with "
                    f"[{old_meta['id']}]({_ov_url(old_meta['id'], old_meta['version'])}) (issued {old_meta['issued']}):")
            out.append(head + "\n" + ("\n".join(f"- {d}" for d in diff) if diff else
                                      "- No notice field differs; check the procurement documents for what changed."))
            newer_meta, newer = old_meta, old_sections

    # sections; with several lots, fields shared by every lot are shown once
    common: list[tuple[str, Any]] = []
    lot_keys = [f"lot:{lid}" for lid, *_ in lots]
    if len(lots) > 1:
        first = by_key[lot_keys[0]].fields
        for label, value in first:
            if label in ("Title", "Description", "Quantity"):
                continue
            if all((label, value) in by_key[k].fields for k in lot_keys[1:]):
                common.append((label, value))
    tail = [s for s in sections if s.key in ("notice", "change")]  # bookkeeping goes last
    for s in [s for s in sections if s not in tail] + tail:
        if s.key == "procedure":
            out.append(f"## {s.heading}")
            out += _fields_md(s.fields)
            if common:
                out.append("## Terms common to all lots")
                out += _fields_md(common)
            continue
        fields = [f for f in s.fields if f not in common] if s.key in lot_keys else s.fields
        if not fields and not s.blocks:
            continue
        level = "###" if s.key.startswith(("lot:", "result:", "org:")) else "##"
        if s.key.startswith("org:") and not out[-1].startswith("## Organisations") and \
                not any(x.startswith("## Organisations") for x in out):
            out.append("## Organisations")
        out.append(f"{level} {s.heading}")
        out += _fields_md(fields) + s.blocks
    title = meta["title"] or meta["id"] or "Notice"
    return f"{title} – Bekanntmachungsservice"[:300], "\n\n".join(out)


async def _ov_fetch(client: httpx.AsyncClient, notice_id: str, version: str | None) -> bytes | None:
    for params in ([{"format": "eforms", "noticeVersion": version}] if version else []) + [{"format": "eforms"}]:
        r = await client.get(f"{OV_NOTICE_API}/{notice_id}", params=params)
        if r.status_code == 200 and r.content.lstrip()[:1] == b"<":
            return r.content
    return None


async def oeffentlichevergabe_notice(client: httpx.AsyncClient, url: str) -> tuple[list[tuple[str, str, str]], list[Found]]:
    """Bekanntmachungsservice: the notice page is a JavaScript app; render the notice from the open API's eForms XML
    instead, with the change history of earlier versions. Documents are captured from the platform it links to."""
    q = parse_qs(urlparse(url).query)
    notice_id = (q.get("noticeId") or [None])[0]
    if not notice_id:
        return [], []
    xml = await _ov_fetch(client, notice_id, (q.get("noticeVersion") or [None])[0])
    if xml is None:
        return [(url, "Bekanntmachungsservice", "_The notice could not be fetched from the Bekanntmachungsservice "
                 "API._")], []
    root = etree.fromstring(xml, _EF_PARSER)
    history, current, seen = [], root, {notice_id}
    for _ in range(MAX_HISTORY):
        prev = _Eforms(current).t(current, f"{EF_EXT}/efac:Changes/efbc:ChangedNoticeIdentifier")
        if not prev:
            break
        pid, pver = notice_id_version(prev)
        if pid in seen:
            break
        seen.add(pid)
        try:
            prev_xml = await _ov_fetch(client, pid, pver)
        except httpx.HTTPError:
            prev_xml = None
        if prev_xml is None:
            break
        current = etree.fromstring(prev_xml, _EF_PARSER)
        history.append(current)
    title, md = eforms_to_markdown(root, history=history)
    return [(url, title, md)], []


HANDLERS = {"www.evergabe-online.de": evergabe_online, "evergabe-online.de": evergabe_online,
            "oeffentlichevergabe.de": oeffentlichevergabe_notice, "www.oeffentlichevergabe.de": oeffentlichevergabe_notice}


def _handler_key(url: str) -> tuple[str, str]:
    """One handler call per tender/notice on a host (details and documents pages of e-Vergabe share their id)."""
    p = urlparse(url)
    q = parse_qs(p.query)
    return p.hostname or "", (q.get("noticeId") or q.get("id") or [p.path])[0]


# ---------------------------------------------------------------- orchestration

@dataclass
class EnrichResult:
    pages: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w.\- ()]+", "_", name).strip(" .") or "file"
    return name[:150]


async def enrich_cluster(db, notice_id: int, files_dir: Path, client: httpx.AsyncClient | None = None) -> EnrichResult:
    """Capture pages and attachments for a notice and every copy of the same tender. Writes to the store's db."""
    rows = db.execute(
        """SELECT id, url, platform_url FROM notices
           WHERE cluster_id = (SELECT COALESCE(cluster_id, id) FROM notices WHERE id = ?) OR id = ?""",
        (notice_id, notice_id)).fetchall()
    doc_rows = db.execute(
        f"SELECT url, title FROM documents WHERE notice_id IN ({','.join('?' * len(rows))})",
        [r["id"] for r in rows]).fetchall()

    page_urls: list[str] = []
    for r in rows:
        for u in (r["url"], r["platform_url"]):
            if u and u not in page_urls:
                page_urls.append(_ted_html(u) or u)
    own = client is None
    client = client or httpx.AsyncClient(follow_redirects=True, timeout=90,
                                         headers={"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "de,pl,en;q=0.5"})
    result = EnrichResult()
    found: list[Found] = []
    try:
        handled_hosts, handled_keys = set(), set()
        for u in list(page_urls) + [d["url"] for d in doc_rows]:
            host = urlparse(u).hostname or ""
            if host not in HANDLERS or _handler_key(u) in handled_keys:
                continue
            handled_hosts.add(host)
            handled_keys.add(_handler_key(u))
            try:
                pages, docs = await HANDLERS[host](client, u)
            except (httpx.HTTPError, etree.LxmlError) as e:
                log.warning("handler for %s failed: %s", u, e)
                pages, docs = [(u, "", f"_Could not fetch: {type(e).__name__}: {e}_")], []
            result.pages += [{"url": pu, "title": t, "markdown": md} for pu, t, md in pages]
            found += docs
        for u in page_urls:
            if (urlparse(u).hostname or "") in handled_hosts:
                continue
            try:
                r = await client.get(u)
                ctype = r.headers.get("content-type", "")
                if "html" not in ctype:
                    continue
                if re.search(r"ted\.europa\.eu/\w+/notice/\d+-\d{4}/html", str(r.url)):
                    title, md = _ted_page(r.text, str(r.url))
                else:
                    title, md = html_to_markdown(r.text, str(r.url))
                if len(md) < 200:
                    md = (md + "\n\n" if md else "") + "_This page is rendered by JavaScript in the browser; " \
                         "its content is not in the HTML._"
                result.pages.append({"url": u, "title": title, "markdown": md, "status": r.status_code})
                # documents linked from a generic page
                for a in LexborHTMLParser(r.text).css("a[href]"):
                    href = urljoin(str(r.url), a.attributes["href"])
                    if DOC_EXT.search(urlparse(href).path):
                        found.append(Found(url=href, filename=_safe_name(unquote(urlparse(href).path.rsplit("/", 1)[-1]))))
            except httpx.HTTPError as e:
                result.pages.append({"url": u, "title": "", "markdown": f"_Could not fetch: {e}_"})
        for d in doc_rows:
            host = urlparse(d["url"]).hostname or ""
            if host not in handled_hosts and DOC_EXT.search(urlparse(d["url"]).path):
                found.append(Found(url=d["url"], filename=_safe_name(d["title"] or d["url"].rsplit("/", 1)[-1])))
        # direct downloads for the generic finds
        seen_urls = set()
        for f in found:
            if f.data is not None or f.error or f.url in seen_urls:
                continue
            seen_urls.add(f.url)
            try:
                d = await client.get(f.url)
                if d.status_code == 200 and len(d.content) <= MAX_FILE_BYTES:
                    f.data, f.content_type = d.content, d.headers.get("content-type")
                else:
                    f.error = f"HTTP {d.status_code}" if d.status_code != 200 else "too large"
            except httpx.HTTPError as e:
                f.error = f"{type(e).__name__}: {e}"
    finally:
        if own:
            await client.aclose()

    now = datetime.now(UTC).isoformat(timespec="seconds")
    target = files_dir / str(notice_id)
    target.mkdir(parents=True, exist_ok=True)
    db.execute("BEGIN")
    try:
        db.execute("DELETE FROM notice_pages WHERE notice_id=?", (notice_id,))
        db.execute("DELETE FROM attachments WHERE notice_id=?", (notice_id,))
        for p in result.pages:
            db.execute("INSERT OR REPLACE INTO notice_pages (notice_id, url, title, markdown, fetched_at) VALUES (?,?,?,?,?)",
                       (notice_id, p["url"], p["title"], p["markdown"], now))
        seen = set()
        for f in found:
            key = (f.folder, f.filename, f.url)
            if key in seen:
                continue
            seen.add(key)
            path = text = sha = None
            pages = None
            if f.data is not None:
                sha = hashlib.sha256(f.data).hexdigest()
                sub = target / _safe_name(f.folder) if f.folder else target
                sub.mkdir(parents=True, exist_ok=True)
                fp = sub / _safe_name(f.filename)
                fp.write_bytes(f.data)
                path = str(fp)
                text, pages = extract_text(f.filename, f.data)
                text = text[:MAX_TEXT_CHARS]
            db.execute(
                """INSERT INTO attachments (notice_id, url, filename, folder, listed_size, listed_date, size,
                   content_type, sha256, path, text, pages, error, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (notice_id, f.url, f.filename, f.folder, f.listed_size, f.listed_date,
                 len(f.data) if f.data is not None else None, f.content_type, sha, path, text, pages, f.error, now))
            result.attachments.append({"filename": f.filename, "folder": f.folder, "size": len(f.data or b""),
                                       "chars": len(text or ""), "error": f.error})
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return result


def enrich(store, notice_id: int) -> EnrichResult:
    files_dir = Path(store.path).resolve().parent / "attachments"
    return asyncio.run(enrich_cluster(store.db, notice_id, files_dir))


__all__ = ["enrich", "enrich_cluster", "html_to_markdown", "extract_text", "eforms_to_markdown", "eforms_diff", "HANDLERS"]
