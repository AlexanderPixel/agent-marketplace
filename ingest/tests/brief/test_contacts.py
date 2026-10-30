"""Contacts of VM191: BAIUDBw DL II 6 (Frau Döhrer), Kaufhaus des Bundes catalogue team, Vergabekammer des Bundes."""

from tenderhub.brief import contacts


def test_vm191_contacts(ctx):
    items = contacts.extract(ctx)
    first = items[0]
    assert first["role"] == "Vergabestelle (BAIUDBw DL II 6)"
    assert first["name"] == "Frau Döhrer"
    assert first["email"] == "BAIUDBwDLII6EinkaufSanitaetsmaterial@bundeswehr.org"
    assert first["phone"] == "0261-896 61152"
    assert first["cite"]["page"] == 1 and first["cite"]["file"].startswith("VM191_01_Aufforderung")
    by_email = {i["email"].lower(): i for i in items}
    assert len(by_email) == len(items)                  # buyer from the notice is not repeated
    assert by_email["katalogdaten@kdbund.bund.de"]["phone"] == "022899-610-2289"
    vk = by_email["vk@bundeskartellamt.bund.de"]
    assert vk["role"] == "Vergabekammer (Nachprüfung)" and vk["phone"] == "+49 22894990"
    assert "noreply.esender_hub@bescha.bund.de" not in by_email


def test_vm191_contact_cites_are_verbatim(ctx):
    texts = {d.filename: " ".join(d.text.split()) for d in ctx.docs}
    texts |= {p["url"]: " ".join(p["markdown"].split()) for p in ctx.pages}
    for item in contacts.extract(ctx):
        c = item["cite"]
        assert c["quote"] in texts[c["file"]], item
