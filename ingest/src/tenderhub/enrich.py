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


HANDLERS = {"www.evergabe-online.de": evergabe_online, "evergabe-online.de": evergabe_online}


def _ted_html(url: str) -> str | None:
    """TED's detail page is a JavaScript app; its server-rendered notice lives at /<lang>/notice/<pn>/html."""
    if m := re.search(r"ted\.europa\.eu/\w+/notice/-/detail/(\d+-\d{4})", url):
        return f"https://ted.europa.eu/en/notice/{m.group(1)}/html"
    return None


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
        handled_hosts = set()
        for u in list(page_urls) + [d["url"] for d in doc_rows]:
            host = urlparse(u).hostname or ""
            if host in HANDLERS and host not in handled_hosts:
                handled_hosts.add(host)
                pages, docs = await HANDLERS[host](client, u)
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


__all__ = ["enrich", "enrich_cluster", "html_to_markdown", "extract_text", "HANDLERS"]
