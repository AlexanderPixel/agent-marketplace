"""Deadlines of VM191: from the Aufforderung's schedule and the e-Vergabe notice; Hinweisblätter can move them."""

from dataclasses import replace

from tenderhub.brief import Doc, cite, deadlines


def by_kind(items):
    return {i["kind"]: i for i in items if i["kind"] != "other"}


def test_vm191_deadlines(ctx):
    items = deadlines.extract(ctx)
    k = by_kind(items)
    assert k["questions"]["at"] == "2026-11-13"
    assert k["offer"]["at"] == "2026-11-20T08:00:00+01:00"
    assert k["binding_period"]["at"] == "2027-02-28"
    assert k["contract_start"]["at"] == "2026-12-01"
    assert k["contract_end"]["at"] == "2028-11-30"
    assert "Verlängerungsoption +1 Jahr" in k["contract_end"]["label"]
    award = [i for i in items if i["kind"] == "other" and "Zuschlag" in i["label"]]
    assert [a["at"] for a in award] == ["2026-12-16"]
    # soonest first, every kind once, nothing changed by the two Hinweisblätter
    assert [i["kind"] for i in items] == ["questions", "offer", "contract_start", "other", "other", "binding_period",
                                          "contract_end"]
    assert not any("geändert" in i["label"] for i in items)
    # 13.11.2026 is a Friday: the e-Vergabe maintenance window starts at 14:00 that day
    assert "Freitag: Plattform ab 14:00 Uhr wegen Wartung nicht erreichbar" in k["questions"]["label"]
    assert "Wartung" not in k["offer"]["label"]                    # Friday 08:00 is before the window
    assert "geschätzt" in k["contract_start"]["label"] and "geschätzt" in k["contract_end"]["label"]


def test_vm191_first_call_off_from_latest_hinweisblatt(ctx):
    call_off = [i for i in deadlines.extract(ctx) if i["label"].startswith("Erster Abruf")]
    assert len(call_off) == 1
    c = call_off[0]
    assert c["at"] is None and c["kind"] == "other"                # a quarter, not a day: no invented date
    assert "1. Quartal 2027" in c["label"] and "2. Abruf Januar 2028" in c["label"]
    assert c["cite"]["folder"] == "Vergabeunterlagen/Version 3"    # the newest statement


def test_vm191_cites_are_verbatim(ctx):
    texts = {d.filename: " ".join(d.text.split()) for d in ctx.docs}
    texts |= {p["url"]: " ".join(p["markdown"].split()) for p in ctx.pages}
    for item in deadlines.extract(ctx):
        c = item["cite"]
        assert c["quote"] in texts[c["file"]], item
    offer = by_kind(deadlines.extract(ctx))["offer"]["cite"]
    assert offer["file"].startswith("VM191_01_Aufforderung") and offer["page"] == 7


def test_hinweisblatt_moving_the_offer_deadline_wins(ctx):
    moved = Doc(None, "VM191_3. Hinweisblatt.pdf", "Vergabeunterlagen/Version 4",
                "[page 1]\n3. Hinweisblatt\nDie Angebotsfrist wird verlängert auf den 04.12.2026, 10:00 Uhr.\n", 1)
    ctx2 = replace(ctx, docs=[*ctx.docs, moved])
    offer = by_kind(deadlines.extract(ctx2))["offer"]
    assert offer["at"] == "2026-12-04T10:00:00+01:00"
    assert "geändert durch VM191_3. Hinweisblatt" in offer["label"] and "vorher 20.11.2026 08:00" in offer["label"]
    assert offer["cite"] == cite(moved, 1, "Angebotsfrist wird verlängert auf den 04.12.2026, 10:00 Uhr")


def test_hinweisblatt_old_and_new_date_in_one_sentence(ctx):
    moved = Doc(None, "VM191_3. Hinweisblatt.pdf", "Vergabeunterlagen/Version 4",
                "[page 1]\nDie Angebotsfrist wird vom 20.11.2026 auf den 27.11.2026, 10:00 Uhr verschoben.\n"
                "Die Frist für die Einreichung von Bieterfragen endet nunmehr am 18.11.2026 (bisher 13.11.2026).\n", 1)
    k = by_kind(deadlines.extract(replace(ctx, docs=[*ctx.docs, moved])))
    assert k["offer"]["at"] == "2026-11-27T10:00:00+01:00"
    assert k["questions"]["at"] == "2026-11-18" and "vorher 13.11.2026" in k["questions"]["label"]


def test_answers_to_bidder_questions_do_not_move_deadlines(ctx):
    qa = Doc(None, "VM191_3. Hinweisblatt.pdf", "Vergabeunterlagen/Version 4",
             "[page 1]\n3. Hinweisblatt (Stand 05.11.2026)\nAntworten auf neue Bieterfragen vom 04.11.2026: "
             "Das Preisblatt wurde geändert.\n", 1)
    k = by_kind(deadlines.extract(replace(ctx, docs=[*ctx.docs, qa])))
    assert k["questions"]["at"] == "2026-11-13" and k["offer"]["at"] == "2026-11-20T08:00:00+01:00"


def test_platform_deadline_differs_from_invitation(ctx):
    pages = [{**p, "markdown": p["markdown"].replace("Frist für den Eingang der Angebote: 20/11/2026 08:00",
                                                     "Frist für den Eingang der Angebote: 27/11/2026 08:00")}
             for p in ctx.pages]
    docs = [d for d in ctx.docs if "Hinweise von der e-Vergabe" not in d.filename]
    offer = by_kind(deadlines.extract(replace(ctx, pages=pages, docs=docs)))["offer"]
    assert offer["at"] == "2026-11-27T08:00:00+01:00"
    assert "zuvor 20.11.2026 08:00" in offer["label"]
