using System.Globalization;
using Marketplace.Data;

namespace Marketplace.Documents;

/// <param name="Party">"bidder", "partner" or "both".</param>
/// <param name="Signature">qes (qualified e-signature), text (Textform via the e-procurement platform) or handwritten.</param>
public record Signer(string Party, string Name, string Signature);

/// <summary>
/// One drafted document, in the languages it is needed in. Bodies are Markdown. Polish and German document names are
/// legal terms of art and stay untranslated in the titles.
/// </summary>
public record AgreementDocument(
    string Id, string Kind, Dictionary<string, string> Title, Dictionary<string, string> Body, List<Signer> Signers,
    string Purpose, string SubmitTo, bool SubmitWithBid);

/// <summary>
/// Drafts the partner paperwork from the agreed deal terms only. Everything is a draft for human review (rule 5: humans sign),
/// labelled as AI-generated in the UI. For a German tender the bidder needs a subcontract plus the German declarations.
/// </summary>
public static class AgreementDrafter
{
    private static readonly CultureInfo Pl = CultureInfo.GetCultureInfo("pl-PL");
    private static readonly CultureInfo De = CultureInfo.GetCultureInfo("de-DE");

    public static List<AgreementDocument> Draft(Deal deal, Tender tender, Organization bidder, Organization partner, DateOnly today)
    {
        var qty = deal.Quantity ?? 0;
        var price = deal.UnitPrice ?? 0;
        var value = qty * price;
        var delivery = deal.Delivery ?? today;
        var lot = tender.Lots.FirstOrDefault(l => l.Id == deal.LotId);
        var lotQty = (int)(lot?.Quantity ?? deal.Gap);
        var share = lotQty == 0 ? 0 : Math.Round(100m * qty / lotQty, 1);
        var place = lot?.Place ?? "";
        var reference = tender.Reference ?? tender.SourceId;
        string P(decimal d) => d.ToString("N2", Pl);
        string D(decimal d) => d.ToString("N2", De);
        string PlDate(DateOnly d) => d.ToString("d MMMM yyyy", Pl);
        string DeDate(DateOnly d) => d.ToString("d. MMMM yyyy", De);
        var bidderSig = $"{bidder.Name}, {bidder.City}";
        var partnerSig = $"{partner.Name}, {partner.City}";
        var draftPl = "> **Projekt wygenerowany przez AI. Wymaga weryfikacji prawnej przed podpisaniem.**";
        var draftDe = "> **KI-generierter Entwurf. Vor der Unterzeichnung rechtlich prüfen.**";
        var autonomy = deal.Autonomous
            ? $"Warunki handlowe uzgodnione automatycznie przez agentów obu stron w granicach zatwierdzonych mandatów ({deal.ClosedAt:yyyy-MM-dd HH:mm} UTC)."
            : $"Warunki handlowe uzgodnione przez agentów i zatwierdzone przez: {deal.ApprovedBy}.";
        var autonomyDe = deal.Autonomous
            ? $"Die kaufmännischen Bedingungen wurden von den Agenten beider Seiten innerhalb der freigegebenen Mandate automatisch vereinbart ({deal.ClosedAt:yyyy-MM-dd HH:mm} UTC)."
            : $"Die kaufmännischen Bedingungen wurden von den Agenten vereinbart und freigegeben durch: {deal.ApprovedBy}.";

        var subcontractPl = $"""
            {draftPl}

            # Umowa podwykonawcza (warunkowa)

            zawarta w dniu {PlDate(today)} pomiędzy:

            **{bidder.Name}**, {bidder.City}, Polska (dalej: „Wykonawca”), a
            **{partner.Name}**, {partner.City}, Czechy (dalej: „Podwykonawca”).

            ## § 1. Przedmiot
            1. Wykonawca zamierza złożyć ofertę w postępowaniu **{reference}** prowadzonym przez **{tender.BuyerName}** ({tender.BuyerCity}, Niemcy), ogłoszenie TED {tender.SourceId}, część **{deal.LotId}: {deal.LotTitle}** ({lotQty} {deal.Unit}).
            2. Podwykonawca zobowiązuje się dostarczyć Wykonawcy **{qty} kompletów** ({share}% ilości części {deal.LotId}) zgodnie z „Material- und Leistungsliste” zamawiającego.

            ## § 2. Wynagrodzenie
            Cena jednostkowa: **{P(price)} EUR** netto za komplet. Wartość umowy: **{P(value)} EUR** netto. Ceny stałe do końca realizacji.

            ## § 3. Termin i miejsce dostawy
            Dostawa do **{PlDate(delivery)}**, DDP {place} (Niemcy), zgodnie z adresami z załącznika 2 do Material- und Leistungsliste.

            ## § 4. Warunek
            Umowa wchodzi w życie z chwilą udzielenia Wykonawcy zamówienia w części {deal.LotId}. W razie nieudzielenia zamówienia umowa wygasa bez roszczeń stron.

            ## § 5. Jakość i zgodność
            1. Podwykonawca utrzymuje certyfikaty AQAP 2110 i ISO 9001 przez cały okres realizacji i przyjmuje wymagania jakościowe zamawiającego (flow-down).
            2. Podwykonawca oświadcza, że nie podlega zakazowi z art. 5k rozporządzenia Rady (UE) nr 833/2014 i składa oświadczenie, o którym mowa w zał. 4.

            ## § 6. Poufność i dane
            Strony wymieniły wyłącznie warunki handlowe (cena, ilość, termin). Dokumenty i limity negocjacyjne stron nie zostały udostępnione.

            ## § 7. Postanowienia końcowe
            Prawo właściwe: prawo polskie. Spory: sąd właściwy dla siedziby Wykonawcy. {autonomy}

            | Za Wykonawcę | Za Podwykonawcę |
            |---|---|
            | {bidderSig} | {partnerSig} |
            | kwalifikowany podpis elektroniczny | kwalifikowany podpis elektroniczny |
            """;

        var subcontractDe = $"""
            {draftDe}

            # Nachunternehmervertrag (aufschiebend bedingt)

            geschlossen am {DeDate(today)} zwischen

            **{bidder.Name}**, {bidder.City}, Polen („Bieter“), und
            **{partner.Name}**, {partner.City}, Tschechien („Nachunternehmer“).

            ## § 1 Gegenstand
            1. Der Bieter beabsichtigt, im Vergabeverfahren **{reference}** des **{tender.BuyerName}** ({tender.BuyerCity}), TED-Bekanntmachung {tender.SourceId}, ein Angebot für **{deal.LotId}: {deal.LotTitle}** ({lotQty} {deal.Unit}) abzugeben.
            2. Der Nachunternehmer liefert dem Bieter **{qty} Sätze** ({D(share)} % der Menge von {deal.LotId}) gemäß der Material- und Leistungsliste des Auftraggebers.

            ## § 2 Vergütung
            Stückpreis **{D(price)} EUR** netto je Satz, Gesamtwert **{D(value)} EUR** netto. Festpreise bis zum Ende der Ausführung.

            ## § 3 Lieferung
            Lieferung bis **{DeDate(delivery)}**, DDP {place}, gemäß Anlage 2 (Verzeichnis der Empfängeranschriften).

            ## § 4 Bedingung
            Der Vertrag wird wirksam mit Zuschlag an den Bieter für {deal.LotId}. Erhält der Bieter keinen Zuschlag, entfällt der Vertrag ohne Ansprüche.

            ## § 5 Qualität und Compliance
            AQAP 2110 und ISO 9001 bleiben während der Ausführung gültig; die Qualitätsanforderungen des Auftraggebers gelten auch für den Nachunternehmer. Der Nachunternehmer erklärt, nicht unter Art. 5k der Verordnung (EU) Nr. 833/2014 zu fallen.

            ## § 6 Schlussbestimmungen
            Es gilt polnisches Recht. {autonomyDe}

            | Für den Bieter | Für den Nachunternehmer |
            |---|---|
            | {bidderSig} | {partnerSig} |
            | qualifizierte elektronische Signatur | qualifizierte elektronische Signatur |
            """;

        var commitmentDe = $"""
            {draftDe}

            # Verpflichtungserklärung des Nachunternehmers

            Vergabeverfahren: **{reference}**, {tender.BuyerName}, TED {tender.SourceId}, {deal.LotId}

            Wir, **{partner.Name}**, {partner.City}, verpflichten uns gegenüber dem Auftraggeber, dem Bieter **{bidder.Name}** im Falle der Zuschlagserteilung
            die folgenden Mittel zur Verfügung zu stellen: Herstellung und Lieferung von **{qty} Sätzen {deal.LotTitle}** bis {DeDate(delivery)}.

            Uns sind die Eignungsanforderungen bekannt. Ausschlussgründe nach §§ 123, 124 GWB liegen nicht vor.

            Ort, Datum: {partner.City}, {DeDate(today)}

            Unterschrift: {partnerSig} (Textform / qualifizierte elektronische Signatur)
            """;

        var commitmentPl = $"""
            {draftPl}

            # Zobowiązanie podmiotu udostępniającego zasoby (Verpflichtungserklärung)

            Postępowanie: **{reference}**, {tender.BuyerName}, TED {tender.SourceId}, część {deal.LotId}

            My, **{partner.Name}**, {partner.City}, zobowiązujemy się wobec zamawiającego oddać do dyspozycji wykonawcy **{bidder.Name}**,
            w razie udzielenia mu zamówienia, zasoby niezbędne do wyprodukowania i dostarczenia **{qty} kompletów** do {PlDate(delivery)}.

            Znane są nam warunki udziału w postępowaniu. Nie zachodzą wobec nas podstawy wykluczenia z §§ 123, 124 GWB.

            Miejscowość, data: {partner.City}, {PlDate(today)}

            Podpis: {partnerSig}
            """;

        var listDe = $"""
            {draftDe}

            # Unterauftragnehmerverzeichnis

            Bieter: **{bidder.Name}**, {bidder.City} · Verfahren **{reference}** · TED {tender.SourceId}

            | Los | Leistungsteil | Menge | Anteil | Nachunternehmer | Sitz |
            |---|---|---|---|---|---|
            | {deal.LotId} | {deal.LotTitle} | {qty} {deal.Unit} | {D(share)} % | {partner.Name} | {partner.City}, CZ |

            Die übrigen Leistungen erbringt der Bieter selbst. Die Verpflichtungserklärung des Nachunternehmers liegt bei.
            """;

        var listPl = $"""
            {draftPl}

            # Wykaz podwykonawców (Unterauftragnehmerverzeichnis)

            Wykonawca: **{bidder.Name}**, {bidder.City} · Postępowanie **{reference}** · TED {tender.SourceId}

            | Część | Zakres | Ilość | Udział | Podwykonawca | Siedziba |
            |---|---|---|---|---|---|
            | {deal.LotId} | {deal.LotTitle} | {qty} {deal.Unit} | {share}% | {partner.Name} | {partner.City}, CZ |

            Pozostały zakres wykonawca realizuje samodzielnie.
            """;

        var declarationDe = $"""
            {draftDe}

            # Eigenerklärung des Bieters

            Verfahren **{reference}**, {tender.BuyerName}

            1. Ausschlussgründe nach §§ 123, 124 GWB liegen bei **{bidder.Name}** nicht vor.
            2. **Russland-Sanktionen:** Weder der Bieter noch ein Nachunternehmer oder Lieferant mit mehr als 10 % des Auftragswerts fällt unter Art. 5k der Verordnung (EU) Nr. 833/2014.
            3. **Wettbewerbsregister:** Der Bieter hat seinen Sitz in Polen und ist im Wettbewerbsregister nicht erfasst. Als gleichwertige Nachweise werden beigefügt: Informacja z Krajowego Rejestru Karnego (Polnisches Strafregister) und Odpis z Krajowego Rejestru Sądowego (Handelsregister), jeweils mit beglaubigter Übersetzung.
            4. Für {deal.LotId} wird ein Nachunternehmer eingesetzt (siehe Unterauftragnehmerverzeichnis).

            {bidderSig}, {DeDate(today)} (Textform über die e-Vergabe-Plattform)
            """;

        var declarationPl = $"""
            {draftPl}

            # Oświadczenia wykonawcy (Eigenerklärung)

            Postępowanie **{reference}**, {tender.BuyerName}

            1. Wobec **{bidder.Name}** nie zachodzą podstawy wykluczenia z §§ 123, 124 GWB.
            2. **Sankcje wobec Rosji:** ani wykonawca, ani podwykonawca lub dostawca o udziale powyżej 10% wartości zamówienia nie podlega art. 5k rozporządzenia (UE) nr 833/2014.
            3. **Wettbewerbsregister:** wykonawca ma siedzibę w Polsce i nie figuruje w niemieckim rejestrze. Jako dokumenty równoważne załączamy informację z KRK oraz odpis z KRS, z tłumaczeniem przysięgłym na język niemiecki.
            4. W części {deal.LotId} wykonawca korzysta z podwykonawcy (zob. wykaz podwykonawców).

            {bidderSig}, {PlDate(today)}
            """;

        var bidderName = bidder.Name;
        var partnerName = partner.Name;
        return
        [
            new("subcontract", "subcontract",
                new() { ["pl"] = "Umowa podwykonawcza", ["de"] = "Nachunternehmervertrag" },
                new() { ["pl"] = Clean(subcontractPl), ["de"] = Clean(subcontractDe) },
                [new("bidder", bidderName, "qes"), new("partner", partnerName, "qes")],
                "Binds the partner to deliver if you win. Not submitted with the bid.", "Between the two companies", false),
            new("commitment", "commitment",
                new() { ["de"] = "Verpflichtungserklärung", ["pl"] = "Zobowiązanie podmiotu udostępniającego zasoby" },
                new() { ["de"] = Clean(commitmentDe), ["pl"] = Clean(commitmentPl) },
                [new("partner", partnerName, "text")],
                "The partner's commitment to the buyer that it will deliver its share.", "With the bid, via evergabe-online.de", true),
            new("subcontractor-list", "subcontractor-list",
                new() { ["de"] = "Unterauftragnehmerverzeichnis", ["pl"] = "Wykaz podwykonawców" },
                new() { ["de"] = Clean(listDe), ["pl"] = Clean(listPl) },
                [new("bidder", bidderName, "text")],
                "Tells the buyer which part is subcontracted and to whom.", "With the bid, via evergabe-online.de", true),
            new("declaration", "declaration",
                new() { ["de"] = "Eigenerklärung des Bieters", ["pl"] = "Oświadczenia wykonawcy" },
                new() { ["de"] = Clean(declarationDe), ["pl"] = Clean(declarationPl) },
                [new("bidder", bidderName, "text")],
                "Exclusion grounds, EU sanctions (Art. 5k) and the Wettbewerbsregister equivalent.", "With the bid, via evergabe-online.de", true),
        ];
    }

    /// <summary>Raw string literals keep their indentation relative to the closing quotes; this trims blank edges.</summary>
    private static string Clean(string s) => s.Trim() + "\n";
}
