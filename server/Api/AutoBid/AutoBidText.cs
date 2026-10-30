using System.Globalization;

namespace Marketplace.AutoBid;

/// <summary>
/// Everything the auto-bid agent and its Telegram bot say to a person, in the company's profile language
/// (Polish, English or German), with numbers, dates and units formatted the same way.
/// </summary>
public sealed class AutoBidText
{
    private static readonly Dictionary<string, Dictionary<string, string>> Texts = new()
    {
        ["pl"] = new()
        {
            ["ready"] = "Do podpisu", ["deadline"] = "Termin", ["daysLeft"] = "zostało {0} dni",
            ["part"] = "Twoja część", ["lot"] = "część {0}", ["youMake"] = "Ty: {0}, partner: {1}",
            ["partner"] = "Partner", ["delivery"] = "dostawa {0}",
            ["closed"] = "Uzgodnione przez agentów w Twoich limitach. Projekty dokumentów: {0} (PL, DE).",
            ["approve"] = "Poniżej Twojego progu autonomii: „Podpisz” zatwierdza też tę umowę.",
            ["requirements"] = "Wymagania", ["reqMet"] = "spełnione", ["reqEquivalent"] = "spełnione, w tym {0} dokumentem równoważnym",
            ["summary"] = "W skrócie", ["details"] = "Szczegóły", ["documents"] = "Dokumenty",
            ["footer"] = "„Podpisz” zatwierdza ofertę. Podpis kwalifikowany i złożenie oferty pozostają po Twojej stronie.",
            ["sign"] = "✅ Podpisz", ["decline"] = "❌ Nie podpisuj", ["notice"] = "Ogłoszenie w TED", ["open"] = "Otwórz w EU Patriot",
            ["signedBy"] = "✅ Zatwierdził(a): {0}", ["declinedBy"] = "❌ Odrzucił(a): {0}",
            ["answerSign"] = "Zapisano: możesz podpisać.", ["answerDecline"] = "Zapisano: bez podpisu.",
            ["already"] = "Decyzja już podjęta.", ["notAllowed"] = "Niedozwolone z tego czatu.", ["notFound"] = "Nie znaleziono.",
            ["stopped"] = "⏸ Automatyczna oferta zatrzymana", ["test"] = "✅ EU Patriot połączony. Prośby o podpis będą przychodzić na ten czat.",
            ["connected"] = "Połączono. Prośby o podpis z EU Patriot będą przychodzić tutaj.\nID czatu: <code>{0}</code>",
            ["otherChat"] = "Ten bot jest połączony z innym czatem.",
            ["noteQualifies"] = "Spełniasz warunki i masz moce produkcyjne.", ["noteGap"] = "Spełniasz warunki; brakuje {0} na {1}.",
            ["noteSearching"] = "Szukam partnera na {0}.", ["noteNegotiating"] = "Negocjuję z {0} w Twoich limitach.",
            ["noteDrafting"] = "Umowa zamknięta. Przygotowuję dokumenty.", ["noteReady"] = "Gotowe do podpisu. Czeka na Twoją decyzję.",
            ["noteReadyApprove"] = "Gotowe. Umowa z partnerem jest poniżej progu autonomii, więc „Podpisz” ją też zatwierdzi.",
            ["noteNoPartner"] = "Żaden partner nie spełnił warunków obowiązkowych, więc oferty nie da się skompletować.",
            ["noteNoDeal"] = "Agenci nie doszli do porozumienia w limitach.", ["noteError"] = "Coś poszło nie tak podczas przygotowania oferty; nic nie zostało wysłane.",
            ["noteSigned"] = "Zatwierdził(a): {0}. Dalej: podpisy kwalifikowane i złożenie oferty na {1} (robi to człowiek).",
            ["noteDeclined"] = "Odrzucił(a): {0}. Nic nie zostanie podpisane ani złożone.", ["portal"] = "platformie zamawiającego",
        },
        ["en"] = new()
        {
            ["ready"] = "Ready to sign", ["deadline"] = "Deadline", ["daysLeft"] = "{0} days left",
            ["part"] = "Your part", ["lot"] = "lot {0}", ["youMake"] = "you: {0}, partner: {1}",
            ["partner"] = "Partner", ["delivery"] = "delivery {0}",
            ["closed"] = "Agreed by the agents inside your limits. Draft documents: {0} (PL, DE).",
            ["approve"] = "Below your autonomy threshold: Sign also approves this deal.",
            ["requirements"] = "Requirements", ["reqMet"] = "met", ["reqEquivalent"] = "met, {0} with an equivalent document",
            ["summary"] = "In short", ["details"] = "Details", ["documents"] = "Documents",
            ["footer"] = "Sign approves the bid. Qualified e-signatures and the submission stay with you.",
            ["sign"] = "✅ Sign", ["decline"] = "❌ Don't sign", ["notice"] = "Notice on TED", ["open"] = "Open in EU Patriot",
            ["signedBy"] = "✅ Signed off by {0}", ["declinedBy"] = "❌ Declined by {0}",
            ["answerSign"] = "Recorded: go ahead and sign.", ["answerDecline"] = "Recorded: not signing.",
            ["already"] = "Already decided.", ["notAllowed"] = "Not allowed from this chat.", ["notFound"] = "Not found.",
            ["stopped"] = "⏸ Auto-bid stopped", ["test"] = "✅ EU Patriot is connected. Sign requests will arrive in this chat.",
            ["connected"] = "Connected. Sign requests from EU Patriot will arrive here.\nChat id: <code>{0}</code>",
            ["otherChat"] = "This bot is linked to another chat.",
            ["noteQualifies"] = "Qualifies and has the capacity.", ["noteGap"] = "Qualifies; short by {0} on {1}.",
            ["noteSearching"] = "Looking for a partner for {0}.", ["noteNegotiating"] = "Negotiating with {0} inside your limits.",
            ["noteDrafting"] = "Deal closed. Drafting the documents.", ["noteReady"] = "Ready to sign. Waiting for your decision.",
            ["noteReadyApprove"] = "Ready. The partner deal is below your autonomy threshold, so Sign also approves it.",
            ["noteNoPartner"] = "No partner passed the hard gates, so the bid cannot be completed.",
            ["noteNoDeal"] = "The agents found no deal inside the limits.", ["noteError"] = "Something went wrong while preparing the bid; nothing was sent.",
            ["noteSigned"] = "Approved by {0}. Next: qualified e-signatures and submission on {1}, done by a person.",
            ["noteDeclined"] = "Declined by {0}. Nothing will be signed or submitted.", ["portal"] = "the buyer's portal",
        },
        ["de"] = new()
        {
            ["ready"] = "Zur Unterschrift", ["deadline"] = "Frist", ["daysLeft"] = "noch {0} Tage",
            ["part"] = "Ihr Anteil", ["lot"] = "Los {0}", ["youMake"] = "Sie: {0}, Partner: {1}",
            ["partner"] = "Partner", ["delivery"] = "Lieferung {0}",
            ["closed"] = "Von den Agenten in Ihrem Rahmen vereinbart. Vertragsentwürfe: {0} (PL, DE).",
            ["approve"] = "Unter Ihrer Autonomie-Schwelle: Unterschreiben gibt auch diesen Deal frei.",
            ["requirements"] = "Anforderungen", ["reqMet"] = "erfüllt", ["reqEquivalent"] = "erfüllt, {0} mit gleichwertigem Nachweis",
            ["summary"] = "Kurz gesagt", ["details"] = "Details", ["documents"] = "Unterlagen",
            ["footer"] = "Unterschreiben gibt das Angebot frei. Qualifizierte Signaturen und die Abgabe bleiben bei Ihnen.",
            ["sign"] = "✅ Unterschreiben", ["decline"] = "❌ Nicht unterschreiben", ["notice"] = "Bekanntmachung in TED", ["open"] = "In EU Patriot öffnen",
            ["signedBy"] = "✅ Freigegeben von {0}", ["declinedBy"] = "❌ Abgelehnt von {0}",
            ["answerSign"] = "Gespeichert: Sie können unterschreiben.", ["answerDecline"] = "Gespeichert: keine Unterschrift.",
            ["already"] = "Bereits entschieden.", ["notAllowed"] = "Aus diesem Chat nicht erlaubt.", ["notFound"] = "Nicht gefunden.",
            ["stopped"] = "⏸ Automatisches Bieten gestoppt", ["test"] = "✅ EU Patriot ist verbunden. Unterschriftsanfragen kommen in diesen Chat.",
            ["connected"] = "Verbunden. Unterschriftsanfragen von EU Patriot kommen hier an.\nChat-ID: <code>{0}</code>",
            ["otherChat"] = "Dieser Bot ist mit einem anderen Chat verknüpft.",
            ["noteQualifies"] = "Geeignet und ausreichend Kapazität.", ["noteGap"] = "Geeignet; es fehlen {0} für {1}.",
            ["noteSearching"] = "Suche einen Partner für {0}.", ["noteNegotiating"] = "Verhandle mit {0} in Ihrem Rahmen.",
            ["noteDrafting"] = "Deal abgeschlossen. Erstelle die Unterlagen.", ["noteReady"] = "Bereit zur Unterschrift. Wartet auf Ihre Entscheidung.",
            ["noteReadyApprove"] = "Bereit. Der Partner-Deal liegt unter Ihrer Autonomie-Schwelle, Unterschreiben gibt ihn mit frei.",
            ["noteNoPartner"] = "Kein Partner hat die Pflichtkriterien erfüllt; das Angebot ist nicht vollständig.",
            ["noteNoDeal"] = "Die Agenten fanden keine Einigung im Rahmen.", ["noteError"] = "Beim Vorbereiten ist etwas schiefgegangen; nichts wurde versendet.",
            ["noteSigned"] = "Freigegeben von {0}. Weiter: qualifizierte Signaturen und Abgabe auf {1}, durch einen Menschen.",
            ["noteDeclined"] = "Abgelehnt von {0}. Nichts wird unterschrieben oder abgegeben.", ["portal"] = "der Vergabeplattform",
        },
    };

    private static readonly Dictionary<string, Dictionary<string, string>> Units = new()
    {
        ["pl"] = new() { ["SE"] = "kpl.", ["EA"] = "szt.", ["PR"] = "par", ["KT"] = "zest." },
        ["en"] = new() { ["SE"] = "sets", ["EA"] = "pcs", ["PR"] = "pairs", ["KT"] = "kits" },
        ["de"] = new() { ["SE"] = "Sätze", ["EA"] = "Stk.", ["PR"] = "Paar", ["KT"] = "Kits" },
    };

    private static readonly TimeZoneInfo CentralEurope = TimeZoneInfo.FindSystemTimeZoneById("Europe/Warsaw");

    public string Language { get; }
    public CultureInfo Culture { get; }

    public AutoBidText(string? language)
    {
        Language = language is "pl" or "de" ? language : "en";
        Culture = CultureInfo.GetCultureInfo(Language switch { "pl" => "pl-PL", "de" => "de-DE", _ => "en-GB" });
    }

    public string this[string key, params object?[] args] =>
        string.Format(Culture, Texts[Language].GetValueOrDefault(key) ?? Texts["en"][key], args);

    public string Unit(string? code) => code is null ? "" : Units[Language].GetValueOrDefault(code, code);
    public string Qty(decimal? n, string? unit) => $"{n?.ToString("N0", Culture)} {Unit(unit)}".Trim();

    /// <summary>"€407.99" in English, "407,99 €" in Polish and German.</summary>
    public string Eur(decimal? v, int digits = 2)
    {
        var n = (v ?? 0).ToString("N" + digits, Culture);
        return Language == "en" ? $"€{n}" : $"{n} €";
    }

    public string Date(DateOnly d) => d.ToString(Language == "en" ? "d MMM yyyy" : "d MMM yyyy", Culture);

    /// <summary>Central European local time, which is what Polish, Czech and German bidders work in.</summary>
    public string DateTime(DateTimeOffset t)
    {
        var local = TimeZoneInfo.ConvertTime(t, CentralEurope);
        return $"{local.ToString("d MMM yyyy, HH:mm", Culture)} {(CentralEurope.IsDaylightSavingTime(local) ? "CEST" : "CET")}";
    }
}
