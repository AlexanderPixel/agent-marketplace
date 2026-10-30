using System.Globalization;
using Marketplace.Activity;
using Marketplace.Data;
using Marketplace.Tenancy;
using Marketplace.Telegram;
using Microsoft.EntityFrameworkCore;
using static Marketplace.Telegram.TelegramBot;

namespace Marketplace.Compliance;

/// <summary>
/// Supplier changes for Buy-European: the compliance agent drafts the order for the new supplier, the termination for
/// the current one and the reason, then asks a person to sign (Telegram or the app). Only "Sign" changes the BOM (rule 5).
/// Nothing is sent to either supplier by the platform: the person sends the signed documents.
/// </summary>
public class SupplierChanges(AppDb db, OrgContext org, ActivityLog log, TimeProvider clock, TelegramBot bot)
{
    private const string Agent = "Compliance agent";

    public async Task<SupplierChange?> PrepareAsync(string componentId, string supplier, CancellationToken ct = default)
    {
        var orgId = org.Require();
        var bom = await db.Boms.FirstOrDefaultAsync(ct);
        var component = bom?.Components.FirstOrDefault(c => c.Id == componentId);
        var to = component?.Alternatives.FirstOrDefault(a => a.Supplier == supplier);
        if (bom is null || component is null || to is null || BuyEuropean.Sanctioned(to.Supplier)) return null;
        if (component.Change is { Stage: "awaiting" } open) return open;

        var company = await db.Organizations.FirstAsync(o => o.Id == orgId, ct);
        var before = BuyEuropean.EuContent(bom.Components);
        var after = BuyEuropean.EuContent(bom.Components.Select(c => c.Id == component.Id ? c with { Supplier = to.Supplier, Country = to.Country, CostEur = to.CostEur } : c));
        var total = bom.Components.Sum(c => c.CostEur);
        var tx = new ChangeText(company.Language);
        var reason = BuyEuropean.Sanctioned(component.Supplier) ? "sanctions" : "content";
        var facts = new ChangeFacts(company, bom, component, to, before, after, total, DateOnly.FromDateTime(clock.GetUtcNow().UtcDateTime), reason);

        var change = new SupplierChange(Guid.NewGuid(), component.Supplier, component.Country, component.CostEur, to, before, after,
            ChangeDrafter.Draft(tx, facts), "awaiting", clock.GetUtcNow());
        var (message, keyboard) = (Compose(tx, facts), Keyboard(tx, change));
        var messageId = await bot.SendAsync(message, keyboard, ct);
        change = change with { TelegramMessageId = messageId };
        Replace(bom, component.Id, c => c with { Change = change });
        await db.SaveChangesAsync(ct);

        await log.WriteAsync(orgId, Agent,
            $"Drafted a supplier change for {component.Name}: {component.Supplier} ({component.Country}) → {to.Supplier} ({to.Country}); " +
            (messageId is null ? "waiting for a signature in the app" : "sent a sign request to Telegram"),
            (reason == "sanctions"
                ? $"{component.Supplier} is on the EU sanctions list: no orders or payments may go to them. "
                : $"{bom.Product} has {before}% EU content; SAFE/EDIP need {BuyEuropean.Threshold}%. This change brings it to {after}%. ") +
            "Drafted: order for the new supplier, termination for the current one, internal reason. Nothing changes and nothing is sent until a person signs.",
            Trace.Change(change.Id),
            new
            {
                check = new { product = bom.Product, euBefore = before, euAfter = after, threshold = BuyEuropean.Threshold, reason, totalEur = total },
                from = new { supplier = component.Supplier, country = component.Country, costEur = component.CostEur, component = component.Name },
                to = new { supplier = to.Supplier, country = to.Country, costEur = to.CostEur },
                documents = change.Documents.Select(d => d.Title),
                telegram = Trace.Telegram(message, keyboard, messageId).Telegram,
            },
            ct: ct);
        return change;
    }

    /// <summary>A person's Sign / Don't sign. Works across organisations so the Telegram bot can call it.</summary>
    public async Task<string> DecideAsync(Guid changeId, bool sign, string who, CancellationToken ct = default)
    {
        var boms = await db.Boms.IgnoreQueryFilters().ToListAsync(ct);
        var bom = boms.FirstOrDefault(b => b.Components.Any(c => c.Change?.Id == changeId));
        var component = bom?.Components.First(c => c.Change?.Id == changeId);
        if (bom is null || component?.Change is not { } change) return new ChangeText(null)["notFound"];
        org.OrgId = bom.OrgId;
        var tx = new ChangeText((await db.Organizations.FirstAsync(o => o.Id == bom.OrgId, ct)).Language);
        if (change.Stage != "awaiting") return tx["already"];

        var now = clock.GetUtcNow();
        if (sign)
        {
            var old = new BomAlternative(component.Supplier, component.Country, component.CostEur, BuyEuropean.PreviousSupplier);
            Replace(bom, component.Id, c => c with
            {
                Supplier = change.To.Supplier, Country = change.To.Country, CostEur = change.To.CostEur,
                // A sanctioned supplier is not kept as a way back: there is no undo to a listed party.
                Alternatives = c.Alternatives.Any(a => a.Supplier == old.Supplier) || BuyEuropean.Sanctioned(old.Supplier) ? c.Alternatives : [.. c.Alternatives, old],
                Change = change with { Stage = "signed", DecidedBy = who, DecidedAt = now },
            });
            await db.SaveChangesAsync(ct);
            await log.WriteAsync(bom.OrgId, Agent,
                $"{who} signed the supplier change for {component.Name}: now {change.To.Supplier} ({change.To.Country}); EU content {change.EuBefore}% → {BuyEuropean.EuContent(bom.Components)}%",
                "A person decided. The BOM now uses the new supplier. Sending the order and the termination to the suppliers stays with that person.",
                Trace.Change(change.Id), ct: ct);
        }
        else
        {
            Replace(bom, component.Id, c => c with { Change = null });
            await db.SaveChangesAsync(ct);
            await log.WriteAsync(bom.OrgId, Agent, $"{who} declined the supplier change for {component.Name} ({change.To.Supplier})",
                "A person decided not to go ahead. The BOM is unchanged and nothing was sent.", Trace.Change(change.Id), ct: ct);
        }

        if (change.TelegramMessageId is { } messageId)
            await bot.MarkDecidedAsync(messageId, sign ? tx["signedBy", who] : tx["declinedBy", who], ct);
        return sign ? tx["answerSign"] : tx["answerDecline"];
    }

    private static void Replace(BillOfMaterials bom, string id, Func<BomComponent, BomComponent> f) =>
        bom.Components = bom.Components.Select(c => c.Id == id ? f(c) : c).ToList();

    private static object[][] Keyboard(ChangeText tx, SupplierChange change) =>
        [[Button(tx["sign"], $"csign:{change.Id}"), Button(tx["decline"], $"cdecline:{change.Id}")]];

    /// <summary>The sign request: what changes, why, for what, what it costs and what you are signing.</summary>
    private static string Compose(ChangeText tx, ChangeFacts f)
    {
        var docs = ChangeDrafter.Titles(tx, f);
        return string.Join("\n", new List<string>
        {
            $"🔁 <b>{H(tx["title"])}</b>",
            $"<b>{H(tx["product"])}</b>: {H(f.Bom.Product)}",
            $"<b>{H(tx["component"])}</b>: {H(f.Component.Name)}",
            $"{Flag(f.Component.Country)} {H(f.Component.Supplier)} → {Flag(f.To.Country)} <b>{H(f.To.Supplier)}</b>",
            "",
            f.Reason == "sanctions"
                ? $"<b>⛔ {H(tx["why"])}</b>: {H(tx["whySanctions", f.Component.Supplier])}"
                : $"<b>{H(tx["why"])}</b>: {H(tx["whyText", BuyEuropean.Threshold, f.Component.Country])}",
            $"<b>{H(tx["euContent"])}</b>: {tx.Pct(f.Before)} → <b>{tx.Pct(f.After)}</b> ({H(tx["required", BuyEuropean.Threshold])})",
            $"<b>{H(tx["forWhat"])}</b>: {H(f.Reason == "sanctions" ? tx["forWhatSanctions"] : tx["forWhatText", f.Bom.Product])}",
            $"<b>{H(tx["cost"])}</b>: {H(tx.Delta(f.To.CostEur - f.Component.CostEur))} / {H(tx["perSet"])} ({H(tx.DeltaPct(f.To.CostEur - f.Component.CostEur, f.Total))})",
            "",
            $"<b>{H(tx["documents"])}</b>:",
        }.Concat(docs.Select((d, i) => $"{i + 1}. {H(d)}")).Append("").Append($"<i>{H(tx["footer"])}</i>"));
    }

    public static string Flag(string country) =>
        country.Length == 2 ? string.Concat(country.ToUpperInvariant().Select(ch => char.ConvertFromUtf32(0x1F1E6 + ch - 'A'))) : "";
}

public record ChangeFacts(Organization Company, BillOfMaterials Bom, BomComponent Component, BomAlternative To,
    decimal Before, decimal After, decimal Total, DateOnly Today, string Reason);

/// <summary>The three drafts. Markdown, labelled as AI drafts; anything the platform does not know is left as a [placeholder].</summary>
public static class ChangeDrafter
{
    public static List<string> Titles(ChangeText tx, ChangeFacts f) =>
        [tx["docOrder", f.To.Supplier], tx["docTermination", f.Component.Supplier], tx["docMemo"]];

    public static List<ChangeDocument> Draft(ChangeText tx, ChangeFacts f)
    {
        var titles = Titles(tx, f);
        var c = f.Component;
        var delta = f.To.CostEur - c.CostEur;
        var me = $"{f.Company.Name}, {f.Company.City} ({f.Company.Country})";
        return
        [
            new("order", titles[0], f.To.Supplier, tx.Fill("orderBody", new()
            {
                ["buyer"] = me, ["supplier"] = $"{f.To.Supplier} ({f.To.Country})", ["date"] = tx.Date(f.Today),
                ["component"] = c.Name, ["product"] = f.Bom.Product, ["price"] = tx.Eur(f.To.CostEur), ["unit"] = tx["perSet"],
                ["note"] = f.To.Note, ["country"] = f.To.Country, ["t"] = BuyEuropean.Threshold.ToString(),
            })),
            new("termination", titles[1], c.Supplier, tx.Fill(f.Reason == "sanctions" ? "terminationSanctionsBody" : "terminationBody", new()
            {
                ["buyer"] = me, ["supplier"] = $"{c.Supplier} ({c.Country})", ["date"] = tx.Date(f.Today),
                ["component"] = c.Name, ["product"] = f.Bom.Product, ["country"] = c.Country, ["t"] = BuyEuropean.Threshold.ToString(),
            })),
            new("memo", titles[2], f.Company.Name, tx.Fill(f.Reason == "sanctions" ? "memoSanctionsBody" : "memoBody", new()
            {
                ["date"] = tx.Date(f.Today), ["product"] = f.Bom.Product, ["component"] = c.Name,
                ["from"] = $"{c.Supplier} ({c.Country})", ["to"] = $"{f.To.Supplier} ({f.To.Country})",
                ["t"] = BuyEuropean.Threshold.ToString(), ["before"] = tx.Pct(f.Before), ["after"] = tx.Pct(f.After),
                ["share"] = tx.Pct(Math.Round(100 * c.CostEur / f.Total, 1)),
                ["oldPrice"] = tx.Eur(c.CostEur), ["newPrice"] = tx.Eur(f.To.CostEur), ["delta"] = tx.Delta(delta),
                ["deltaPct"] = tx.DeltaPct(delta, f.Total), ["note"] = f.To.Note, ["unit"] = tx["perSet"], ["supplier"] = c.Supplier,
            })),
        ];
    }
}

/// <summary>What the supplier-change flow says, in the company's language. German companies get English for now.</summary>
public sealed class ChangeText(string? language)
{
    public string Language { get; } = language == "pl" ? "pl" : "en";
    private CultureInfo Culture => CultureInfo.GetCultureInfo(Language == "pl" ? "pl-PL" : "en-GB");

    public string this[string key, params object[] args] => string.Format(Culture, Texts[Language][key], args);
    public string Fill(string key, Dictionary<string, string> values) =>
        values.Aggregate(Texts[Language][key], (s, kv) => s.Replace("{" + kv.Key + "}", kv.Value));

    public string Eur(decimal d) => d.ToString("N2", Culture) + " €";
    public string Delta(decimal d) => (d >= 0 ? "+" : "−") + Eur(Math.Abs(d));
    public string Pct(decimal d) => d.ToString("0.#", Culture) + "%";
    public string DeltaPct(decimal d, decimal total) => (d >= 0 ? "+" : "−") + Pct(total == 0 ? 0 : Math.Round(100 * Math.Abs(d) / total, 1)) + " " + this["ofCost"];
    public string Date(DateOnly d) => d.ToString("d MMMM yyyy", Culture);

    private const string DraftPl = "> **Projekt wygenerowany przez AI. Wymaga weryfikacji przed podpisaniem i wysłaniem.**";
    private const string DraftEn = "> **AI-generated draft. Review before signing and sending.**";

    private static readonly Dictionary<string, Dictionary<string, string>> Texts = new()
    {
        ["pl"] = new()
        {
            ["title"] = "Zmiana dostawcy do podpisu", ["product"] = "Produkt", ["component"] = "Komponent",
            ["why"] = "Dlaczego", ["whyText"] = "SAFE i EDIP finansują tylko produkty, w których co najmniej {0}% kosztu komponentów pochodzi z UE, EOG lub Ukrainy. Ten komponent pochodzi z {1}, więc się nie liczy.",
            ["euContent"] = "Udział UE", ["required"] = "wymagane {0}%",
            ["forWhat"] = "Po co", ["forWhatText"] = "aby „{0}” spełnił regułę udziału UE dla zakupów finansowanych z SAFE / EDIP.",
            ["cost"] = "Koszt", ["perSet"] = "kpl.", ["ofCost"] = "kosztu produktu",
            ["documents"] = "Do podpisu (projekty AI)",
            ["docOrder"] = "Zamówienie dla {0}", ["docTermination"] = "Wypowiedzenie dostaw dla {0}", ["docMemo"] = "Uzasadnienie zmiany dostawcy",
            ["footer"] = "„Podpisz” zatwierdza zmianę i dokumenty, a zestawienie materiałowe przechodzi na nowego dostawcę. Dokumenty wysyłasz dostawcom sam(a).",
            ["sign"] = "✅ Podpisz", ["decline"] = "❌ Nie podpisuj",
            ["signedBy"] = "✅ Podpisał(a): {0}", ["declinedBy"] = "❌ Odrzucił(a): {0}",
            ["answerSign"] = "Podpisano. Zestawienie zaktualizowane.", ["answerDecline"] = "Odrzucono. Nic się nie zmienia.",
            ["already"] = "Decyzja już podjęta.", ["notFound"] = "Nie znaleziono.",
            ["whySanctions"] = "{0} jest na unijnej skonsolidowanej liście sankcyjnej (zamrożenie aktywów). Nie wolno składać zamówień, płacić ani udostępniać im żadnych zasobów.",
            ["forWhatSanctions"] = "aby natychmiast przestać kupować od podmiotu objętego sankcjami i zachować ciągłość produkcji z dostawcą z UE. Bez tego produkt nie spełnia warunków SAFE / EDIP.",
            ["terminationSanctionsBody"] = DraftPl + """


                # Wstrzymanie dostaw i płatności – {component}

                - **Od:** {buyer}
                - **Do:** {supplier}
                - **Data:** {date}

                Ze skutkiem natychmiastowym wstrzymujemy wszystkie zamówienia i płatności dotyczące komponentu „{component}”.

                **Powód:** Państwa firma figuruje na unijnej skonsolidowanej liście sankcji finansowych (zamrożenie aktywów). Prawo UE zabrania nam udostępniania Państwu środków lub zasobów gospodarczych, w tym płatności za dostawy.

                Otwarte zamówienia zostają anulowane. Nie przyjmujemy dalszych dostaw.

                [do konsultacji z działem prawnym przed wysłaniem: los zaliczek i otwartych faktur, zgłoszenie do właściwego organu krajowego]

                ---
                Za {buyer}: ____________________
                """,
            ["memoSanctionsBody"] = DraftPl + """


                # Uzasadnienie zmiany dostawcy (sankcje)

                - **Data:** {date}
                - **Produkt:** {product}
                - **Komponent:** {component} ({share} kosztu produktu)
                - **Zmiana:** {from} → {to}

                ## Dlaczego
                {supplier} figuruje na unijnej skonsolidowanej liście sankcji finansowych (zamrożenie aktywów). Nie wolno składać u nich zamówień, płacić im ani udostępniać żadnych zasobów. Dopóki są w zestawieniu materiałowym, produkt nie spełnia warunków SAFE / EDIP.

                ## Efekt
                Komponent pochodzi od dostawcy z UE. Udział UE: {before} → **{after}**.

                ## Koszt
                {oldPrice} → {newPrice} za {unit}: {delta} ({deltaPct}).

                ## Kolejność
                1. Natychmiast: wstrzymanie zamówień i płatności (bez okresu wypowiedzenia).
                2. Podpis tej zmiany i wysłanie zamówienia nowemu dostawcy.
                3. Konsultacja z działem prawnym: otwarte faktury, zaliczki, zgłoszenie do właściwego organu.
                """,
            ["orderBody"] = DraftPl + """


                # Zamówienie – {component}

                - **Zamawiający:** {buyer}
                - **Dostawca:** {supplier}
                - **Data:** {date}

                ## 1. Przedmiot
                Dostawa komponentu „{component}” do produktu „{product}”, według tej samej dokumentacji technicznej (rysunek, specyfikacja materiałowa) co u dotychczasowego dostawcy.

                ## 2. Cena
                {price} netto za {unit}. Warunki dostawcy: {note}.

                ## 3. Pochodzenie
                Dostawca oświadcza, że komponent jest wytwarzany w {country} i dołącza świadectwo pochodzenia do każdej dostawy. Pochodzenie jest istotnym warunkiem zamówienia: Zamawiający wykazuje nim zgodność produktu z regułą Buy-European programów SAFE / EDIP (co najmniej {t}% kosztu komponentów z UE, EOG lub Ukrainy).

                ## 4. Ilości, terminy i płatność
                [do uzupełnienia: prognoza roczna, termin pierwszej dostawy, warunki płatności, Incoterms]

                ## 5. Kwalifikacja
                Seryjne dostawy rozpoczynają się po pozytywnej kwalifikacji próbek (FAI / PPAP) przez Zamawiającego.

                ---
                Za Zamawiającego: ____________________ Za Dostawcę: ____________________
                """,
            ["terminationBody"] = DraftPl + """


                # Wypowiedzenie dostaw – {component}

                - **Od:** {buyer}
                - **Do:** {supplier}
                - **Data:** {date}

                Informujemy, że kończymy zamawianie komponentu „{component}” do produktu „{product}”.

                - **Powód:** produkt musi spełniać regułę Buy-European programów SAFE / EDIP: co najmniej {t}% kosztu komponentów z UE, EOG lub Ukrainy. Komponent dostarczany jest z {country}, więc nie liczy się do tego progu. Zmiana nie wynika z jakości Państwa dostaw.

                - **Termin:** zgodnie z okresem wypowiedzenia w umowie [do uzupełnienia: numer i data umowy, okres wypowiedzenia]. Otwarte zamówienia realizujemy do końca.

                Wypowiedzenie wysyłamy dopiero po pozytywnej kwalifikacji nowego dostawcy.

                ---
                Za {buyer}: ____________________
                """,
            ["memoBody"] = DraftPl + """


                # Uzasadnienie zmiany dostawcy

                - **Data:** {date}
                - **Produkt:** {product}
                - **Komponent:** {component} ({share} kosztu produktu)
                - **Zmiana:** {from} → {to}

                ## Dlaczego
                SAFE i EDIP finansują zakupy obronne tylko wtedy, gdy co najmniej {t}% kosztu komponentów pochodzi z UE, EOG lub Ukrainy. Dziś produkt ma **{before}**, więc się nie kwalifikuje. Ten komponent pochodzi spoza tych państw.

                ## Efekt
                Po zmianie udział UE wynosi **{after}** (wymagane {t}%). To spełnia warunek udziału UE. Pozostałe warunki SAFE / EDIP (kontrola nad firmą, sankcje) są sprawdzane osobno na stronie Buy-European.

                ## Koszt
                {oldPrice} → {newPrice} za {unit}: {delta} ({deltaPct}).

                ## Ryzyka
                - Kwalifikacja nowego dostawcy: {note}.
                - Okres wypowiedzenia u obecnego dostawcy [do sprawdzenia w umowie].

                ## Kolejność
                1. Podpis tej zmiany (zestawienie materiałowe w EU Patriot przechodzi na nowego dostawcę).
                2. Wysłanie zamówienia nowemu dostawcy i kwalifikacja próbek.
                3. Wysłanie wypowiedzenia obecnemu dostawcy.
                """,
        },
        ["en"] = new()
        {
            ["title"] = "Supplier change to sign", ["product"] = "Product", ["component"] = "Component",
            ["why"] = "Why", ["whyText"] = "SAFE and EDIP only fund products where at least {0}% of component cost comes from the EU, EEA or Ukraine. This component comes from {1}, so it does not count.",
            ["euContent"] = "EU content", ["required"] = "{0}% required",
            ["forWhat"] = "For what", ["forWhatText"] = "so that \"{0}\" meets the EU content rule for purchases funded by SAFE / EDIP.",
            ["cost"] = "Cost", ["perSet"] = "set", ["ofCost"] = "of product cost",
            ["documents"] = "To sign (AI drafts)",
            ["docOrder"] = "Order for {0}", ["docTermination"] = "Supply termination for {0}", ["docMemo"] = "Reason for the supplier change",
            ["footer"] = "\"Sign\" approves the change and the documents, and the bill of materials switches to the new supplier. You send the documents to the suppliers.",
            ["sign"] = "✅ Sign", ["decline"] = "❌ Don't sign",
            ["signedBy"] = "✅ Signed by {0}", ["declinedBy"] = "❌ Declined by {0}",
            ["answerSign"] = "Signed. Bill of materials updated.", ["answerDecline"] = "Declined. Nothing changes.",
            ["already"] = "Already decided.", ["notFound"] = "Not found.",
            ["whySanctions"] = "{0} is on the EU consolidated sanctions list (asset freeze). You may not order from them, pay them or make any resources available to them.",
            ["forWhatSanctions"] = "to stop buying from a sanctioned party at once and keep production going with an EU supplier. Until then the product cannot meet SAFE / EDIP.",
            ["terminationSanctionsBody"] = DraftEn + """


                # Suspension of supplies and payments – {component}

                - **From:** {buyer}
                - **To:** {supplier}
                - **Date:** {date}

                With immediate effect we suspend all orders of and payments for "{component}".

                **Reason:** your company is on the EU consolidated list of financial sanctions (asset freeze). EU law prohibits us from making funds or economic resources available to you, including payment for deliveries.

                Open orders are cancelled. We will not accept further deliveries.

                [check with legal before sending: advance payments and open invoices, report to the competent national authority]

                ---
                For {buyer}: ____________________
                """,
            ["memoSanctionsBody"] = DraftEn + """


                # Reason for the supplier change (sanctions)

                - **Date:** {date}
                - **Product:** {product}
                - **Component:** {component} ({share} of product cost)
                - **Change:** {from} → {to}

                ## Why
                {supplier} is on the EU consolidated list of financial sanctions (asset freeze). We may not order from them, pay them or make any resources available to them. While they are in the bill of materials, the product cannot meet SAFE / EDIP.

                ## Effect
                The component comes from an EU supplier. EU content: {before} → **{after}**.

                ## Cost
                {oldPrice} → {newPrice} per {unit}: {delta} ({deltaPct}).

                ## Order of steps
                1. Now: stop orders and payments (no notice period).
                2. Sign this change and send the order to the new supplier.
                3. Check with legal: open invoices, advance payments, report to the competent authority.
                """,
            ["orderBody"] = DraftEn + """


                # Order – {component}

                - **Buyer:** {buyer}
                - **Supplier:** {supplier}
                - **Date:** {date}

                ## 1. Subject
                Supply of "{component}" for "{product}", to the same technical documentation (drawing, material specification) as the current supplier.

                ## 2. Price
                {price} net per {unit}. Supplier terms: {note}.

                ## 3. Origin
                The supplier declares the component is made in {country} and includes a certificate of origin with every delivery. Origin is an essential term: the buyer uses it to show the product meets the SAFE / EDIP Buy-European rule (at least {t}% of component cost from the EU, EEA or Ukraine).

                ## 4. Quantities, dates and payment
                [to complete: annual forecast, first delivery date, payment terms, Incoterms]

                ## 5. Qualification
                Series deliveries start after the buyer approves samples (FAI / PPAP).

                ---
                For the buyer: ____________________ For the supplier: ____________________
                """,
            ["terminationBody"] = DraftEn + """


                # Supply termination – {component}

                - **From:** {buyer}
                - **To:** {supplier}
                - **Date:** {date}

                We are ending orders of "{component}" for "{product}".

                - **Reason:** the product must meet the SAFE / EDIP Buy-European rule: at least {t}% of component cost from the EU, EEA or Ukraine. This component ships from {country}, so it does not count towards that threshold. The change is not about the quality of your deliveries.

                - **Timing:** per the notice period in our contract [to complete: contract number and date, notice period]. Open orders will be completed.

                We send this termination only after the new supplier is qualified.

                ---
                For {buyer}: ____________________
                """,
            ["memoBody"] = DraftEn + """


                # Reason for the supplier change

                - **Date:** {date}
                - **Product:** {product}
                - **Component:** {component} ({share} of product cost)
                - **Change:** {from} → {to}

                ## Why
                SAFE and EDIP fund defence purchases only if at least {t}% of component cost comes from the EU, EEA or Ukraine. Today the product is at **{before}**, so it does not qualify. This component comes from outside those countries.

                ## Effect
                After the change, EU content is **{after}** ({t}% required). That meets the EU content rule. The other SAFE / EDIP conditions (company control, sanctions) are checked separately on the Buy-European page.

                ## Cost
                {oldPrice} → {newPrice} per {unit}: {delta} ({deltaPct}).

                ## Risks
                - New supplier qualification: {note}.
                - Notice period with the current supplier [check the contract].

                ## Order of steps
                1. Sign this change (the bill of materials in EU Patriot switches to the new supplier).
                2. Send the order to the new supplier and qualify samples.
                3. Send the termination to the current supplier.
                """,
        },
    };
}
