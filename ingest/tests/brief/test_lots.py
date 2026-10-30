"""Lots of VM191: three lots from the newest Leistungsverzeichnis, specs from the per-lot Preis- und Produktblatt."""

from tenderhub.brief import Doc, TenderContext
from tenderhub.brief.lots import extract


def test_three_lots_from_newest_lv(ctx):
    lots = extract(ctx)
    assert [(x["lot"], x["title"], x["quantity"], x["unit"]) for x in lots] == [
        ("1", "Endobronchialblocker-Set, 9Fr", 2400, "EA"),
        ("2", "Epistaxis-Ballonkatheter, Doppelballon", 4480, "EA"),
        ("3", "Tubus, Endotracheal-, spiralverstärkt, ID3,0", 130, "EA"),
    ]
    for i, x in enumerate(lots, 1):
        c = x["cite"]
        assert (c["file"], c["folder"], c["page"]) == ("Leistungsverzeichnis.pdf", "Vergabeunterlagen/Version 3", i)
        doc = next(d for d in ctx.docs if d.filename == c["file"] and d.folder == c["folder"])
        assert c["quote"] in " ".join(dict(doc.page_texts())[c["page"]].split())


def test_lot_specs_from_product_sheets(ctx):
    d1, d2, d3 = (x["description"] for x in extract(ctx))
    assert d1.startswith("Endobronchialblocker zur platzierung des rechten oder linken Hauptbronchus")
    assert "Katheter 9 Fr" in d1 and "Katheterlänge 78cm" in d1 and "latexfrei" in d1
    assert ("Richtfabrikat o. glw.: Arndt Endobronchialblocker-Set (Art.-Nr. G44120, Cook Deutschland GmbH), "
            "Set aus 1x Bronchialblocker, 1x Arndt Multiport-Adapter, 1x Saugadapter") in d1
    assert "Mindestabnahme 1.800 Stück von max. 2.400 Stück (Abrufe 01/2027: 600, 01/2028: 1.200)" in d1
    assert "Mindesthaltbarkeit 28 Monate" in d1 and "max. 1 Stück pro Packung" in d1
    assert "Achtung" not in d1
    assert "zwei separat befüllbare Ballons" in d2 and "max. 10ml und max. 30ml" in d2
    assert "Ultra-Stat™ Epistaxis Katheter, steril (Art.-Nr. 2010701 (ohne Spritze), Spiggle & Theis" in d2
    assert "Mindestabnahme 3.360 Stück von max. 4.480 Stück (Abrufe 01/2027: 1.120, 02/2028: 2.240)" in d2
    # the 1./2. Hinweisblatt answer says the 2nd call-off is in January 2028; lot 2's sheet says 02/2028
    assert "Achtung: 2. Abruf laut Produktblatt 02/2028, laut Hinweisblatt (Vergabeunterlagen/Version 3) im Januar 2028" in d2
    assert "Mindesthaltbarkeit 48 Monate" in d2
    assert d3.startswith("Endotrachealtubus spiralverstärkt für die nasale und orale Intubation")
    assert "Hochvolumen-Niederdruckcuff, inklusive 15mm-Konnektor; PVC; ID 3,00mm; AD 5,0mm" in d3
    assert "Shiley™ oraler/nasaler spiralverstärkter Endotrachealtubus mit Cuff (Art.-Nr. 118-30, Medtronic GmbH)" in d3
    assert "Mindestabnahme 100 Stück von max. 130 Stück (Abrufe 01/2027: 30, 01/2028: 70)" in d3
    assert "max. 10 Stück pro Packung" in d3
    assert "Mindesthaltbarkeit" not in d3  # the sheet gives none for lot 3
    assert "Achtung" not in d3


_SHEET_TEXT = """[page 1]
Los Los-Bezeichnung Beschreibung Richtfabrikat - oder gleichwertig
Beispiel Einmalspritze
Aufbau: Beispiel
1 Feldbett
Aufbau: faltbares Feldbett aus Aluminium mit Bezug
Länge: 190 cm
Breite: 70 cm
Belastbarkeit: 120 kg
Feldbett Comfort
Art.-Nr. FB-1
Muster GmbH
2 Stück pro
Packung
36 Monate 300
1. Abrufmenge 03/2027: 100 Stück
2. Abrufmenge 03/2028: 100 Stück
3. Abrufmenge 03/2029: 100 Stück
600 Stück gemäß Leistungsbeschreibung
Ort, Datum
"""


def test_sheet_with_three_call_offs_and_amended_dates():
    lv = Doc(1, "Leistungsverzeichnis.pdf", "Vergabeunterlagen/Version 1",
             "[page 1]\nLOS 1 - 500 EA Feldbett, faltbar\n")
    sheet = Doc(2, "Anlage 2_LOS 1_Preis- und Produktblatt_Feldbett.pdf", "Vergabeunterlagen/Version 1", _SHEET_TEXT)
    amend = Doc(3, "1. Hinweisblatt.pdf", "Vergabeunterlagen/Version 2",
                "[page 1]\nDie 1. Abrufmenge wird im 1. Quartal 2027 abgerufen; die 3. Abrufmenge im Mai\n2029.")
    (lot,) = extract(TenderContext({}, [], [lv, sheet, amend]))
    d = lot["description"]
    assert d.startswith("faltbares Feldbett aus Aluminium mit Bezug; Länge 190 cm; Breite 70 cm; Belastbarkeit 120 kg.")
    assert "Richtfabrikat o. glw.: Feldbett Comfort (Art.-Nr. FB-1, Muster GmbH)." in d
    assert "Mindestabnahme 300 Stück von max. 600 Stück (Abrufe 03/2027: 100, 03/2028: 100, 03/2029: 100)" in d
    assert "Achtung: Höchstmenge im Produktblatt (600) weicht vom LV ab" in d
    assert "1. Abruf" not in d  # 03/2027 lies in the 1st quarter 2027
    assert "Achtung: 3. Abruf laut Produktblatt 03/2029, laut Hinweisblatt (Vergabeunterlagen/Version 2) im Mai 2029" in d
    assert "Mindesthaltbarkeit 36 Monate" in d and "max. 2 Stück pro Packung" in d


def test_falls_back_to_notice_pages():
    md = ("## 5. Lot\n\n5.1. Lot: LOT-0001\n\nTitle: LOS 1 - 50 EA Feldbett, faltbar\n\n"
          "5.1. Lot: LOT-0002\n\nTitle: Zubehör Schlafsack\n")
    lots = extract(TenderContext({}, [{"url": "https://ted.example/n", "title": "", "markdown": md}], []))
    assert [(x["lot"], x["title"], x["quantity"], x["unit"]) for x in lots] == [
        ("1", "Feldbett, faltbar", 50, "EA"), ("2", "Zubehör Schlafsack", None, None)]
    assert lots[0]["cite"] == {"file": "https://ted.example/n", "folder": None, "page": None,
                               "quote": "Title: LOS 1 - 50 EA Feldbett, faltbar"}


def test_without_lots_lists_lv_positions():
    lv = Doc(1, "Leistungsverzeichnis.pdf", "Vergabeunterlagen/Version 1",
             "[page 1]\nA) FELDBETT, FALTBAR\nB)\nC) 00010 H - / - 1.250 EA 01.12.2026 ____\n")
    lots = extract(TenderContext({}, [], [lv]))
    assert [(x["lot"], x["title"], x["quantity"], x["unit"]) for x in lots] == [(None, "FELDBETT, FALTBAR", 1250, "EA")]
    assert lots[0]["cite"]["page"] == 1
