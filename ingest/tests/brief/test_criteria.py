"""Award criteria of VM191: Preis 60 % / Leistung 40 %, three Leistungskriterien (20/10/10 points), Preisquotientenmethode."""

from dataclasses import replace

from tenderhub.brief import TenderContext
from tenderhub.brief.criteria import extract


def _by_name(items):
    return {it["name"]: it for it in items}


def test_main_criteria_and_weights(ctx):
    items = extract(ctx)
    assert [(it["name"], it["weight"], it["weight_unit"]) for it in items[:2]] == [("Preis", 60, "%"), ("Leistung", 40, "%")]
    preis, leistung = items[0], items[1]
    assert preis["cite"]["file"] == "VM191_01_Aufforderung zur Angebotsabgabe_Version 1.pdf"
    assert preis["cite"]["page"] == 8 and preis["cite"]["quote"] == "Preis 60 %"
    assert "Brutto" in preis["description"] and "Skonto" in preis["description"]
    assert "max. 40 Leistungspunkte" in leistung["description"]
    assert all(it["lot"] is None for it in items if not it["name"].startswith("Bezugswert"))  # one matrix for all 3 lots


def test_leistungskriterien_from_bewertungsmatrix(ctx):
    subs = [it for it in extract(ctx) if it["weight_unit"] == "points"]
    assert [it["weight"] for it in subs] == [20, 10, 10]
    expiry, qm, pack = subs
    assert expiry["name"].startswith("Das Verfalldatum übertrifft bei Auslieferung")
    assert "12 P: um 7 - 12 Monate übertroffen" in expiry["description"]
    assert "20 P: um mehr als 12 Monate übertroffen" in expiry["description"]
    assert "≙ 20 % der Gesamtwertung" in expiry["description"]
    assert qm["name"] == "Nachweislich eingerichtetes und zertifiziertes Qualitätsmanagementsystem"
    assert "§ 21 Abs. 3 VerpackG" in pack["name"]
    for it in subs:
        assert it["cite"]["file"] == "VM191_18_Bewertungsmatrix.pdf" and it["cite"]["page"] == 1
        assert "Nachweis: Eigenerklärung des Bieters" in it["description"]


def test_formula_tie_break_and_no_late_documents(ctx):
    items = _by_name(extract(ctx))
    formula = items["Wertungsformel (Preisquotientenmethode)"]
    assert "S = w * L + (1-w) * Pmin/P * Lmax" in formula["description"]
    assert "w = 0,4, Lmax = 40" in formula["description"]           # fixed values, not the worked examples (0,3 / 0,2)
    assert "max. 16 Punkte aus Leistung + 24 aus Preis" in formula["description"]
    assert "L = Leistungspunktzahl, Pmin = Niedrigster Angebotspreis, P = Angebotspreis" in formula["description"]
    tie = items["Gleichstand (Tie-Break)"]
    assert tie["description"] == "Bei gleicher Gesamtpunktzahl (je Los) entscheidet: 1. Preis, 2. Leistung, danach Losentscheid"
    late = items["Keine Nachforderung wertungsrelevanter Unterlagen"]
    assert "§ 56 Absatz 3 VgV" in late["description"]
    assert late["cite"]["file"] == "https://www.evergabe-online.de/tenderdetails.html?id=900460"


def test_evaluation_basis_and_proof_with_offer(ctx):
    items = _by_name(extract(ctx))
    basis = items["Wertungsgrundlage"]
    assert "Preis- und Produktblatt" in basis["description"] and "Eigenerklärungen" in basis["description"]
    assert basis["cite"]["page"] == 4
    proof = items["Eigenerklärungen zu den Leistungskriterien mit dem Angebot"]
    assert "(ggf. pro Los)" in proof["description"]
    assert proof["cite"]["quote"] == "Die als Nachweis dienenden Eigenerklärungen sind (ggf. pro Los) mit dem Angebot einzureichen."


def test_shelf_life_baseline_per_lot(ctx):
    """The Verfalldatum criterion counts months above the price sheet's Mindesthaltbarkeit; lot 3 leaves it blank."""
    base = {it["lot"]: it for it in extract(ctx) if it["name"] == "Bezugswert Verfalldatum-Kriterium"}
    assert sorted(base) == ["1", "2", "3"]
    assert base["1"]["description"].startswith("Mindesthaltbarkeit 28 Monate")
    assert "4 P bei 29-34 Monaten, 12 P bei 35-40 Monaten, 20 P ab mehr als 40 Monaten" in base["1"]["description"]
    assert base["2"]["description"].startswith("Mindesthaltbarkeit 48 Monate")
    assert "55-60 Monaten" in base["2"]["description"]
    assert "keine Mindesthaltbarkeit eingetragen" in base["3"]["description"]
    assert "LOS 1" in base["1"]["cite"]["file"] and base["1"]["cite"]["quote"] == "28 Monate"


def test_lot_from_filename():
    from tenderhub.brief.criteria import _LOT_IN_NAME
    for name, lot in (("VM191_05_Anlage 2_LOS 3_Preis.pdf", "3"), ("Bewertungsmatrix Los-2.pdf", "2"), ("Los_12.pdf", "12")):
        assert _LOT_IN_NAME.search(name).group(1) == lot
    assert _LOT_IN_NAME.search("Losentscheid.pdf") is None and _LOT_IN_NAME.search("Kostenlos 3.pdf") is None


def test_quotes_are_verbatim(ctx):
    texts = {d.filename: " ".join(d.text.split()) for d in ctx.docs} | {p["url"]: " ".join(p["markdown"].split()) for p in ctx.pages}
    for it in extract(ctx):
        assert it["cite"]["quote"] in texts[it["cite"]["file"]], it


def test_falls_back_to_notice_page_weights(ctx):
    """Without the Aufforderung, weights come from the notice page (section 5.1.10)."""
    bare = TenderContext(ctx.notice, ctx.pages, [d for d in ctx.docs if "Aufforderung" not in d.filename])
    items = extract(bare)
    assert [(it["name"], it["weight"], it["weight_unit"]) for it in items[:2]] == [("Preis", 60, "%"), ("Qualitätskriterien", 40, "%")]
    assert items[0]["cite"]["file"] == "https://www.evergabe-online.de/tenderdetails.html?id=900460"
    assert not any(it["name"].startswith("Gleichstand") for it in items)


def test_empty_without_sources(ctx):
    assert extract(replace(ctx, pages=[], docs=[])) == []
