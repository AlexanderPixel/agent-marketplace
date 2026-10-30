"""Brief section: submission. See docs/brief-format.md for the item fields.

How to bid: platform and form, signature, language, lots, variants, questions, what gets an offer excluded.
Read from the "Aufforderung zur Angebotsabgabe" (EinkaufBw / BAAINBw template wording) with the platform pages
(e-Vergabe details, TED; German or English eForms rendering) as fallback and for what only the notice states.
"""

from __future__ import annotations

import re

from . import TenderContext, cite
from .deadlines import _from_aufforderung, aufforderung, clean, de_date, page_search, platform_hints, search

F = re.I | re.S


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, F)


def _value(s: str) -> str:
    """Readable value from PDF text: undo line-break hyphenation ("Haupt- angebote", "e- Vergabe")."""
    s = re.sub(r"(\w)-[ \t]*\n\s*(?=[a-zäöüß])", r"\1", s)
    return re.sub(r"\b([eE])-\s+(?=[A-ZÄÖÜ])", r"\1-", clean(s))


def extract(ctx: TenderContext) -> list[dict]:
    auf = aufforderung(ctx)
    items: list[dict] = []
    seen: set[str] = set()

    def add(label: str, value: str | None, source, page, quote: str) -> None:
        if value and label not in seen:
            seen.add(label)
            items.append({"label": label, "value": value, "cite": cite(source, page, quote)})

    def doc_hit(pattern: str):
        return search(auf, _rx(pattern))

    def page_hit(pattern: str):
        return page_search(ctx, _rx(pattern))

    # -- form and platform
    form = doc_hit(r"Form\s+der\s+Angebotsabgabe\s*(?:Das\s+Angebot\s+ist\s*)?(?:[☒xX]\s*)?(.{5,220}?)\s*einzureichen")
    form_text = ""
    if form:
        page, m = form
        form_text = _value(m.group(1))
        add("Form der Angebotsabgabe", form_text, auf, page, m.group(0))
    else:
        hit = page_hit(r"(?:Elektronische\s+Einreichung|Electronic\s+submission):\s*([^\n]+)")
        if hit:
            add("Form der Angebotsabgabe", "elektronische Einreichung: " + clean(hit[1].group(1)), hit[0], None,
                hit[1].group(0))
    hit = page_hit(r"(?:Adresse\s+für\s+die\s+Einreichung|Address\s+for\s+submission):\s*\[?(https?://[^\s\])]+)")
    if hit:
        add("Einreichung unter", hit[1].group(1), hit[0], None, hit[1].group(0))

    # -- the platform requires activating participation to get changes and ask questions
    activate = _rx(r"Nur\s+wenn\s+Sie\s+fristgerecht\s+die\s+Teilnahme.{0,160}?Bieterfragen[^.]{0,60}\.")
    hit = page_search(ctx, activate)
    src = (hit[0], None, hit[1]) if hit else None
    if not src and (h := search(platform_hints(ctx), activate)):
        src = (platform_hints(ctx), h[0], h[1])
    if src:
        add("Teilnahme aktivieren", "auf der Plattform aktivieren – nur dann Benachrichtigung über Änderungen und "
            "Bieterfragen möglich", src[0], src[1], src[2].group(0))

    # -- signature
    sig = doc_hit(r"bei\s+einem\s+elektronisch\s+übermittelten\s+Angebot\s+in\s+Textform\s+muss\s+(?:der\s+)?"
                  r"Bieter\s+erkennbar\s+sein")
    if re.search(r"qualifiziert\w*\s+elektronisch\w*\s+Signatur", form_text, re.I):
        add("Signatur", "qualifizierte elektronische Signatur erforderlich", auf, form[0], form[1].group(0))
    elif re.search(r"fortgeschritten\w*\s+elektronisch\w*\s+Signatur", form_text, re.I):
        add("Signatur", "fortgeschrittene elektronische Signatur erforderlich", auf, form[0], form[1].group(0))
    elif sig and re.search(r"Textform", form_text, re.I):
        add("Signatur", "keine e-Signatur nötig (Textform) – Bieter muss erkennbar sein", auf, sig[0],
            sig[1].group(0))

    # -- language
    lang = doc_hit(r"Das\s+Angebot\s+ist\s+in\s+allen\s+seinen\s+Teilen\s+in\s+(\w+)\s+Sprache\s+einzureichen")
    if lang:
        page, m = lang
        value = {"deutscher": "Deutsch", "englischer": "Englisch"}.get(m.group(1).lower(), m.group(1))
        if re.search(r"Fremdsprachen.{0,120}?Über-?\s*setzung", auf.text[m.end(): m.end() + 300], re.S):
            value += " (fremdsprachige Unterlagen mit Übersetzung)"
        add("Sprache", value, auf, page, m.group(0))
    elif hit := page_hit(r"(?:Sprachen,\s+in\s+denen\s+Angebote[^:]*|Languages\s+in\s+which\s+tenders[^:]*):\s*([^\n]+)"):
        add("Sprache", clean(hit[1].group(1)), hit[0], None, hit[1].group(0))

    # -- lots
    lots = doc_hit(r"Es\s+kann\s+auf\s+ein\s+einzelnes,\s*mehrere\s+oder\s+alle\s+Lose\s+geboten\s+wer-?\s*den")
    if lots:
        combined = re.search(r"mehrere\s+Lose\s+können\s+in\s+einem\s+Angebot\s+zusammenge-?\s*fasst",
                             auf.text[max(0, lots[1].start() - 400): lots[1].start()])
        add("Lose", "Angebot auf ein, mehrere oder alle Lose" + ("; mehrere Lose in einem Angebot möglich" if combined else ""),
            auf, lots[0], lots[1].group(0))
    elif hit := page_hit(r"(?:Höchstzahl\s+der\s+Lose,\s+für\s+die\s+ein\s+Bieter\s+Angebote\s+einreichen\s+kann|"
                         r"Maximum\s+number\s+of\s+lots\s+for\s+which\s+one\s+tenderer\s+can\s+submit\s+tenders):\s*(\d+)"):
        add("Lose", f"Angebot auf bis zu {hit[1].group(1)} Lose", hit[0], None, hit[1].group(0))

    # -- variants and multiple main offers
    var = doc_hit(r"Nebenangebote\s+sind:\s*(nicht\s+zugelassen|zugelassen)")
    if var:
        add("Nebenangebote", clean(var[1].group(1)), auf, var[0], var[1].group(0))
    elif hit := page_hit(r"(?:Nebenangebote|Variants):\s*([^\n]+)"):
        add("Nebenangebote", clean(hit[1].group(1)), hit[0], None, hit[1].group(0))
    main = doc_hit(r"mehrere\s+Haupt-?\s*angebote\s+sind:\s*(nicht\s+zugelassen|zugelassen)")
    if main:
        value = _value(main[1].group(1))
        if value.startswith("nicht") and re.search(r"werden\s+alle\s+Haupt-?\s*angebote\s+von\s+der\s+Wertung\s+"
                                                    r"ausgeschlossen", auf.text):
            value += " (sonst Ausschluss aller Hauptangebote)"
        add("Mehrere Hauptangebote", value, auf, main[0], main[1].group(0))

    # -- bidder questions
    q = doc_hit(r"Bieterfragen\s+sind\s+ausschließlich\s+über\s+(?:die\s+)?(.{3,120}?)\s+zu\s+stellen")
    if q:
        value = "nur über " + _value(q[1].group(1))
        win = auf.text[q[1].end(): q[1].end() + 1200]
        if mw := re.search(r"jedem\s+Freitag\s+ab\s+(\d{1,2}:\d{2})\s+Uhr\s+bis\s+jeden\s+Samstag\s+(\d{1,2}:\d{2})", win):
            value += f"; Fr {mw.group(1)} – Sa {mw.group(2)} keine Kommunikation (Wartung)"
        add("Bieterfragen", value, auf, q[0], q[1].group(0))
    change = doc_hit(r"Begründete\s+Änderungsvorschläge\s+hinsichtlich\s+der\s+Vertragsunterlagen.{0,80}?"
                     r"im\s+Rahmen\s+von\s+Bieterfragen.{0,60}?einzureichen")
    if change:
        add("Änderungswünsche zum Vertrag", "nur begründet im Rahmen von Bieterfragen, rechtzeitig vor der Fragenfrist", auf, change[0], change[1].group(0))
    inbox = doc_hit(r"verpflichtet\s+sich\s+der\s+Bieter.{0,80}?Nachrichtenpostfach\s+der\s+Platt-?\s*form\s+für\s+den\s+"
                    r"Empfang\s+rechtserheblicher\s+Erklärungen")
    if inbox:
        mail = re.search(r"Aufklärungs\W*und/oder\s+Nachforde-?\s*rungsverlangen.{0,80}?per\s+E-?\s*Mail",
                         auf.text[inbox[1].end(): inbox[1].end() + 400], re.S)
        add("Kommunikation", "Nachrichtenpostfach der Plattform regelmäßig prüfen (rechtserhebliche Erklärungen)"
            + ("; Aufklärung/Nachforderung auch per E-Mail" if mail else ""), auf, inbox[0], inbox[1].group(0))
    late_q = doc_hit(r"(?:später|verspätet|nach\s+(?:Ablauf\s+)?(?:dieser|der)\s+Frist)\s+eingehende\s+"
                     r"(?:Bieter-?\s*)?(?:fragen|Anfragen)[^.]{0,160}\.")
    if late_q:
        add("Verspätete Bieterfragen", _value(late_q[1].group(0)), auf, late_q[0], late_q[1].group(0))

    # -- exclusion traps
    late = doc_hit(r"Angebote,\s+die\s+nicht\s+fristgerecht\s+eingegan-?\s*gen\s+sind,\s+werden\s+vom\s+"
                   r"Vergabeverfahren\s+ausgeschlossen(,\s+es\s+sei\s+denn)?")
    if late:
        add("Verspätete Angebote", "Ausschluss" + (" (außer der Bieter hat die Verspätung nicht zu vertreten)"
                                                   if late[1].group(1) else ""), auf, late[0], late[1].group(0))
    forms = doc_hit(r"Nichtverwend\s*en\s+der\s+Formulare\s+oder\s+Abänderung\s+der\s+Vergabeunterla-?\s*gen"
                    r".{0,160}?zum\s+Ausschluss\s+des\s+Angebots")
    if forms:
        add("Formulare", "beigefügte Formulare nutzen; Änderung der Vergabeunterlagen führt zum Ausschluss",
            auf, forms[0], forms[1].group(0))

    # -- validity, negotiations, samples, prices, corrections, Nachforderung, consortia
    valid = page_hit(r"(?:Dauer,\s+während\s+der\s+das\s+Angebot\s+gültig\s+bleiben\s+muss|Duration\s+during\s+"
                     r"which\s+the\s+tender\s+must\s+remain\s+valid):\s*(\d+)\s*(\w+)")
    if valid:
        n, unit = valid[1].group(1), valid[1].group(2).lower()
        unit = next((de for key, de in (("mon", "Monate"), ("tag", "Tage"), ("day", "Tage"), ("woche", "Wochen"),
                                        ("week", "Wochen"), ("jahr", "Jahre"), ("year", "Jahre")) if unit.startswith(key)),
                    valid[1].group(2))
        value = f"{n} {unit}"
        bind = _from_aufforderung(auf).get("binding_period")
        if bind:
            value += f"; Aufforderung nennt Zuschlags- und Bindefrist bis {de_date(bind['at'])}"
        add("Angebotsgültigkeit (Bekanntmachung)", value, valid[0], None, valid[1].group(0))
    neg = doc_hit(r"Verhandlungen\s+sind\s+[^.]{0,60}?nicht\s+vorgesehen\.(\s*Es\s+ist\s+ein\s+verbindliches\s+"
                  r"Angebot\s+abzugeben)?")
    if neg:
        add("Verhandlungen", "keine – verbindliches Angebot abgeben" if neg[1].group(1) else "keine",
            auf, neg[0], neg[1].group(0))
    samples = doc_hit(r"Muster/?\s*Proben\s+sind\s+im\s+Vergabeverfahren\s+(nicht\s+)?vorgesehen")
    if samples:
        value = "nicht vorgesehen" if samples[1].group(1) else "vorgesehen (siehe Aufforderung)"
        reserve = next((h for d in ctx.docs_matching(r"Preis|Produktblatt|Leistungsbeschreibung")
                        if (h := search(d, _rx(r"behält\s+sich\s+der\s+Auftraggeber\s+vor,\s+Muster\w*\s+"
                                               r"anzufordern")))), None)
        if samples[1].group(1) and reserve:
            value += " – laut Preis- und Produktblatt kann der AG bei Unklarheiten Musterprodukte anfordern"
        add("Muster/Proben", value, auf, samples[0], samples[1].group(0))
    prices = doc_hit(r"Preise\s+sind\s+[^.]{0,60}?als\s+(Netto|Brutto)preise\s+in\s+(EUR|Euro)")
    if prices:
        add("Preise", f"{prices[1].group(1)}preise in EUR", auf, prices[0], prices[1].group(0))
    corr = doc_hit(r"Änderungen\s+an\s+oder\s+Berichtigungen\s+von\s+Eintragungen.{0,80}?bis\s+zum\s+Ablauf\s+der\s+"
                   r"Angebotsfrist\s+zulässig\.\s*Sie\s+sind\s+als\s+solche\s+zwei-?\s*felsfrei\s+zu\s+kennzeichnen\."
                   r"(\s*Angebote\s+mit\s+nicht\s+zweifelsfreien.{0,80}?ausgeschlossen\.)?")
    withdraw = doc_hit(r"Angebote\s+können\s+bis\s+zum\s+Ablauf\s+der\s+Angebotsfrist[^.]{0,80}?zurückgezogen\s+werden")
    if corr:
        add("Änderung/Rücknahme", "Korrekturen bis Ablauf der Angebotsfrist, zweifelsfrei gekennzeichnet"
            + (" (sonst Ausschluss)" if corr[1].group(1) else "") + ("; Rücknahme bis Fristende möglich" if withdraw else ""),
            auf, corr[0], corr[1].group(0))
    elif withdraw:
        add("Änderung/Rücknahme", "Rücknahme bis Ablauf der Angebotsfrist möglich", auf, withdraw[0],
            withdraw[1].group(0))
    nach = doc_hit(r"Nachforderung\s+gemäß\s+§\s*56\s+Abs\.\s*2\s+VgV(.{0,200}?nicht\s+vorgelegt,\s+wird\s+das\s+"
                   r"Angebot\s+vom\s+weiteren\s+Vergabeverfahren\s+ausgeschlossen)?")
    if nach:
        add("Nachforderung", "möglich nach § 56 Abs. 2 VgV" + ("; wird die Nachforderungsfrist versäumt: Ausschluss"
                                                             if nach[1].group(1) else ""),
            auf, nach[0], nach[1].group(0))
    no_later = page_hit(r"Nachforderung\s+von\s+leistungsbezogenen\s+Unterlagen,\s+die\s+die\s+Wirtschaftlichkeits"
                        r"bewertung.{0,120}?ausgeschlossen")
    if no_later:
        add("Nicht nachreichbar", "leistungsbezogene Unterlagen, die die Wertung nach den Zuschlagskriterien betreffen, werden "
            "nicht nachgefordert (§ 56 Abs. 3 VgV) – vollständig mit dem Angebot einreichen", no_later[0], None,
            no_later[1].group(0))
    bg = doc_hit(r"Die\s+Erklärung\s+ist\s+von\s+allen\s+Mitgliedern\s+der\s+Bietergemeinschaft\s+zu\s+unterschrei-?\s*ben")
    if bg:
        add("Bietergemeinschaften", "zulässig; bevollmächtigten Vertreter benennen, Erklärung (B-V 047) von allen "
            "Mitgliedern unterschreiben" if re.search(r"B-V\s*047", auf.text) else
            "zulässig; Erklärung von allen Mitgliedern unterschreiben", auf, bg[0], bg[1].group(0))
    secrets = doc_hit(r"Betriebs-\s*oder\s+Geschäftsgeheimnisse\s+sind\s+in\s+den\s+Angeboten\s+daher\s+deutlich\s+zu\s+"
                      r"kennzeichnen(.{0,160}?Hinweis.{0,80}?„([^“”]+)[“”],?\s*(Zf\.\s*\d+))?")
    if secrets:
        add("Geschäftsgeheimnisse", "im Angebot deutlich kennzeichnen" + (
            f" und in „{clean(secrets[1].group(2))}“ {secrets[1].group(3)} darauf hinweisen" if secrets[1].group(1) else ""),
            auf, secrets[0], secrets[1].group(0))
    prec = doc_hit(r"Soweit\s+sich\s+aus\s+den\s+Vergabeunterlagen\s+Abweichungen.{0,160}?An-?\s*gaben\s+in\s+der\s+"
                   r"Auffor-?\s*derung\s+zur\s+Angebotsabgabe\s+Vorrang")
    if prec:
        add("Rangfolge", "bei Widersprüchen in den Vergabeunterlagen gilt die Aufforderung zur Angebotsabgabe",
            auf, prec[0], prec[1].group(0))
    tool = page_hit(r"(?:Hierzu\s+gehören\s+für\s+Unternehmen\s+(?:der|die)\s+)?Angebots-Assistent\w*\s*\(AnA\)")
    if tool and form_text and re.search(r"evergabe-online", form_text, re.I):
        add("Werkzeug", "Angebots-Assistent (AnA) der e-Vergabe-Plattform", tool[0], None, tool[1].group(0))
    return items
