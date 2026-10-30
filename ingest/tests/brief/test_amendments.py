"""Amendments of VM191: Version 2 (1. Hinweisblatt, 27.10.2026) and Version 3 (2. Hinweisblatt, 28.10.2026)."""

from tenderhub.brief import Doc, TenderContext
from tenderhub.brief.amendments import extract


def _by_version(ctx):
    return {a["version"]: a for a in extract(ctx)}


def test_one_item_per_amendment_round_oldest_first(ctx):
    items = extract(ctx)
    assert [a["version"] for a in items] == [2, 3]
    assert [a["date"] for a in items] == ["2026-10-27", "2026-10-28"]
    assert [a["title"] for a in items] == ["1. Hinweisblatt", "2. Hinweisblatt"]


def test_cites_the_hinweisblatt_verbatim(ctx):
    a = _by_version(ctx)
    assert a[2]["cite"] == {"file": "VM191_1. Hinweisblatt.pdf", "folder": "Vergabeunterlagen/Version 2", "page": 1,
                            "quote": "1. Hinweisblatt (Stand 27.10.2026)"}
    assert a[3]["cite"]["file"] == "VM191_2. Hinweisblatt.pdf"
    assert a[3]["cite"]["quote"] == "2. Hinweisblatt (Stand 28.10.2026)"
    for item in a.values():
        doc = next(d for d in ctx.docs if d.filename == item["cite"]["file"])
        assert item["cite"]["quote"] in " ".join(doc.text.split())


def test_first_round_answers_the_call_off_question(ctx):
    changes = _by_version(ctx)[2]["changes"]
    qa = changes[0]
    assert qa.startswith("„Wäre es möglich, sich auf Lieferungen über einen längeren Zeitraum")
    assert "Laufzeit von 2 Jahren (+ 1 Jahr Option)" in qa
    assert "1. Quartal 2027" in qa and "Januar 2028" in qa
    # the answer is kept whole: its last sentences carry obligations (full quantity by the delivery date,
    # no obligation to call off up to the Höchstmenge)
    assert "die vollständige Menge muss jedoch bis zum jeweiligen Lieferdatum vollständig erfolgt sein" in qa
    assert qa.split(" [")[0].endswith("eine Verpflichtung zur Abnahme bis zu der Höchstmenge besteht nicht.")
    # "Mindestabnahmemengen ergeben sich aus den Preis- und Produktblättern" is resolved per lot
    assert qa.endswith("[Mindestabnahmemenge laut Preis- und Produktblättern: Los 1 1.800 Stück, Los 2 3.360 Stück, "
                       "Los 3 100 Stück]")
    assert ("Änderungsbekanntmachung übermittelt am 27.10.2026 15:29 (Hauptgrund: Aktualisierte Informationen; "
            "ohne Beschreibung der Änderung)") in changes


def test_call_off_month_that_contradicts_a_lot_sheet_is_flagged(ctx):
    changes = _by_version(ctx)[2]["changes"]
    # the 1. Hinweisblatt says "2. Abrufmenge im Januar 2028"; the Los 2 Preis- und Produktblatt says 02/2028
    conflicts = [c for c in changes if c.startswith("Abweichung")]
    assert conflicts == ["Abweichung: das Hinweisblatt nennt für die 2. Abrufmenge „Januar 2028“, das Preis- und "
                         "Produktblatt Los 2 aber 02/2028 (2.240 Stück)"]
    assert not any(c.startswith("Abweichung") for c in _by_version(ctx)[3]["changes"])


def test_second_round_reports_only_its_new_questions(ctx):
    changes = _by_version(ctx)[3]["changes"]
    # the 2. Hinweisblatt repeats question 1 of the 1. Hinweisblatt; it must not be reported twice
    assert not any("Wäre es möglich" in c for c in changes)
    assert changes[0].startswith("1. Abruf Mengen: „Wie viele Einheiten müssen im Januar 2027 geliefert werden?“")
    # the answer only points to the Preis- und Produktblätter; their 1. Abrufmenge per lot is appended
    assert changes[0].endswith("[1. Abrufmenge laut Preis- und Produktblättern: Los 1 600 Stück, Los 2 1.120 Stück, "
                               "Los 3 30 Stück (01/2027)]")
    assert "„Wie viel Zeit wird zwischen Abruf und Lieferdatum gewährt?“" in changes[1]
    assert "innerhalb von 5 bis 14 Werktagen" in changes[1]
    assert changes[1].endswith("in diesen Fällen kann ein neuer Liefertermin bestimmt werden.")
    assert not any("…" in c for c in changes)


def test_files_changed_ignores_reissued_leistungsverzeichnis(ctx):
    a = _by_version(ctx)
    # the Leistungsverzeichnis is re-issued each round but only its Erstellungsdatum/version stamp differs
    assert a[2]["files_changed"] == ["VM191_1. Hinweisblatt.pdf"]
    assert a[3]["files_changed"] == ["VM191_2. Hinweisblatt.pdf"]
    for item in a.values():
        assert item["changes"][-1].startswith("Leistungsverzeichnis.aidf, Leistungsverzeichnis.pdf neu bereitgestellt, "
                                              "inhaltlich unverändert")


def test_real_content_change_is_detected_and_unversioned_tender_has_none():
    v1 = Doc(1, "LV.pdf", "Vergabeunterlagen/Version 1", "Erstellungsdatum: 20.10.2026\nVersion 1\nMenge 2400 EA")
    v2 = Doc(2, "LV.pdf", "Vergabeunterlagen/Version 2", "Erstellungsdatum: 27.10.2026\nVersion 2\nMenge 1800 EA")
    items = extract(TenderContext({}, [], [v1, v2]))
    assert len(items) == 1
    assert items[0]["files_changed"] == ["LV.pdf"] and items[0]["date"] == "2026-10-27"
    assert items[0]["title"] == "Version 2 der Vergabeunterlagen"
    assert extract(TenderContext({}, [], [v1])) == []


def test_generic_information_sheet_is_not_an_amendment_sheet():
    v1 = Doc(1, "LV.pdf", "Vergabeunterlagen/Version 1", "Menge 10 EA")
    v2 = Doc(2, "17_Informationsblatt DSGVO.pdf", "Vergabeunterlagen/Version 2",
             "Informationsblatt (Stand 27.10.2026)\nDie Angebotsfrist wird nicht berührt. Daten werden geändert.")
    items = extract(TenderContext({}, [], [v1, v2]))
    assert items[0]["title"] == "Version 2 der Vergabeunterlagen"
    assert items[0]["changes"] == [] and items[0]["files_changed"] == ["17_Informationsblatt DSGVO.pdf"]
