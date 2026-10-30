"""enrich: page -> markdown, attachment text extraction, eForms notice rendering and the oeffentlichevergabe handler."""

from __future__ import annotations

import io
import zipfile

import httpx
import respx

from tenderhub.enrich import (
    _ted_page,
    eforms_diff,
    eforms_to_markdown,
    enrich_cluster,
    extract_text,
    html_to_markdown,
    notice_id_version,
    oeffentlichevergabe_notice,
)

from .conftest import fixture_path

NOTICE = "d53c984e-fb97-4555-8e8d-170b3ea2578d"
PREV = "3c8f8b3f-2fce-4b85-9077-b4537fd8edfc"
API = "https://oeffentlichevergabe.de/api/notices"
UI = f"https://oeffentlichevergabe.de/ui/de/search/details?noticeId={NOTICE}&noticeVersion=01"


def _notice_xml() -> bytes:
    return fixture_path("oeffentlichevergabe", f"notice_{NOTICE}.xml").read_bytes()


def _previous_xml() -> bytes:
    """The fixture as its own predecessor: other id, no change block, earlier deadline for every lot."""
    xml = _notice_xml().decode()
    start, end = xml.index("<efac:Changes>"), xml.index("</efac:Changes>") + len("</efac:Changes>")
    xml = xml[:start] + xml[end:]
    xml = xml.replace(f">{NOTICE}<", f">{PREV}<").replace("<cbc:EndDate>2026-11-20+01:00", "<cbc:EndDate>2026-11-13+01:00")
    return xml.encode()


# ---------------------------------------------------------------- html_to_markdown

PAGE = """<html><head><title>Ausschreibung 42</title><script>var x = 1;</script></head><body>
<nav><a href="/home">Startseite</a></nav>
<header class="site-header">Portal</header>
<main>
  <h1>Bekanntmachung</h1>
  <div><span class="label">1.1</span> <span>Offizielle Bezeichnung</span>: <span class="data">Stadt Musterstadt</span></div>
  <div>1.1.</div><div>Buyer</div>
  <div>2.1.1 Zweck</div>
  <p>Unterlagen: <a href="docs/LV.pdf">Leistungsverzeichnis</a> und <a href="javascript:void(0)">Drucken</a></p>
  <table><tr><th>Datei</th><th>Größe</th></tr><tr><td>a.pdf</td><td>1 MB</td></tr><tr><td>b|c.pdf</td><td>2 MB</td></tr></table>
  <table><tr><td>Frist</td><td>20.11.2026</td></tr><tr><td>Ort</td><td>Bonn</td></tr></table>
  <ul><li>erster Punkt</li><li>zweiter <b>wichtiger</b> Punkt</li></ul>
  <div hidden>versteckt</div>
  <div class="cookie-banner">Cookies akzeptieren</div>
</main></body></html>"""


def test_html_to_markdown_groups_inline_runs_into_one_line():
    title, md = html_to_markdown(PAGE, "https://portal.example/tender/42/")
    assert title == "Ausschreibung 42"
    assert "1.1 Offizielle Bezeichnung: Stadt Musterstadt" in md.split("\n\n")


def test_html_to_markdown_numbered_sections_become_headings():
    _, md = html_to_markdown(PAGE, "https://portal.example/tender/42/")
    blocks = md.split("\n\n")
    assert "# Bekanntmachung" in blocks
    assert "#### 2.1.1 Zweck" in blocks         # depth follows the numbering
    assert "### 1.1. Buyer" in blocks          # a lone "1.1." is merged with the following block


def test_html_to_markdown_tables_and_lists():
    _, md = html_to_markdown(PAGE, "https://portal.example/tender/42/")
    assert "| Datei | Größe |\n|---|---|\n| a.pdf | 1 MB |\n| b\\|c.pdf | 2 MB |" in md
    assert "- **Frist**: 20.11.2026\n- **Ort**: Bonn" in md   # two-column table without headers: key/value list
    assert "- erster Punkt" in md and "- zweiter **wichtiger** Punkt" in md


def test_html_to_markdown_resolves_links_and_drops_chrome():
    _, md = html_to_markdown(PAGE, "https://portal.example/tender/42/")
    assert "[Leistungsverzeichnis](https://portal.example/tender/42/docs/LV.pdf)" in md
    assert "Drucken" in md and "javascript:" not in md
    for gone in ("Startseite", "Portal", "var x", "versteckt", "Cookies"):
        assert gone not in md


# ---------------------------------------------------------------- extract_text

def _pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def _zip(members: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_extract_text_pdf():
    text, pages = extract_text("Leistungsbeschreibung.pdf", _pdf("Lieferung von 2400 Kathetern"))
    assert pages == 1
    assert text.startswith("[page 1]") and "Lieferung von 2400 Kathetern" in text


def test_extract_text_docx_keeps_paragraphs():
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    doc = (f'<w:document xmlns:w="{w}"><w:body>'
           "<w:p><w:r><w:t>Angebots</w:t></w:r><w:r><w:t>schreiben</w:t></w:r></w:p>"
           "<w:p><w:r><w:t>Los 1: 2.400 Stück</w:t></w:r></w:p><w:p></w:p></w:body></w:document>")
    text, pages = extract_text("Angebot.docx", _zip({"word/document.xml": doc, "[Content_Types].xml": "<x/>"}))
    assert (text, pages) == ("Angebotsschreiben\nLos 1: 2.400 Stück", None)


def test_extract_text_xlsx_resolves_shared_strings():
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    shared = f'<sst xmlns="{ns}"><si><t>Artikel</t></si><si><t>Menge</t></si><si><r><t>Tu</t></r><r><t>bus</t></r></si></sst>'
    sheet = (f'<worksheet xmlns="{ns}"><sheetData>'
             '<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>'
             '<row><c t="s"><v>2</v></c><c><v>130</v></c><c t="inlineStr"><is><t>EA</t></is></c></row>'
             "</sheetData></worksheet>")
    data = _zip({"xl/workbook.xml": f'<workbook xmlns="{ns}"/>', "xl/sharedStrings.xml": shared,
                 "xl/worksheets/sheet1.xml": sheet})
    text, _ = extract_text("Preisblatt.xlsx", data)
    assert text == "## sheet1.xml\nArtikel\tMenge\nTubus\t130\tEA"


def test_extract_text_zip_extracts_members_and_skips_schemas():
    data = _zip({
        "Bekanntmachung.xml": "<Notice><Title>Katheter</Title><Lot><Name>LOS 1</Name></Lot></Notice>",
        "Hinweise.txt": "Bitte beachten".encode("cp1250"),
        "schema/notice.xsd": "<xs:schema/>",
        "Anlage.pdf": _pdf("Anlage eins"),
    })
    text, pages = extract_text("Unterlagen.zip", data)
    assert pages is None
    assert "===== Bekanntmachung.xml =====\nKatheter\nLOS 1" in text   # XML: text kept, markup dropped
    assert "===== Hinweise.txt =====\nBitte beachten" in text
    assert "===== Anlage.pdf =====\n[page 1]\nAnlage eins" in text
    assert "notice.xsd" not in text


def test_extract_text_never_raises():
    text, pages = extract_text("kaputt.pdf", b"%PDF-1.4 garbage")
    assert pages is None and text.startswith("[text extraction failed")
    assert extract_text("bild.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00") == ("", None)


# ---------------------------------------------------------------- eForms -> markdown

def test_eforms_notice_renders_summary_lots_and_terms():
    title, md = eforms_to_markdown(_notice_xml())
    assert title == "1/DLII6/VM191 - RV Tubus, divers und Katheter, divers – Bekanntmachungsservice"
    blocks = md.split("\n\n")
    assert blocks[0] == "# 1/DLII6/VM191 - RV Tubus, divers und Katheter, divers"
    summary = blocks[blocks.index("## Summary") + 1]
    for line in ("- **Buyer**: Bundesamt für Infrastruktur, Umweltschutz und Dienstleistungen der Bundeswehr (Bonn)",
                 "- **Procedure**: Open procedure · accelerated · Supplies",
                 "- **Submission deadline**: 2026-11-20 08:00 (UTC+01:00)",
                 "- **Contract period**: 2026-12-01 to 2028-11-30",
                 "- **Award criteria**: Qualitätskriterien (Quality): 40 % – gem. Bewertungsmatrix; "
                 "Preis (Price): 60 % – Preiskriterium"):
        assert line in summary.split("\n")
    assert "changes notice [3c8f8b3f-2fce-4b85-9077-b4537fd8edfc-01](https://oeffentlichevergabe.de/ui/de/search/" \
           "details?noticeId=3c8f8b3f-2fce-4b85-9077-b4537fd8edfc&noticeVersion=01)" in summary
    # lot overview with the quantity taken from the lot titles
    assert "| LOT-0002 | Epistaxis-Ballonkatheter, Doppelballon | 4.480 EA | 33000000 |" in md
    # what every lot shares is listed once; each lot keeps its own title, quantity and options
    common = md[md.index("## Terms common to all lots"):md.index("### Lot: LOT-0001")]
    assert "Deadline for receipt of tenders: 2026-11-20 08:00 (UTC+01:00)" in common
    assert "Tenders must remain valid for: 4 months" in common
    assert "Framework agreement: Framework agreement, partly without reopening" in common
    assert md.count("Deadline for receipt of tenders:") == 1
    lot1 = md[md.index("### Lot: LOT-0001"):md.index("### Lot: LOT-0002")]
    assert "Title: LOS 1 - 2.400 EA Endobronchialblocker-Set, 9Fr" in lot1
    assert "Quantity: 2.400 EA (from the lot title)" in lot1
    assert "Description: gemäß VM191_05_Anlage 2_LOS 1_Preis- und Produktblatt_Endobronchialblocker-Set, 9Fr" in lot1
    # documents and submission as links, review body once, phone number not shown as an identifier
    assert "[https://www.evergabe-online.de/tenderdocuments.html?id=900460]" in md
    assert md.count("### Bundeskartellamt - Vergabekammer des Bundes") == 1
    assert "Roles: Review body, Mediation body" in md
    assert "t:022855041533" not in md and "Registration number" not in md
    assert md.index("## Organisations") < md.index("## Notice") < md.index("## Change")


def test_eforms_below_threshold_notice_with_inline_buyer():
    xml = zipfile.ZipFile(fixture_path("oeffentlichevergabe", "notices_2026-10-28_EFORMS.zip")).read("25975366-1.xml")
    title, md = eforms_to_markdown(xml)
    assert title == "Organigramm-Tool für GSI und FAIR – Bekanntmachungsservice"
    assert "- **Buyer**: GSI Helmholtzzentrum für Schwerionenforschung GmbH" in md
    assert "- **Submission deadline**: 2026-11-11 12:00 (UTC+01:00)" in md
    assert "## Lots" not in md and "### Lot: LOT-0000" in md


def test_eforms_award_notice_lists_winner():
    xml = zipfile.ZipFile(fixture_path("oeffentlichevergabe", "notices_2026-10-29_EFORMS.zip")).read(
        "2493c652-6b8c-415a-813d-f8dd011a8811-01.xml")
    _, md = eforms_to_markdown(xml)
    assert "- **Winner**: Hutchinson GmbH (Mannheim)" in md
    result = md[md.index("### Result: LOT-0000"):]
    assert "Outcome: At least one winner was chosen" in result and "Tenders received: 1" in result


def test_eforms_diff_and_change_history():
    _, md = eforms_to_markdown(_notice_xml(), history=[_previous_xml()])
    history = md[md.index("## Change history"):]
    assert f"compared with [{PREV}](" in history
    assert ("- Changed — Lot: LOT-0001 › Deadline for receipt of tenders: 2026-11-13 08:00 (UTC+01:00) → "
            "2026-11-20 08:00 (UTC+01:00)") in history
    # identical versions: nothing to list
    _, md = eforms_to_markdown(_notice_xml(), history=[_notice_xml()])
    assert "No notice field differs" in md
    assert eforms_diff([], []) == []


def test_notice_id_version():
    assert notice_id_version(f"{PREV}-01") == (PREV, "01")
    assert notice_id_version("25975366-1") == ("25975366", "1")
    assert notice_id_version(PREV) == (PREV, None)


def test_ted_page_gets_the_notice_title():
    html = """<html><head><title>210/2026</title></head><body><div id="notice">
      <div class="header"><div class="header-content"><span class="bold">749685-2026 - Competition</span>
        <div class="bold">Germany – Medical equipments – RV Tubus und Katheter</div>
        <div class="bold">OJ S 210/2026 29/10/2026</div></div></div>
      <div class="h2">1. <span>Buyer</span></div>
      <div><span class="label" data-labels-key="field|name|BT-21-Procedure">Title</span><span>: </span>
        <span class="data">RV Tubus und Katheter</span></div>
      <div>Some more text so that the page is not considered empty by anyone reading it.</div>
    </div></body></html>"""
    title, md = _ted_page(html, "https://ted.europa.eu/en/notice/749685-2026/html")
    assert title == "RV Tubus und Katheter – TED 749685-2026"
    assert md.startswith("# RV Tubus und Katheter\n\n_749685-2026 - Competition · Germany – Medical equipments · "
                         "OJ S 210/2026 29/10/2026_")
    assert "Title: RV Tubus und Katheter" in md


# ---------------------------------------------------------------- oeffentlichevergabe handler

@respx.mock
async def test_oeffentlichevergabe_handler_fetches_api_and_follows_changes():
    cur = respx.get(f"{API}/{NOTICE}").mock(return_value=httpx.Response(200, content=_notice_xml()))
    prev = respx.get(f"{API}/{PREV}").mock(return_value=httpx.Response(200, content=_previous_xml()))
    async with httpx.AsyncClient() as client:
        pages, docs = await oeffentlichevergabe_notice(client, UI)
    assert docs == []
    assert cur.calls[0].request.url.params["noticeVersion"] == "01"
    assert prev.calls[0].request.url.params["noticeVersion"] == "01"
    [(url, title, md)] = pages
    assert url == UI and title.startswith("1/DLII6/VM191")
    assert "## Change history" in md and "2026-11-13 08:00 (UTC+01:00) → 2026-11-20" in md


@respx.mock
async def test_oeffentlichevergabe_handler_falls_back_to_latest_version_and_reports_failure():
    route = respx.get(f"{API}/{NOTICE}").mock(side_effect=lambda req: httpx.Response(
        404 if "noticeVersion" in req.url.params else 200, content=b"<x/>" if "noticeVersion" in req.url.params
        else _notice_xml()))
    respx.get(f"{API}/{PREV}").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        [(_, _, md)], _ = await oeffentlichevergabe_notice(client, UI)
        assert route.call_count == 2 and "## Change history" not in md and "## Summary" in md
        respx.get(f"{API}/gone").mock(return_value=httpx.Response(404))
        [(_, _, md)], _ = await oeffentlichevergabe_notice(
            client, "https://oeffentlichevergabe.de/ui/de/search/details?noticeId=gone")
    assert "could not be fetched" in md


@respx.mock
async def test_enrich_cluster_captures_the_notice_from_the_api(store, tmp_path):
    from lxml import etree

    from tenderhub.connectors.oeffentlichevergabe import Oeffentlichevergabe

    from .conftest import make_connector

    conn, c = make_connector(Oeffentlichevergabe)
    await c.aclose()
    n = conn.to_notice(etree.fromstring(_notice_xml()), published=None)
    n.platform_url, n.documents = None, []   # keep the test to the notice page itself
    store.upsert(n)
    nid = store.db.execute("SELECT id FROM notices WHERE source_id=?", (NOTICE,)).fetchone()["id"]
    respx.get(f"{API}/{NOTICE}").mock(return_value=httpx.Response(200, content=_notice_xml()))
    respx.get(f"{API}/{PREV}").mock(return_value=httpx.Response(404))

    async with httpx.AsyncClient() as client:
        res = await enrich_cluster(store.db, nid, tmp_path / "files", client)

    [page] = res.pages
    assert page["url"] == UI
    row = store.db.execute("SELECT title, markdown FROM notice_pages WHERE notice_id=?", (nid,)).fetchone()
    assert row["title"] == "1/DLII6/VM191 - RV Tubus, divers und Katheter, divers – Bekanntmachungsservice"
    assert "### Lot: LOT-0003" in row["markdown"] and "rendered by JavaScript" not in row["markdown"]
