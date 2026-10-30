"""Requirements section on the example tender VM191 (BAIUDBw framework agreement, tubes and catheters, 3 lots)."""

import re

import pytest

from tenderhub.brief import cite
from tenderhub.brief.requirements import CATEGORIES, _clean, extract


@pytest.fixture(scope="module")
def items(ctx):
    return extract(ctx)


def find(items, *needles, category=None):
    hits = [i for i in items if all(n in i["requirement"] for n in needles)
            and (category is None or i["category"] == category)]
    assert hits, f"no requirement with {needles}"
    assert len(hits) == 1, f"duplicate requirements for {needles}: {hits}"
    return hits[0]


def test_shape_order_and_no_duplicates(items):
    assert 30 <= len(items) <= 70
    cats = [i["category"] for i in items]
    assert set(cats) <= set(CATEGORIES)
    assert cats == sorted(cats, key=CATEGORIES.index), "items must be grouped in CATEGORIES order"
    assert {"eligibility", "product", "quality", "delivery", "catalogue", "invoicing", "legal"} <= set(cats)
    texts = [i["requirement"].lower() for i in items]
    assert len(texts) == len(set(texts))
    for i in items:
        assert set(i) == {"category", "requirement", "cite"}
        assert len(i["requirement"]) < 500  # the state list (26 names) is the longest item


def test_quotes_are_verbatim(ctx, items):
    """Every quote is in the cited file/page (after undoing PDF extraction artefacts and whitespace)."""
    norm = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    docs = {(d.filename, d.folder): d for d in ctx.docs}
    for i in items:
        c = i["cite"]
        assert c["quote"]
        doc = docs[(c["file"], c["folder"])]
        pages = {p: _clean(t) for p, t in doc.page_texts()}
        assert norm(c["quote"]) in norm(pages[c["page"]]), f"quote not on cited page: {c}"


def test_eligibility(items):
    assert find(items, "§§ 123, 124 GWB")["cite"]["file"].startswith("VM191_12_B-V034")
    rus = find(items, "Art. 5k VO (EU) 833/2014")
    assert "mehr als 10 % des Auftragswerts" in rus["requirement"]
    states = find(items, "BMI-Staatenliste", category="eligibility")
    assert "26 Staaten" in states["requirement"]
    for s in ("Afghanistan", "China (inkl. Hongkong, Macau)", "Russische Föderation", "Ukraine", "Vietnam"):
        assert s in states["requirement"]
    # "Korea" alone would read as South Korea; the list means the DVR Korea
    assert "Korea (Demokratische Volksrepublik Korea)" in states["requirement"]
    assert "Staatenliste" in states["cite"]["file"]
    assert "über 50% russische Beteiligung" in rus["requirement"]
    reg = find(items, "Handelsregister", category="eligibility")
    assert "nicht älter als 6 Monate" in reg["requirement"]
    assert "Verpflichtungserklärung" in find(items, "Eignungsleihe")["requirement"]


def test_product_per_lot(items):
    assert find(items, "Einmalgebrauch, steril", "Lose 1, 2, 3")["category"] == "product"
    los1 = find(items, "Los 1:", "pro Packung")
    assert "max. 1 Stück" in los1["requirement"] and "28 Monate" in los1["requirement"]
    assert "LOS 1" in los1["cite"]["file"] and los1["cite"]["quote"] == "1 Stück pro Packung 28 Monate"
    assert "48 Monate" in find(items, "Los 2:", "pro Packung")["requirement"]
    los3 = find(items, "Los 3:", "pro Packung")
    assert "max. 10 Stück" in los3["requirement"] and "keine Mindesthaltbarkeit" in los3["requirement"]
    prices = find(items, "zwei Nettopreise")
    assert "Vertragsjahr 1 und 2" in prices["requirement"] and "Vertragsjahr 3" in prices["requirement"]
    assert find(items, "Zustimmung des AG liefern")["cite"]["page"] == 3
    assert find(items, "Bevollmächtigter mit Sitz in der EU")["category"] == "product"


def test_product_and_quality(items):
    ce = find(items, "CE-gekennzeichnet")
    assert ce["cite"]["file"].startswith("VM191_03_Entwurf_RV") and ce["cite"]["page"] == 15
    assert not [i for i in items if "MDR (EU) 2017/745 ist dem Angebot" in i["requirement"]], "covered by CE item"
    assert find(items, "UDI")["cite"]["file"].startswith("VM191_06_RV_Anlage 3_TS-QMA")
    assert find(items, "TL A-0032 Teil 1")["cite"]["file"].startswith("VM191_07_RV_Anlage 4_ZVB")
    assert find(items, "Angebotsdatenblatt", "Risikoklasse (MDR)", "EAN/UPC")
    assert find(items, "DIN EN ISO 13485 oder DIN ISO 9001")["category"] == "quality"
    assert find(items, "EN ISO 13485 bereits mit dem Angebot")
    assert find(items, "DIN EN ISO 14971", "DIN EN ISO 10993")
    lv = find(items, "Qualitätssicherung nach TS-QMA")
    assert "keine amtliche Qualitätssicherung (Güteprüfung)" in lv["requirement"]
    assert lv["cite"]["folder"] == "Vergabeunterlagen/Version 3", "newest Leistungsverzeichnis"
    assert "AMMPSicherheit@Bundeswehr.org" in find(items, "Schwerwiegende Vorkommnisse")["requirement"]
    assert find(items, "21 CFR 820")["category"] == "quality"
    assert "Herstellervorgaben" in find(items, "Händler prüfen je Produkt")["requirement"]


def test_delivery(items):
    assert find(items, "5 bis 14 Werktagen", category="delivery")["cite"]["page"] == 7
    window = find(items, "„frei Haus“")
    assert "Montag bis Donnerstag 7:00–15:00 Uhr" in window["requirement"]
    assert "Freitag 7:00–11:00 Uhr" in window["requirement"]
    places = find(items, "Lieferorte:")
    assert "Sanitätsmateriallager Krugau (15913 Märkische Heide)" in places["requirement"]
    assert "Sanitätsmateriallager Epe (48599 Gronau)" in places["requirement"]
    assert "DPU Incoterms® 2020" in find(items, "Lieferklausel")["requirement"]
    part = find(items, "Teillieferungen")
    assert part["cite"]["file"] == "VM191_2. Hinweisblatt.pdf", "newest Hinweisblatt"
    assert "1. Abruf im 1. Quartal 2027, 2. Abruf im Januar 2028" in part["requirement"]
    note = find(items, "Lieferschein mit", category="delivery")
    assert "Auftragsnummer, Artikelname, Menge" in note["requirement"]
    announce = find(items, "Geplante Auslieferungen")
    assert "LogZBw" in announce["requirement"] and "@bundeswehr.org" in announce["requirement"]
    assert find(items, "Spätestens 10 Tage vor dem Liefertermin")
    assert find(items, "50 % und 75 % der Höchstmenge")


def test_catalogue_and_invoicing(items):
    assert find(items, "BMEcat 1.2")["category"] == "catalogue"
    assert find(items, "eCl@ss 10.1")
    limits = find(items, "Kurzbezeichnung max. 80 Zeichen")
    assert "4.000 Zeichen" in limits["requirement"] and "600 x 600" in limits["requirement"]
    assert find(items, "gelten als nicht gestellt")["category"] == "invoicing"
    assert find(items, "Leitweg-ID", "SAP-Kontraktnummer")["cite"]["file"].startswith("VM191_07_RV_Anlage 4_ZVB")
    assert find(items, "XRechnung (EN 16931)", "OZG-RE")
    assert find(items, "30 Kalendertagen", "kein Skonto")


def test_legal(items):
    ins = find(items, "Betriebshaftpflichtversicherung")
    assert "5 Millionen €" in ins["requirement"] and "10 Millionen €" in ins["requirement"]
    assert find(items, "0,2 %", "max. 5 %")
    assert find(items, "15 % (Hardcore-Kartell)", "3 %")
    assert find(items, "24 Monate ab Lieferung")
    assert find(items, "sechs Monaten zum Monatsende")
    assert find(items, "Keine Abnahmepflicht")
    assert find(items, "Gerichtsstand Bonn")
    assert find(items, "UN-Kaufrecht ausgeschlossen")["cite"]["page"] == 12
    term = find(items, "Laufzeit bis zur Ausschöpfung")
    assert "spätestens 2 Jahre" in term["requirement"] and "einmal um 1 Jahr" in term["requirement"]
    assert find(items, "drei Wochen vor dem Liefertermin")["category"] == "legal"
    assert "Engpässen bei Lieferanten" in find(items, "Keine höhere Gewalt")["requirement"]
    assert "ersten vollendeten Woche" in find(items, "0,2 %")["requirement"]
    prices = find(items, "Zollgebühren")
    assert "Festpreis" not in prices["requirement"], "PPB has separate prices for Vertragsjahr 1-2 and 3"


def test_flags_arzneimittel_template(items):
    flag = find(items, "für Arzneimittel formuliert")
    assert flag["category"] == "other" and "Bieterfrage" in flag["requirement"]
    date = find(items, "geforderten Liefertermin 01.12.2026")
    assert date["category"] == "other" and "1. Quartal 2027" in date["requirement"]
    assert date["cite"]["folder"] == "Vergabeunterlagen/Version 3"
    assert find(items, "Aufforderung zur Angebotsabgabe vor")["category"] == "other"


def test_state_names_keep_ambiguous_long_form():
    from tenderhub.brief.requirements import _states
    text = ("Staatenliste\n1. Kongo (Demokratische Republik Kongo)\n2. China (Volksrepublik China),\n"
            "ab 01.07.1997 einschließlich Sonderverwaltungsregion (SVR) Hongkong\n3. Georgien\n")
    assert _states(text) == ["Kongo (Demokratische Republik Kongo)", "China (inkl. Hongkong)", "Georgien"]


def test_generic_no_tender_values_in_code():
    from pathlib import Path

    import tenderhub.brief.requirements as mod
    src = Path(mod.__file__).read_text()
    for v in ("VM191", "2400", "2.400", "Krugau", "Gronau", "Bonn\"", "28 Monate", "AMMPSicherheit",
              "LogZBw", "Quartal 2027", "01.12.2026", "21 CFR 820", "Märkische"):
        assert v not in src, f"tender-specific value {v!r} hard-coded"


def test_empty_context_yields_nothing():
    from tenderhub.brief import TenderContext
    assert extract(TenderContext({})) == []


def test_cite_helper_contract():
    assert cite("https://x", None, "a  b")["quote"] == "a b"
