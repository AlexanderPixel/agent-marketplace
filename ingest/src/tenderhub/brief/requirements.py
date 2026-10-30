"""Brief section: requirements — conditions the supplier and the products must meet.

Item: {"category", "requirement", "cite"} (see docs/brief-format.md). One concrete sentence per requirement, the ones a
bidder could fail on first, grouped by category in CATEGORIES order.

Works from the standard German federal / Bundeswehr procurement templates (BAIUDBw / BAAINBw on e-Vergabe):
Rahmenvereinbarung (RV) draft, Leistungsbeschreibung, Preis- und Produktblatt per Los, TS-QMA, ZVB/BMVg, BMI
Staatenliste, Eigenerklärungen (B-V 034, RUS-Sanktionen), KdB Lieferantenhandbuch / Anforderungen Katalogdaten,
Information E-Rechnung, Angebotsdatenblatt, Hinweisblätter, Leistungsverzeichnis. Every value in a sentence
(numbers, places, lists) is read from the matched text; a rule whose text is missing simply yields nothing.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from . import Doc, TenderContext, cite

CATEGORIES = ("eligibility", "product", "quality", "delivery", "catalogue", "invoicing", "legal", "other")

# attachment filename patterns (newest version wins)
RV = r"Entwurf|Rahmenvereinbarung|Rahmenvertrag"
LB = r"Leistungsbeschreibung"
PPB = r"Preis-?\s*und\s*Produktblatt"
TSQMA = r"TS-?QMA"
ZVB = r"ZVB"
STAATEN = r"Staatenliste"
RUS = r"RUS|Russland|Sanktion"
EXCL = r"B-?V\s*0?34|Ausschlussgr"
LHB = r"Lieferantenhandbuch"
KAT = r"Anforderung\w*\s*Katalogdaten"
ERECH = r"e-?Rechnung"
ADB = r"Angebotsdatenblatt"
HINWEIS = r"Hinweisblatt"
LV = r"Leistungsverzeichnis.*\.pdf$|Material-\s*und\s*Leistungsliste"
AUFF = r"Aufforderung"


# ---------------------------------------------------------------- text helpers

_SPLIT_LETTER = re.compile(r"(^|[\s\-–•])([A-ZÄÖÜ])\n(?=[a-zäöüß])", re.M)


def _clean(chunk: str) -> str:
    """Undo two PDF-extraction artefacts so phrases match as they read in the PDF.

    - text extracted as 2-3 character lines ("Bun\\ndes\\nmin...", ZVB/BMVg Bundesanzeiger print) is joined back;
    - a heading/bullet whose first letter landed on its own line ("- A\\nuftragsnummer") is rejoined.
    """
    lines = chunk.split("\n")
    if len(lines) > 40:
        short = sum(1 for ln in lines if len(ln) <= 4)
        if short > 0.6 * len(lines):
            chunk = "".join(lines)
    return _SPLIT_LETTER.sub(r"\1\2", chunk)


def _rx(pattern: str) -> re.Pattern:
    """Pattern where a literal space matches any whitespace (incl. none: PDF text splits and joins words)."""
    return re.compile(pattern.replace(" ", r"\s*"), re.I | re.S)


def _pages(doc: Doc) -> Iterator[tuple[int | None, str]]:
    for page, chunk in doc.page_texts():
        yield page, _clean(chunk)


def _docs(ctx: TenderContext, name_rx: str) -> list[Doc]:
    """Latest version of each matching attachment, newest version first."""
    return sorted(ctx.docs_matching(name_rx), key=lambda d: -d.version)


def _search(ctx: TenderContext, name_rx: str, pattern: str | re.Pattern) -> tuple[Doc, int | None, re.Match] | None:
    rx = _rx(pattern) if isinstance(pattern, str) else pattern
    for doc in _docs(ctx, name_rx):
        for page, text in _pages(doc):
            m = rx.search(text)
            if m:
                return doc, page, m
    return None


def _num(s: str) -> str:
    return " ".join(s.split())


def _item(category: str, requirement: str, doc: Doc | str, page: int | None, quote: str) -> dict:
    return {"category": category, "requirement": requirement, "cite": cite(doc, page, quote)}


# ---------------------------------------------------------------- simple rules: one regex -> one sentence

@dataclass(frozen=True)
class Rule:
    category: str
    docs: str                                   # filename regex
    pattern: str                                # see _rx(); named groups feed the sentence
    text: str | Callable[[re.Match], str | None]
    skip_if: tuple[str, str] | None = None      # (filename regex, pattern): another rule already covers this


def _fmt(template: str) -> Callable[[re.Match], str]:
    return lambda m: template.format(**{k: _num(v) for k, v in m.groupdict().items() if v is not None})


RULES: tuple[Rule, ...] = (
    # ---- eligibility
    Rule("eligibility", EXCL,
         r"Eigenerklärung zum Nichtvorliegen zwingender und f ?akultativer Ausschlussgründe gemäß §§ ?123, ?124 GWB",
         "Keine zwingenden oder fakultativen Ausschlussgründe nach §§ 123, 124 GWB (u. a. Verurteilungen wegen "
         "Bestechung, Betrug, Geldwäsche, Steuer-/Sozialabgabenrückstände, Insolvenz) – Eigenerklärung erforderlich"),
    Rule("eligibility", AUFF,
         r"Aktueller Berufs- oder Handelsregisterauszug in Kopie \(nicht älter als (?P<n>\d+ Monate), gerechnet ab "
         r"Angebotsfrist\)",
         "Eintragung im Berufs- oder Handelsregister: aktueller Auszug (nicht älter als {n}, gerechnet ab "
         "Angebotsfrist) mit dem Angebot"),
    Rule("eligibility", AUFF,
         r"auf\s+Ressourcen von Dritten / Nachunternehmern zurückgreifen will, sind die\s+geforderten Bietererklärungen "
         r"und Eignungsnachweise ebenfalls von den Drit-? ?ten / Nachunternehmern vorzulegen.{0,300}?"
         r"\(Verpflichtungserklärung\)",
         lambda m: ("Eignungsleihe: Dritte/Nachunternehmer"
                    + (" (auch konzernverbundene Unternehmen)"
                       if _rx(r"Konzernverbundene Unternehmen sind Dritte").search(m.string) else "")
                    + " legen dieselben Bietererklärungen und Eignungsnachweise vor, dazu eine unterschriebene "
                      "Verpflichtungserklärung")),
    # ---- product
    Rule("product", RV,
         r"mit dem Angebot eine Konformitätsbescheinigung vor, die ergibt, dass alle Geräte eine CE ?-Zulassung aufweisen",
         "Alle Produkte müssen CE-gekennzeichnet (MDR) und im EU-Binnenmarkt verkehrsfähig sein; "
         "Konformitätsbescheinigung (CE) bereits mit dem Angebot vorlegen"),
    Rule("product", AUFF,
         r"Nachweis über die Konformität des Produkts mit den Vorgaben der MDR (?P<mdr>\d{4}/\d{3})",
         "Nachweis der Produktkonformität mit der MDR (EU) {mdr} ist dem Angebot beizufügen",
         skip_if=(RV, r"mit dem Angebot eine Konformitätsbescheinigung vor")),
    Rule("product", TSQMA,
         r"Dem Lieferumfang liegt eine für MDR Produkte / IVDR Produkte gültige EU ?- ?Konformitätserklä ?-?\s*rung bei",
         "Jeder Lieferung liegt die gültige EU-Konformitätserklärung (MDR/IVDR) bei; eine gesetzliche UDI muss auf "
         "dem Produkt angebracht sein"),
    Rule("product", PPB,
         r"Abweichungen zum Richtfabrikat sollen vom Bieter beschrieben.{0,200}?Musterprodukte anzufordern",
         "Richtfabrikat „oder gleichwertig“: Abweichungen des angebotenen Produkts sind zu beschreiben und in ihrer "
         "Wirkung auf die Zweckbestimmung zu bewerten; der AG kann Muster anfordern"),
    Rule("product", PPB,
         r"Sofern die geforderte Höchstmenge aufgrund der verfügbaren Packungsgrößen nicht exakt geliefert werden kann, "
         r"ist auf die nächstgrößere auslieferbare Höchstmenge aufzurunden",
         "Preise je kleinstmöglicher Einheit (z. B. Stück) angeben; passt die Menge nicht zu den Packungsgrößen, "
         "auf die nächstgrößere lieferbare Menge aufrunden"),
    Rule("product", PPB,
         r"Angebotspreis je 1 Einheit \((?P<a>Vertragsjahr [^)]*?) ?\)\s*in Euro ohne Ust\. mit zwei "
         r"Nachkommastellen\s*Angebotspreis je 1 Einheit \((?P<b>Vertragsjahr [^)]*?) ?\)",
         "Je Los zwei Nettopreise je Einheit in Euro (zwei Nachkommastellen) angeben: {a} sowie {b}"),
    Rule("product", RV,
         r"Abweichungen von den in der Leistungsbeschreibung\s+aufgestellten\s+Leistungsanforderungen sind zulässig, "
         r"wenn.{0,250}?die abweichenden Produkte dürfen nur nach ausdrücklicher\s+Zustimmung des AG geliefert werden",
         "Abweichungen vom vereinbarten Produkt nur als technische Weiterentwicklung ohne Einbußen bei Funktion und "
         "Sicherheit; vorab anzeigen und erst nach ausdrücklicher Zustimmung des AG liefern"),
    Rule("product", TSQMA,
         r"Falls der Hersteller sich in einem Land befindet, wo die Verordnungen.{0,80}?nicht gelten, so ist zu prüfen, "
         r"dass ein Bevollmächtigter, der innerhalb der EU seinen\s+Wohnsitz haben muss",
         "Hersteller außerhalb des Geltungsbereichs der MDR/IVDR: ein Bevollmächtigter mit Sitz in der EU muss die "
         "rechtliche Verantwortung übernehmen"),
    Rule("product", LB,
         r"Versandpackungen\)?, sind\s+mit Artikelname gemäß Auftragsnummer, Anzahl der enthaltenen Packungen",
         "Verpackung rechtskonform und handelsüblich kennzeichnen; Versandpackungen mit Artikelname gemäß "
         "Auftragsnummer und Anzahl der enthaltenen Packungen beschriften und transportsicher verpacken"),
    Rule("product", ZVB,
         r"Die Liefergegenstände sind vom Auftragnehmer gemäß den Vorgaben der Technischen Lieferbedingungen "
         r"(?P<tl>[A-Z]-\d{4}) Teil (?P<teil>\d+) zu kennzeichnen",
         "Liefergegenstände nach Technischer Lieferbedingung TL {tl} Teil {teil} kennzeichnen"),
    Rule("product", ADB,
         r"Für jeden Artikel ist ein separates Angebotsdatenblatt auszufüllen",
         lambda m: None),  # built by _datasheet()
    # ---- quality
    Rule("quality", RV,
         r"Qualitätsmanagementsystem nach (?P<qm>DIN EN ISO \d+ oder DIN ISO \d+) oder vergleichbar.{0,80}?"
         r"nachzuweisen, die Zertifizierung über den gesamten Leistungszeitraum aufrechtzuhalten",
         "Zertifiziertes QM-System nach {qm} (oder vergleichbar) nachweisen und über die gesamte Laufzeit "
         "aufrechterhalten"),
    Rule("quality", AUFF,
         r"Nachweis über die Konformität mit den Anforderungen nach (?P<iso>EN ISO \d+)",
         "Konformitätsnachweis {iso} bereits mit dem Angebot einreichen"),
    Rule("quality", TSQMA,
         r"Medizinprodukt Klasse 3 und 2b, invasiv\s*•\s*(?P<a>DIN EN ISO \d+)\s*•\s*(?P<b>DIN EN ISO \d+)\s*•\s*"
         r"(?P<c>DIN EN ISO \d+)",
         "Hersteller: QM-System nach {a}; für invasive Produkte der Klassen IIb/III zusätzlich {b} "
         "(Risikomanagement) und {c} (Biokompatibilität)"),
    Rule("quality", TSQMA,
         r"Ein Qualitätsmanagementsystem nach (?P<cfr>21 CFR \d+) wird durch die Bundeswehr\s+anerkannt",
         "Ein Hersteller-QM-System nach {cfr} (US FDA) wird von der Bundeswehr anerkannt"),
    Rule("quality", TSQMA,
         r"Händler haben zu prüfen, dass für das jeweilige Medizinprodukt eine EU ?- ?Konformitätserklä ?-?\s*rung "
         r"vorliegt und ein CE Kennzeichen angebracht ist.{0,500}?Die Lagerungs- und Transportbedingungen des "
         r"jeweiligen Medizinproduktes müssen den Vor ?-?\s*gaben des Herstellers entsprechen",
         "Händler prüfen je Produkt CE-Kennzeichen, EU-Konformitätserklärung und eine Hersteller-Qualitätssicherung "
         "mindestens nach TS-QMA; Lagerung und Transport nach Herstellervorgaben"),
    Rule("quality", LV,
         r"Prüfart: (?P<p>[^\n]+)\nQS-Anforderungen: (?P<qs>[^\n]+)",
         lambda m: f"Qualitätssicherung nach {_num(m['qs'])}; Prüfart: {_num(m['p'])}"),
    Rule("quality", TSQMA,
         r"Vor Auslieferung von Medizinprodukten haben Händler zu prüfen, dass keine Risikoinformatio ?-?\s*nen, "
         r"Produktrückrufe",
         "Händler müssen vor jeder Auslieferung Rückrufe und Risikoinformationen (Hersteller, BfArM, PEI) prüfen; "
         "betroffene Produkte dürfen nicht geliefert werden"),
    Rule("quality", TSQMA,
         r"Das Zutrittsrecht zu allen Einrichtungen, in denen die vertraglich vereinbarten Arbeiten\s+durchgeführt werden",
         "Beauftragte des AG erhalten Zutritts-, Prüf- und Auditrechte beim AN und seinen Unterlieferanten "
         "(inkl. Einsicht in Medizinprodukteakte, Risikomanagement und CAPA)"),
    Rule("quality", TSQMA,
         r"Die oberste Leitung des Auftragnehmers ernennt.{0,80}?Manage ?-?\s*mentbeauftragte\(n\) für amtliche "
         r"Qualitätssicherungsangelegenheiten",
         "Eine/n Managementbeauftragte/n für amtliche Qualitätssicherung benennen (direkt der Geschäftsleitung "
         "unterstellt; bei kleinen Unternehmen die Geschäftsführung)"),
    Rule("quality", TSQMA,
         r"hat der Auf ?-?\s*tragnehmer alle schwerwiegenden Vorkommnisse und Si ?cherheitskorrekturmaßnahmen an\s+"
         r"die zuständigen Behörden zu melden.{0,400}?(?P<mail>[\w.-]+@[Bb]undeswehr\.org)",
         "Schwerwiegende Vorkommnisse und Sicherheitskorrekturmaßnahmen an die zuständigen Behörden melden "
         "(bei Bedarf zusätzlich an {mail})"),
    Rule("quality", RV,
         r"Vermerks auf allen Ausfertigungen des Lieferscheins.{0,80}?(?P<v>„Wir bes ?tätigen.{0,300}?“)",
         "Alle Lieferscheinausfertigungen mit dem datierten und unterschriebenen Prüf- und Konformitätsvermerk "
         "des AN versehen (Wortlaut in der RV)"),
    # ---- delivery
    Rule("delivery", RV,
         r"innerhalb\s+von (?P<t>\d+ bis \d+) ?Werktagen nach Zugang des Einzelauftrages",
         "Lieferung innerhalb von {t} Werktagen nach Zugang des Einzelauftrags; absehbare Verzögerungen frühzeitig "
         "mit Begründung anzeigen"),
    Rule("delivery", LB,
         r"außerhalb gesetzlicher Feiertage\s+von (?P<d1>Montag bis Donnerstag) zwischen (?P<t1>[\d:]+ und [\d:]+) "
         r"und (?P<d2>Freitag) zwischen (?P<t2>[\d:]+ und [\d:]+)(?: zu erfolgen)?\.?\s*Die Lieferung muss „(?P<inc>[^“]+)“",
         lambda m: (f"Anlieferung „{_num(m['inc'])}“, {_num(m['d1'])} {_num(m['t1']).replace(' und ', '–')} Uhr und "
                    f"{_num(m['d2'])} {_num(m['t2']).replace(' und ', '–')} Uhr (nicht an gesetzlichen Feiertagen)")),
    Rule("delivery", RV,
         r"Die Lieferklausel für die Vertragsgegenstände lautet: (?P<a>[A-Z]{3}) - Incoterms® ?(?P<y>\d{4}).{0,200}?"
         r"\((?P<wa>Inländischer Auftragnehmer)\).{0,60}?(?P<b>[A-Z]{3}) - Incoterms ?® ?\d{4}.{0,80}?"
         r"\((?P<wb>Ausländischer Auftragnehmer)\)(?P<extra> zzgl\. Entladen)?",
         lambda m: (f"Lieferklausel {m['a']} Incoterms® {m['y']} (inländischer AN) bzw. {m['b']} Incoterms® {m['y']}"
                    f"{' zzgl. Entladen' if m['extra'] else ''} (ausländischer AN)")),
    Rule("delivery", HINWEIS,
         r"(?:Die 1\. Abrufmenge wird im (?P<a>[^;]{3,40}?) abgerufen; die 2\. Abrufmenge im (?P<b>[^.;]{3,30}?\d{4})"
         r"\.\s*)?(?:Die\s+jeweilige\s+Mindestabnahmemenge wird\s+in einem Abruf erfolgen; )?"
         r"die\s+geforderte Menge kann in\s+Teillieferungen erfolgen, die\s+vollständige Menge muss\s+jedoch bis zum "
         r"jeweiligen\s+Lieferdatum vollständig\s+erfolgt sein",
         lambda m: ((f"Mindestabnahmemenge in zwei Abrufen (1. Abruf im {_num(m['a'])}, 2. Abruf im {_num(m['b'])}); "
                     if m["a"] else "")
                    + "jede Abrufmenge wird in einem Abruf bestellt; Teillieferungen sind zulässig, die volle Menge muss "
                      "aber bis zum Liefertermin geliefert sein")),
    Rule("delivery", LB,
         r"Lieferschein mit folgenden Angaben zu erstellen:\s*(?P<f>(?:-\s*[^\n]+\n?){2,8}?)\s*Der Lieferschein ist in "
         r"die Versandpackung",
         lambda m: ("Lieferschein mit " + ", ".join(_num(x) for x in re.findall(r"-\s*([^\n]+)", m["f"]))
                    + " oben in die Versandpackung legen oder im Packlistenbeutel außen befestigen")),
    Rule("delivery", RV,
         r"Hierzu ist neben (?P<a>[^()\n]{3,40}?) \( ?E ?-Mail: (?P<m1>[\w.-]+@[\w.-]+)\) auch das (?P<b>[\w-]{3,20}) "
         r"\((?P<m2>[\w.-]+@[\w.-]+) bzw\. (?P<m3>[\w.-]+@[\w.-]+)\) über die geplante Auslieferung schriftlich zu "
         r"informieren",
         "Geplante Auslieferungen schriftlich ankündigen: {a} ({m1}) und {b} ({m2} bzw. {m3})"),
    Rule("delivery", RV,
         r"spätestens (?P<n>\d+) Tage vor\s+dem vereinbarten Liefertermin abstimmen",
         "Spätestens {n} Tage vor dem Liefertermin die Übernahme mit dem Empfänger abstimmen"),
    Rule("delivery", TSQMA,
         r"Bei Produkten mit begrenzter Lagerz ?eit muss durch den Händler eine Kontrolle des Verfallda ?-?\s*tums "
         r"erfolgen",
         "Bei verfalldatierten Produkten die verbleibende Laufzeit vor jeder Auslieferung ermitteln und dem AG "
         "mitteilen"),
    Rule("delivery", RV,
         r"innerhalb von (?P<d>\d+) Kalendertagen unaufgefordert bei Erreichen\s+von (?P<a>\d+ ?%) sowie "
         r"(?P<b>\d+ ?%) der jeweiligen Höchstmenge",
         "AG unaufgefordert informieren, wenn {a} und {b} der Höchstmenge abgerufen sind (binnen {d} Kalendertagen), "
         "sowie unverzüglich bei 100 %"),
    # ---- catalogue
    Rule("catalogue", RV,
         r"(?:unter der E ?-Mail-Adresse\s+(?P<mail>[\w.-]+@[\w.-]+)\s+)?unverzüglich nach\s+Abschluss der RV Daten "
         r"zur Erstellung elektronischer Bestellkataloge",
         lambda m: ("Unverzüglich nach Vertragsschluss Katalogdaten für den elektronischen Bestellkatalog (Kaufhaus des "
                    "Bundes) liefern" + (f" (an {m['mail']})" if m["mail"] else "")
                    + " und bei Vertragsänderungen pflegen")),
    Rule("catalogue", LHB,
         r"Die Katalogdaten müssen im Format (?P<f>BMECat [\d.]+) bereitgestellt werden",
         lambda m: f"Katalogdaten im Format {_num(m['f']).replace('BMECat', 'BMEcat')} liefern (alternativ über die "
                   f"Excel-Vorlage)"),
    Rule("catalogue", LHB,
         r"Jeder Artikel muss auf der letzten Ebene \(Produktgruppe\) eingeordnet werden\. Die Klassifizierung\s+"
         r"ist zwingend notwendig.{0,300}?eCl@ss in der Version (?P<v>\d+\.\d+)",
         "Jeder Artikel muss zwingend nach eCl@ss {v} auf Produktgruppenebene (8-stellig) klassifiziert werden"),
    # ---- invoicing
    Rule("invoicing", RV,
         r"Rechnungen, die dieser Form nicht genügen.{0,120}?gelten als nicht gestellt",
         "Rechnungen ausschließlich als E-Rechnung nach ERechV; nicht konforme Rechnungen gelten als nicht gestellt "
         "(kein Zahlungsverzug)"),
    Rule("invoicing", ZVB,
         r"In ihr sind unter anderem die (?P<f>Vertragsnummer, Leitweg- ?Identifikationsnummer, Lieferantennummer "
         r"und SAP-Kontraktnummer) anzugeben",
         lambda m: (f"Pflichtangaben in der E-Rechnung: {re.sub(r'Leitweg-\s*Identifikationsnummer', 'Leitweg-ID', _num(m['f']))}; "
                    f"Lieferschein mit Empfangsvermerk beifügen")),
    Rule("invoicing", ERECH,
         r"die OZG-RE die alleinige\s+Eingangsrechnungsplattform des Bundes",
         lambda m: None),  # built by _einvoice()
    Rule("invoicing", RV,
         r"Es wird kein Skonto gewährt\. Zahlungen werden innerhalb von (?P<d>\d+) Kalendertagen nach\s+Eingang",
         "Zahlung binnen {d} Kalendertagen nach Eingang von Rechnung, Lieferschein und Leistungsbestätigung; "
         "kein Skonto"),
    # ---- legal
    Rule("legal", RV,
         r"Betriebshaftpflichtversicherung\s+mit einem im europäischen Wirtschaftsraum zugelassenen Unternehmen in "
         r"Höhe von\s+•\s*(?P<p>[\d,.]+ Millionen €) für Personenschäden und\s+•\s*(?P<s>[\d,.]+ Millionen €) für "
         r"Sach- und Vermögenschäden",
         "Betriebshaftpflichtversicherung (Versicherer im EWR) über {p} für Personen- und {s} für Sach- und "
         "Vermögensschäden, Nachweis auf Anforderung"),
    Rule("legal", RV,
         r"Verzug, beträgt die Vertragsstrafe für jeden Werktag des Verzugs (?P<d>[\d,]+ ?%) des Netto-\s*"
         r"Abrechnungspreises\. Die Gesamtsumme der zu zahlenden Vertragsstrafen\s+ist auf (?P<cap>[\d,]+ ?%)"
         r"(?: des Netto-\s*Abrechnungspreises beschränkt\. Die Vertragsstrafe\s+wird ab der (?P<start>[^.]{3,40}?) "
         r"nach Ablauf des vereinbarten\s+Leistungstermins berechnet)?",
         lambda m: (f"Vertragsstrafe bei Lieferverzug {_num(m['d'])} des Netto-Abrechnungspreises je Werktag, insgesamt "
                    f"max. {_num(m['cap'])}"
                    + (f"; berechnet ab der {_num(m['start'])} nach dem Liefertermin" if m["start"] else ""))),
    Rule("legal", RV,
         r"Nicht als höhere Gewalt anzusehen sind insbesondere (?P<x>[^.]{10,250}?)\.\s",
         lambda m: f"Keine höhere Gewalt (Lieferpflicht bleibt): {_num(m['x'])}"),
    Rule("legal", RV,
         r"Der Besteller ist berechtigt, einen Einzelauftrag im Vorfeld der Leistungserbringung \(bis\s+zu "
         r"(?P<w>\w+ Wochen) vor dem vereinbarten Leistungstermin\) zurückzunehmen\. Ansprüche\s+des AN",
         "Der Besteller darf Einzelaufträge bis {w} vor dem Liefertermin ohne Ansprüche des AN zurücknehmen"),
    Rule("legal", RV,
         r"endet mit Ausschöpfung der Höchstmenge.{0,80}?spätestens jedoch (?P<n>\d+ Jahre?) nach\s+Inkrafttreten"
         r".{0,700}?einmal um (?P<opt>\d+ Jahre?) \(bis maximal (?P<max>\d+ Jahre?)\) verlängert werden\. Die "
         r"Verlängerungsoption ist spätestens\s+(?P<f>\w+ Monate) vor Ablauf",
         "Laufzeit bis zur Ausschöpfung der Höchstmenge, spätestens {n}; der AG kann einmal um {opt} verlängern "
         "(max. {max}; Option spätestens {f} vor Ablauf, keine Pflicht zur Ausübung)"),
    Rule("legal", RV,
         r"Die Höhe der Vertragsstrafe beträgt für jeden Verstoß (?P<p>[\d,]+ ?%) der\s+Abrechnungssumme",
         "Vertragsstrafe {p} der Netto-Abrechnungssumme je Verstoß gegen militärische Sicherheit, Vertraulichkeit, "
         "Datenschutz oder Geheimschutz"),
    Rule("legal", ZVB,
         r"Hardcore-Kartellrechtsverstöße.{0,80}?hat er (?P<a>\d+ ?%) der Netto-Abrechnungssumme.{0,300}?"
         r"In allen anderen Kartellrechtsver-?stößen hat der Auftragnehmer (?P<b>\d+ ?%) der Netto-Abrechnungssumme",
         "Pauschalierter Schadensersatz bei Kartellverstößen: {a} (Hardcore-Kartell) bzw. {b} der "
         "Netto-Abrechnungssumme"),
    Rule("legal", RV,
         r"Eine Verpflichtung des AG, Einzelaufträge in einem bestimmten Umfang zu erteilen,\s+besteht durch diese RV "
         r"nicht",
         "Keine Abnahmepflicht des AG über die Mindestabnahmemengen hinaus; die Höchstmenge ist nicht garantiert"),
    Rule("legal", RV,
         r"In den Preisangaben sind sämtliche\s+Leistungen \((?P<inc>[^)]+)\) enthalten",
         lambda m: (f"Die Preise im Preis- und Produktblatt decken alle Leistungen ab "
                    f"({_num(m['inc']).replace('/', ', ')}); Preisanpassung nur bei gesetzlichen/behördlichen oder "
                    f"nachweisbaren unvorhersehbaren Preisänderungen auf Hersteller- oder Großhandelsebene")),
    Rule("legal", RV,
         r"Die Verjährungsfrist für Mängelansprüche für die Produkte dieser RV beträgt (?P<n>\d+ Monate)",
         "Gewährleistung (Verjährung von Mängelansprüchen) {n} ab Lieferung; Nacherfüllungskosten trägt der AN"),
    Rule("legal", RV,
         r"mit einer Frist von (?P<f>\w+ Monaten zum Monatsende)\s+ordentlich gekündigt",
         "RV jederzeit mit {f} ordentlich kündbar (ganz oder teilweise)"),
    Rule("legal", ZVB,
         r"Der Auftragnehmer zeigt dem Auftraggeber jeden Unterauftrag sowie jeden Wechsel eines "
         r"Unterauftragnehmers.{0,200}?unverzüglich und unaufge-?for ?dert in Textform an",
         "Jeden Unterauftrag und Wechsel eines Unterauftragnehmers unverzüglich in Textform anzeigen; "
         "Eignungsanforderungen gelten auch für Unterauftragnehmer"),
    Rule("legal", ZVB,
         r"Allgemeine Geschäftsbedingungen des Auftragnehmers sind nicht Vertragsbestandteil",
         "Eigene AGB des Bieters werden nicht Vertragsbestandteil"),
    Rule("legal", ZVB,
         r"Vertragssprache ist Deutsch\..{0,300}?Schriftverkehr mit dem Auftraggeber, einschließlich Erklärungen und "
         r"Nachweise, hat in deutscher Sprache zu erfolgen",
         "Vertrags- und Korrespondenzsprache Deutsch (auch für Erklärungen und Nachweise)"),
    Rule("legal", ZVB,
         r"Auf den Vertrag findet deutsches Recht Anwendung\. ?36\.2 Die Anwendung des Übereinkommens der vereinten "
         r"Nationen über Verträge über den internationalen Waren-?kauf \(UN-Kaufrecht\) ist ausgeschlossen",
         "Deutsches Recht, UN-Kaufrecht ausgeschlossen"),
    Rule("legal", RV,
         r"Ausschließlicher Gerichtsstand ist (?P<o>[A-ZÄÖÜ][\wäöüß-]+)",
         "Ausschließlicher Gerichtsstand {o}"),
    # ---- other
    Rule("other", RV,
         r"benennt er für den\s+Zeitraum von (?P<d>Montag bis Freitag), jeweils von (?P<t>[\d:]+ Uhr bis [\d:]+ Uhr), "
         r"eine/n feste/n",
         "Feste/n, ständig erreichbare/n Ansprechpartner/in plus Vertretung benennen ({d}, {t})"),
    Rule("other", AUFF,
         r"Soweit sich aus den Vergabeunterlagen Abweichungen zu den in dieser Auffor-? ?derung zur Angebotsabgabe "
         r"festgelegten Bedingungen ergeben, haben die An-? ?gaben in der Aufforderung zur Angebotsabgabe Vorrang",
         "Bei Widersprüchen zwischen den Vergabeunterlagen gehen die Bedingungen der Aufforderung zur Angebotsabgabe "
         "vor"),
)


def _apply(ctx: TenderContext, rule: Rule) -> dict | None:
    if rule.skip_if and _search(ctx, *rule.skip_if):
        return None
    hit = _search(ctx, rule.docs, rule.pattern)
    if not hit:
        return None
    doc, page, m = hit
    fmt = rule.text if callable(rule.text) else _fmt(rule.text)
    text = fmt(m)
    return _item(rule.category, text, doc, page, m.group(0)) if text else None


# ---------------------------------------------------------------- composite rules

def _russia(ctx: TenderContext) -> list[dict]:
    """Eigenerklärung RUS-Sanktionen: Art. 5k VO (EU) 833/2014, incl. the subcontractor share threshold."""
    hit = _search(ctx, RUS, r"Artikel (?P<art>5k)\)? Absatz 1 der Verordnung \(EU\) Nr\. ?(?P<vo>833/2014)")
    if not hit:
        return []
    doc, page, m = hit
    texts = [t for _, t in _pages(doc)]
    share = next((s for t in texts for s in _rx(r"mehr\s+als (\d+ ?%) des Auftragswerts").findall(t)), None)
    owner = next((s for t in texts for s in _rx(r"Anteilen im Umfang von mehr als (\d+ ?%)").findall(t)), None)
    stake = f", über {_num(owner)} russische Beteiligung" if owner else ", russische Beteiligung"
    sub = (f"; das gilt auch für Unterauftragnehmer/Lieferanten mit mehr als {_num(share)} des Auftragswerts, "
           f"während der gesamten Vertragslaufzeit") if share else ""
    return [_item("eligibility", f"Kein Russland-Bezug nach Art. {m['art']} VO (EU) {m['vo']} (russische "
                  f"Staatsangehörigkeit/Niederlassung{stake}, Handeln in deren Auftrag){sub}",
                  doc, page, m.group(0))]


_STATE = re.compile(r"^\s*\d{1,2}\.\s+(?P<short>[A-ZÄÖÜ][^\n(,.]*?)\s*(?:\((?P<long>[^)\n]+)\))?[ \t]*[,.]?[ \t]*$"
                    r"(?P<extra>(?:\n(?!\s*\d{1,2}\.\s)[^\n]*)*)", re.M)
# short names that denote more than one state: keep the official long form ("Korea" = DVR Korea, not Republic of Korea)
_AMBIGUOUS_STATE = re.compile(r"^(Korea|Kongo|Guinea)$")


def _states(text: str) -> list[str]:
    out = []
    for m in _STATE.finditer(text):
        name = _num(m["short"])
        if m["long"] and _AMBIGUOUS_STATE.match(name):
            name = f"{name} ({_num(m['long'])})"
        regions = re.findall(r"einschließlich\s+(?:Sonderverwaltungsregion\s*\(SVR\)\s*)?([A-ZÄÖÜ][\wäöüß-]+)",
                             m["extra"] or "")
        if regions:
            name += f" (inkl. {', '.join(regions)})"
        out.append(name)
    return out


def _state_list(ctx: TenderContext) -> list[dict]:
    """BMI Staatenliste (§ 13 Abs. 1 Nr. 17 SÜG): no staff from these states on deliveries into Bundeswehr sites."""
    pat = (r"dürfen\s+daher seitens des Auftragnehmers nicht mit\s+Anlieferungen beauftragt werden, die einen Zutritt "
           r"zu\s+Liegenschaften der Bundeswehr\s+erforderlich machen")
    hit = _search(ctx, STAATEN, pat) or _search(ctx, LB, pat)
    if not hit:
        return []
    doc, page, m = hit
    states: list[str] = []
    for d in _docs(ctx, STAATEN):
        for _, text in _pages(d):
            if _rx(r"Staatenliste").search(text):
                states += _states(text)
        if states:
            break
    tail = f" – {len(states)} Staaten: {', '.join(states)}" if states else ""
    return [_item("eligibility", "Kein Personal (auch von Subunternehmen) aus Staaten der BMI-Staatenliste "
                  f"(§ 13 Abs. 1 Nr. 17 SÜG) bei Anlieferungen in Bundeswehr-Liegenschaften{tail}",
                  doc, page, m.group(0))]


_LOT_FILE = re.compile(r"LOS\s*(\d+)", re.I)
_PACK = re.compile(r"(\d[\d.]*)\s+(St(?:ü|ue)ck|Paar|Set|Flaschen?|Rollen?|Beutel)\s+pro\s*Packung", re.I)
_SHELF = re.compile(r"(\d+)\s+Monate")
_USE = re.compile(r"Gebrauch:\s*([^\n]+)")
_ROW_END = re.compile(r"Ort,\s*Datum|Richtfabrikat\s*-\s*oder\s*gleichwertig\s*=")


def _lot_rows(ctx: TenderContext) -> list[tuple[str, Doc, int | None, str]]:
    """(lot, doc, page, row text) for each Preis- und Produktblatt; the row starts at the line "<lot> <Title>"
    (after the filled-in example row) and ends at the signature block / legend."""
    rows = []
    for doc in _docs(ctx, PPB):
        lm = _LOT_FILE.search(doc.filename)
        if not lm:
            continue
        lot = str(int(lm.group(1)))
        for page, text in _pages(doc):
            start = re.search(rf"^\s*0?{lot}\s+[A-ZÄÖÜ]", text, re.M)
            if not start:
                continue
            end = _ROW_END.search(text, start.end())
            rows.append((lot, doc, page, text[start.start(): end.start() if end else len(text)]))
            break
    return sorted(rows, key=lambda r: int(r[0]))


def _lots(ctx: TenderContext) -> list[dict]:
    """Per lot: sterility/single use (merged when equal), max pieces per pack and minimum shelf life on receipt."""
    rows = _lot_rows(ctx)
    items: list[dict] = []
    uses: dict[str, list[tuple[str, Doc, int | None, str]]] = {}
    for lot, doc, page, row in rows:
        um = _USE.search(row)
        if um:
            uses.setdefault(_num(um.group(1)).rstrip("."), []).append((lot, doc, page, um.group(0)))
    for use, hits in uses.items():
        lots = ", ".join(h[0] for h in hits)
        label = "Los" if len(hits) == 1 else "Lose"
        _, doc, page, quote = hits[0]
        items.append(_item("product", f"Gebrauch: {use} ({label} {lots})", doc, page, quote))
    for lot, doc, page, row in rows:
        pm = _PACK.search(row)
        if not pm:
            continue
        sm = _SHELF.search(row, pm.end())
        if sm and not row[pm.end():sm.start()].strip():
            text = (f"Los {lot}: max. {pm.group(1)} {pm.group(2)} pro Packung; Verfallsdatum muss bei Wareneingang "
                    f"bei der Bundeswehr noch mind. {sm.group(1)} Monate Resthaltbarkeit bieten")
            quote = row[pm.start(): sm.end()]
        else:
            text = f"Los {lot}: max. {pm.group(1)} {pm.group(2)} pro Packung (keine Mindesthaltbarkeit angegeben)"
            quote = pm.group(0)
        items.append(_item("product", text, doc, page, quote))
    return items


def _datasheet(ctx: TenderContext) -> list[dict]:
    """Angebotsdatenblatt Medizinprodukte: which product data the bidder must supply per article."""
    doc = next(iter(_docs(ctx, ADB)), None)
    if not doc:
        return []
    text = "\n".join(t for _, t in _pages(doc))
    m = _rx(r"Für jeden Artikel ist ein separates Angebotsdatenblatt auszufüllen").search(text)
    if not m:
        return []
    page = next((p for p, t in _pages(doc) if m.group(0) in t), None)
    fields = [label for label, rx in (
        ("CE-Kennzeichen", r"CE-Kennzeichen"), ("Risikoklasse (MDR)", r"Risikoklasse"),
        ("Sterilität", r"Steriles Produkt"), ("Verfalldatum ab Wareneingang", r"Verfalldatum ab Wareneingang"),
        ("PZN", r"Pharmazentralnummer"), ("EAN/UPC", r"EAN\s*/\s*UPC"),
        ("Gewicht und Maße je Grund-, Sammel- und Versandpackung", r"Länge\s*[xX]\s*Breite"),
    ) if re.search(rx, text)]
    detail = f" ({', '.join(fields)})" if fields else ""
    return [_item("product", f"Für jeden Artikel ein separates Angebotsdatenblatt Medizinprodukt mit "
                  f"Produkt- und Verpackungsdaten ausfüllen{detail}", doc, page, m.group(0))]


def _catalogue_limits(ctx: TenderContext) -> list[dict]:
    """Anforderungen an Katalogdaten (KdB): field length limits and image rules."""
    doc = next(iter(_docs(ctx, KAT)), None)
    if not doc:
        return []
    found: dict[str, tuple[int | None, re.Match]] = {}
    for key, pat in (("short", r"Länge des Artikelnamens auf (\d[\d.]*) Zeichen begrenzt"),
                     ("long", r"Länge der Artikellangbeschreibung ist auf (\d[\d.]*) Zeichen begrenzt"),
                     ("kw", r"Schlagworte.{0,900}?Länge auf (\d[\d.]*) Zeichen begrenzt"),
                     ("img", r"Auflösung von mindestens (\d+ ?x ?\d+)"),
                     ("placeholder", r"keine Alternativabbildungen bzw\. Platzhalter")):
        for page, text in _pages(doc):
            m = _rx(pat).search(text)
            if m:
                found[key] = (page, m)
                break
    if not found:
        return []
    parts = []
    if "short" in found:
        parts.append(f"Kurzbezeichnung max. {found['short'][1].group(1)} Zeichen")
    if "long" in found:
        parts.append(f"Langbeschreibung max. {found['long'][1].group(1)} Zeichen")
    if "kw" in found:
        parts.append(f"Schlagworte max. {found['kw'][1].group(1)} Zeichen")
    if "img" in found:
        parts.append(f"Produktbilder mind. {_num(found['img'][1].group(1))} Pixel")
    if "placeholder" in found:
        parts.append("keine Platzhalter- oder Logobilder")
    page, m = next(iter(found.values()))
    return [_item("catalogue", "Katalogdaten: " + ", ".join(parts), doc, page, m.group(0))]


def _delivery_places(ctx: TenderContext) -> list[dict]:
    """Lieferorte from the Leistungsbeschreibung: '<Depot> Übernahme: <Lager> / <Street> / <PLZ> <City>'."""
    rx = re.compile(r"Übernahme:\s*\n\s*(?P<site>[^\n]+?)\s*\n[^\n]+\n\s*(?P<plz>\d{5})\s+(?P<city>[^\n]+?)\s*$", re.M)
    for doc in _docs(ctx, LB):
        for page, text in _pages(doc):
            sites = list(rx.finditer(text))
            if not sites:
                continue
            places = "; ".join(f"{_num(s['site'])} ({s['plz']} {_num(s['city'])})" for s in sites)
            more = ("; der AG kann weitere inländische Lieferorte benennen"
                    if _rx(r"behält sich vor weitere inländische Lieferorte zu benennen").search(text) else "")
            return [_item("delivery", f"Lieferorte: {places}{more}", doc, page,
                          text[sites[0].start(): sites[-1].end()])]
    return []


def _einvoice(ctx: TenderContext) -> list[dict]:
    """Information zur E-Rechnung: standard and the federal invoice platform."""
    plat = _search(ctx, ERECH, r"die (?P<p>OZG-RE) die alleinige\s+Eingangsrechnungsplattform des Bundes")
    std = _search(ctx, ERECH, r"Standard (?P<s>XRechnung) ist eine nationale Ausgestaltung der Europäischen Norm "
                              r"(?P<en>EN \d+)")
    if not plat:
        return []
    doc, page, m = plat
    fmt = f" im Standard {std[2]['s']} ({_num(std[2]['en'])})" if std else ""
    return [_item("invoicing", f"E-Rechnungen{fmt} über die {m['p']} einreichen (alleinige Eingangsplattform des "
                  f"Bundes, einmalige Registrierung erforderlich)", doc, page, m.group(0))]


def _spec_mismatch(ctx: TenderContext) -> list[dict]:
    """Flag a general Leistungsbeschreibung written for Arzneimittel when the tender buys Medizinprodukte."""
    hit = _search(ctx, LB, r"Leistungsbeschreibung\s+allgemeiner Teil\s+Arzneimittel")
    if not hit:
        return []
    is_md = bool(_docs(ctx, ADB)) or bool(_search(ctx, RV, r"CE ?-Zulassung")) or bool(_search(ctx, PPB, r"steril"))
    if not is_md:
        return []
    doc, page, m = hit
    amg = _search(ctx, LB, r"Arzneimittelgesetzes\s*\(AMG\)")
    leaflet = _search(ctx, LB, r"papiergebundene Gebrauchsinformation")
    extra = [x for x, h in (("AMG-Konformität", amg), ("Packungsbeilage", leaflet)) if h]
    detail = f" ({', '.join(extra)})" if extra else ""
    return [_item("other", f"Die Leistungsbeschreibung (allgemeiner Teil) ist für Arzneimittel formuliert{detail}, "
                  f"obwohl Medizinprodukte beschafft werden – Geltung per Bieterfrage klären", doc, page, m.group(0))]


_LV_ROW_DATE = re.compile(r"^C\)\s*\d{5}\b[^\n]*?\b(\d{2}\.\d{2}\.(\d{4}))\b", re.M)


def _date_mismatch(ctx: TenderContext) -> list[dict]:
    """Flag a Leistungsverzeichnis whose required delivery date (column "Lt, gef") lies before the first call-off
    announced in the Hinweisblatt — the bidder must know which date binds."""
    abruf = _search(ctx, HINWEIS, r"Die 1\. Abrufmenge wird im (?P<a>[^;]{3,40}?) abgerufen")
    if not abruf:
        return []
    first_year = re.search(r"\d{4}", abruf[2]["a"])
    for doc in _docs(ctx, LV):
        for page, text in _pages(doc):
            if not _rx(r"Lt, gef").search(text):
                continue
            m = _LV_ROW_DATE.search(text)
            if not (m and first_year and int(m.group(2)) < int(first_year.group(0))):
                continue
            dates = sorted({d for _, t in _pages(doc) for d, _ in _LV_ROW_DATE.findall(t)})
            return [_item("other", f"Leistungsverzeichnis nennt als geforderten Liefertermin {', '.join(dates)}, laut "
                          f"Hinweisblatt erfolgt der 1. Abruf erst im {_num(abruf[2]['a'])} – verbindlichen Termin per "
                          f"Bieterfrage klären", doc, page, m.group(0))]
    return []


COMPOSITES: dict[str, Callable[[TenderContext], list[dict]]] = {
    "eligibility": lambda ctx: _russia(ctx) + _state_list(ctx),
    "product": lambda ctx: _lots(ctx) + _datasheet(ctx),
    "delivery": _delivery_places,
    "catalogue": _catalogue_limits,
    "invoicing": _einvoice,
    "other": lambda ctx: _spec_mismatch(ctx) + _date_mismatch(ctx),
}
# where composite items go within their category: before (True) or after (False) the simple rules
_COMPOSITE_FIRST = {"eligibility": False, "product": True, "delivery": False, "catalogue": False,
                    "invoicing": False, "other": False}


def extract(ctx: TenderContext) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for cat in CATEGORIES:
        simple = [it for rule in RULES if rule.category == cat and (it := _apply(ctx, rule))]
        comp = COMPOSITES[cat](ctx) if cat in COMPOSITES else []
        for it in (comp + simple) if _COMPOSITE_FIRST.get(cat) else (simple + comp):
            key = it["requirement"].lower()
            if key not in seen:
                seen.add(key)
                items.append(it)
    return items
