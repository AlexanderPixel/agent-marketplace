"""Checklist of documents to hand in, extracted from the VM191 tender (BAIUDBw, tubes and catheters, 3 lots)."""

import pytest

from tenderhub.brief.checklist import extract

OBLIGATIONS = {"mandatory", "if_applicable", "on_request"}
WHENS = {"with_offer", "on_request", "after_award", None}


@pytest.fixture(scope="module")
def items(ctx):
    return extract(ctx)


def _find(items, text, lot=None):
    hits = [i for i in items if text in i["document"] and (lot is None or i["lot"] == lot)]
    assert len(hits) == 1, f"{text!r} (lot {lot}): {[i['document'] for i in items]}"
    return hits[0]


def test_item_shape_and_order(items):
    assert 25 <= len(items) <= 35
    for i in items:
        assert set(i) == {"document", "obligation", "when", "template", "lot", "cite"}
        assert i["obligation"] in OBLIGATIONS and i["when"] in WHENS
    # with the offer first, mandatory before optional; after-award last
    assert items[0]["document"] == "Formular Angebot"
    assert items[-1]["when"] == "after_award"
    for i in items:     # obligation and timing are consistent
        assert (i["obligation"] == "on_request") == (i["when"] == "on_request"), i
    ranks = [{"with_offer": 0, None: 1, "on_request": 2, "after_award": 3}[i["when"]] for i in items]
    assert ranks == sorted(ranks)
    keys = [(i["document"], i["lot"]) for i in items]
    assert len(keys) == len(set(keys))


def test_quotes_are_verbatim(ctx, items):
    files = {d.filename: d for d in ctx.docs}
    for i in items:
        c = i["cite"]
        doc = files[c["file"]]
        page_text = next(t for p, t in doc.page_texts() if p == c["page"])
        assert c["quote"] in " ".join(page_text.split()), i


def test_offer_form_and_contract_annexes(items):
    offer = _find(items, "Formular Angebot")
    assert offer["template"] == "VM191_02_Angebot.docx"
    assert offer["obligation"] == "mandatory" and offer["when"] == "with_offer"
    assert offer["cite"]["file"] == "VM191_01_Aufforderung zur Angebotsabgabe_Version 1.pdf" and offer["cite"]["page"] == 3
    assert _find(items, "Entwurf Individualvertrag")["template"] == "VM191_03_Entwurf_RV Tubus u. Katheter_ divers.pdf"
    assert _find(items, "Leistungsbeschreibung")["template"] == "VM191_04_RV_Anlage 1_Leistungsbeschreibung_.pdf"


def test_price_sheet_per_lot(items):
    sheets = [i for i in items if i["document"].startswith("Preis- und Produktblatt")]
    assert [s["lot"] for s in sheets] == ["1", "2", "3"]
    assert sheets[0]["document"] == "Preis- und Produktblatt Los 1 – Anlage 2 zum Vertrag"
    assert "Endobronchialblocker" in sheets[0]["template"]
    assert "Epistaxis" in sheets[1]["template"]
    assert "Tubus_ Endotracheal" in sheets[2]["template"]
    assert all(s["obligation"] == "mandatory" and s["when"] == "with_offer" for s in sheets)


def test_self_declarations(items):
    b034 = _find(items, "(B-V 034)")
    assert b034["obligation"] == "mandatory"
    assert b034["template"] == "VM191_12_B-V034_Eigenerklaerung Nichtvorliegen Ausschlussgruende.pdf"
    assert "Ausschlussgründe" in b034["document"]          # word-wrap hyphen removed
    rus = _find(items, "RUS-Sanktionen")
    assert "jedem Mitglied einer Bietergemeinschaft" in rus["document"]
    assert rus["obligation"] == "mandatory" and rus["template"] == "VM191_15_Eigenerklaerung_RUS-Sanktionen.pdf"
    # rows 13/14 in the Aufforderung are numbered the other way round than the files: matched by form code
    b044 = _find(items, "(B-V 044)")
    assert b044["obligation"] == "if_applicable"
    assert b044["template"] == "VM191_14_B-V044_Erklaerung bevorzugte Beruecksichtigung.pdf"
    b047 = _find(items, "(B-V 047)")
    assert b047["obligation"] == "if_applicable" and "Bietergemeinschaft" in b047["document"]
    assert b047["document"].endswith("(von allen Mitgliedern zu unterschreiben)")      # Aufforderung p.8
    assert b047["template"] == "VM191_13_B-V047_Erklaerung Bietergemeinschaft.pdf"


def test_medical_data_sheet(items):
    sheet = _find(items, "Angebotsdatenblatt Medizinprodukte")
    assert sheet["document"] == "Angebotsdatenblatt Medizinprodukte (je Artikel ein Blatt)"
    assert sheet["template"] == "VM191_19_Angebotsdatenblatt Medizinprodukte.pdf"
    assert sheet["cite"]["page"] == 4


def test_informational_annexes_are_not_listed(items):
    docs = " ".join(i["document"] for i in items)
    for name in ("TS-QMA", "ZVB", "Lieferantenhandbuch", "DSGVO", "e-Rechnung", "Staatenliste", "Leistungsverzeichnis"):
        assert name not in docs


def test_further_proofs(items):
    reg = _find(items, "Handelsregisterauszug")
    # the 6 months count back from the Angebotsfrist 20.11.2026, so the extract must be issued on 20.05.2026 or later
    assert reg["document"] == ("Aktueller Berufs- oder Handelsregisterauszug in Kopie (nicht älter als 6 Monate, "
                               "gerechnet ab Angebotsfrist: ausgestellt am 20.05.2026 oder später)")
    assert reg["obligation"] == "mandatory" and reg["when"] == "with_offer" and reg["template"] is None
    assert _find(items, "EN ISO 13485")["obligation"] == "mandatory"
    assert _find(items, "MDR 2017/745")["document"].endswith("MDR 2017/745")
    assert _find(items, "Produktspezifikationen")["cite"]["page"] == 4


def test_evaluation_declarations(items):
    decl = [i for i in items if "Leistungskriterium" in i["document"]]
    # "(ggf. pro Los)": Aufforderung p.4, "Die als Nachweis dienenden Eigenerklärungen sind (ggf. pro Los) ..."
    assert [d["document"] for d in decl] == [
        "Eigenerklärung – Bieterangabe zum Verfalldatum (Leistungskriterium 1, bis 20 Leistungspunkte, ggf. je Los)",
        "Eigenerklärung – Bieterangabe zu einem nachweislich eingerichteten und zertifizierten "
        "Qualitätsmanagementsystem (Leistungskriterium 2, bis 10 Leistungspunkte, ggf. je Los)",
        "Eigenerklärung – Bieterangabe zu den Mindeststandards gem. § 21 Abs. 3 VerpackG "
        "(Leistungskriterium 3, bis 10 Leistungspunkte, ggf. je Los)",
    ]
    assert all(d["obligation"] == "if_applicable" and d["when"] == "with_offer" for d in decl)
    assert all(d["cite"]["file"] == "VM191_18_Bewertungsmatrix.pdf" for d in decl)
    # the generic "Ggf. Eigenerklärungen gem. Anlage 10 Bewertungsmatrix" line is replaced by the three above
    assert not any("Bewertungsmatrix" in i["document"] for i in items)


def test_conditional_documents(items):
    tr = _find(items, "Übersetzung")
    assert tr["obligation"] == "if_applicable" and tr["when"] == "with_offer"
    sub = _find(items, "Verpflichtungserklärung")
    assert sub["obligation"] == "if_applicable" and sub["when"] is None and sub["cite"]["page"] == 8
    third = _find(items, "Eignungsnachweise auch für Dritte")
    assert third["obligation"] == "if_applicable" and third["cite"]["page"] == 8
    pref = _find(items, "Bescheinigungen nach § 2 der Richtlinien")
    assert pref["obligation"] == "if_applicable" and pref["when"] == "with_offer"
    assert pref["cite"]["file"] == "VM191_14_B-V044_Erklaerung bevorzugte Beruecksichtigung.pdf"
    equiv = _find(items, "Abweichungen zum Richtfabrikat")
    assert equiv["obligation"] == "if_applicable" and equiv["when"] == "with_offer"
    assert "Preis- und Produktblatt" in equiv["cite"]["file"]


def test_on_request_proofs(items):
    req = [i for i in items if i["when"] == "on_request"]
    assert len(req) == 4
    assert all(i["obligation"] == "on_request" for i in req)
    excl = [i for i in req if i["cite"]["file"].startswith("VM191_12_B-V034")]
    assert {i["cite"]["page"] for i in excl} == {2, 4} and len(excl) == 3
    assert all(not i["cite"]["quote"].endswith(("Gerich", "zust")) for i in excl)    # cut at a word boundary
    _find(items, "Strafregister")
    _find(items, "Insolvenzregister")
    _find(items, "Sozialversicherungsbeiträgen")
    ins = _find(items, "Betriebshaftpflichtversicherung")      # RV § 7 (5): "... auf Anforderung nachzuweisen"
    assert ins["document"] == ("Nachweis der Betriebshaftpflichtversicherung (5 Millionen € für Personenschäden; "
                               "10 Millionen € für Sach- und Vermögenschäden)")
    assert ins["cite"]["file"] == "VM191_03_Entwurf_RV Tubus u. Katheter_ divers.pdf" and ins["cite"]["page"] == 8


def test_contract_proofs(items):
    # RV § 14 (2): with the offer; § 14 (5): with every delivery (both p.15)
    ce = _find(items, "Konformitätsbescheinigung, dass alle Geräte eine CE-Zulassung aufweisen")
    assert ce["obligation"] == "mandatory" and ce["when"] == "with_offer" and ce["cite"]["page"] == 15
    delivery = _find(items, "Prüf- und Konformitätsbescheinigung")
    assert delivery["when"] == "after_award" and delivery["document"].endswith("(Vermerk auf dem Lieferschein)")


def test_catalogue_data_after_award(items):
    cat = _find(items, "Katalogdaten")
    assert cat["when"] == "after_award" and cat["obligation"] == "mandatory"
    assert cat["document"].endswith("– unverzüglich nach Abschluss der RV")
    assert cat["template"] == "VM191_11_Vorlage-Katalogdaten_eClass10_1.xlsx"
    assert cat["cite"]["file"] == "VM191_03_Entwurf_RV Tubus u. Katheter_ divers.pdf" and cat["cite"]["page"] == 6
