"""How to bid on VM191: e-Vergabe, Textform, German, any lots, no variants."""

from tenderhub.brief import submission


def test_vm191_submission(ctx):
    items = submission.extract(ctx)
    v = {i["label"]: i["value"] for i in items}
    assert v["Form der Angebotsabgabe"] == ("elektronisch (in Textform) über die e-Vergabe-Plattform des Bundes "
                                           "www.evergabe-online.de")
    assert v["Einreichung unter"] == "https://www.evergabe-online.de/tenderdetails.html?id=900460"
    assert v["Signatur"].startswith("keine e-Signatur nötig (Textform)")
    assert v["Sprache"].startswith("Deutsch")
    assert v["Lose"].startswith("Angebot auf ein, mehrere oder alle Lose")
    assert v["Nebenangebote"] == "nicht zugelassen"
    assert v["Mehrere Hauptangebote"].startswith("nicht zugelassen")
    assert v["Bieterfragen"].startswith("nur über e-Vergabe") and "Fr 14:00 – Sa 23:59" in v["Bieterfragen"]
    assert v["Verspätete Angebote"].startswith("Ausschluss")
    # the notice says 4 months, the invitation names a fixed binding date: show both
    assert v["Angebotsgültigkeit (Bekanntmachung)"].startswith("4 Monate")
    assert "Bindefrist bis 28.02.2027" in v["Angebotsgültigkeit (Bekanntmachung)"]
    assert v["Verhandlungen"].startswith("keine")
    assert v["Muster/Proben"].startswith("nicht vorgesehen") and "Musterprodukte anfordern" in v["Muster/Proben"]
    assert "zweifelsfrei" in v["Änderung/Rücknahme"] and "Rücknahme" in v["Änderung/Rücknahme"]
    assert "Ausschluss" in v["Nachforderung"]
    assert "§ 56 Abs. 3 VgV" in v["Nicht nachreichbar"]           # notice: performance documents cannot be added later
    assert "Bieterfragen" in v["Änderungswünsche zum Vertrag"]
    assert v["Kommunikation"].startswith("Nachrichtenpostfach der Plattform")
    assert "Zf. 6" in v["Geschäftsgeheimnisse"]
    assert "Aufforderung" in v["Rangfolge"]
    assert v["Preise"] == "Nettopreise in EUR"
    assert "Teilnahme aktivieren" in v
    assert "Verspätete Bieterfragen" not in v          # the documents say nothing about late questions
    assert len(v) == len(items)                         # no duplicate labels
    assert items[0]["label"] == "Form der Angebotsabgabe"


def test_vm191_submission_cites_are_verbatim(ctx):
    texts = {d.filename: " ".join(d.text.split()) for d in ctx.docs}
    texts |= {p["url"]: " ".join(p["markdown"].split()) for p in ctx.pages}
    for item in submission.extract(ctx):
        c = item["cite"]
        assert c["quote"] in texts[c["file"]], item
