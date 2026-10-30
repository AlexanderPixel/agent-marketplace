using System.Globalization;
using System.Text;
using Marketplace.Ai;
using Marketplace.Data;

namespace Marketplace.Qualification;

/// <param name="Cite">Where the point comes from in the notice, e.g. "BT-21-Lot LOT-0004" or "Ogłoszenie BZP, pkt 5.4".</param>
public record SummaryPoint(string Text, string Cite);

public record TenderSummaryText(string Language, string Headline, List<SummaryPoint> Points, bool ByAi);

/// <summary>
/// Summarises a notice in the reader's language. Grounded (rule 2): the model only sees the notice's own fields, every
/// point must cite one of them, and points with unknown citations are dropped. Without AI a template does the same.
/// </summary>
public class TenderSummaryService(AiService ai)
{
    private record AiSummary(string? Headline, List<SummaryPoint>? Points);

    public async Task<TenderSummaryText> SummariseAsync(Tender t, string language, CancellationToken ct)
    {
        var sources = Sources(t);
        if (ai.Enabled)
        {
            var system =
                "You summarise public procurement notices for a supplier. Use ONLY the facts inside <source> blocks. " +
                $"Write in the language with ISO code '{language}'. Keep Polish legal document names (e.g. SWZ, wadium, odpis z KRS) and German register names untranslated. " +
                "Reply as JSON: {\"headline\": string, \"points\": [{\"text\": string, \"cite\": string}]}. 4 to 7 points: what is bought, quantities, " +
                "deadline, where to deliver, who may bid and what evidence is required. Each cite must be the id of the source block it comes from. " +
                "If something is not in the sources, do not mention it.";
            var user = string.Join("\n", sources.Select(s => $"<source id=\"{s.Key}\">{AiService.Neutralize(s.Value)}</source>"));
            var result = await ai.AskJsonAsync<AiSummary>(system, user, ct);
            var points = result?.Points?.Where(p => !string.IsNullOrWhiteSpace(p.Text) && sources.ContainsKey(p.Cite)).ToList();
            if (result?.Headline is { Length: > 0 } && points is { Count: > 0 })
            {
                return new TenderSummaryText(language, result.Headline, points, true);
            }
        }

        return Template(t, language);
    }

    /// <summary>The notice facts the model may use, keyed by the citation shown to the user.</summary>
    private static Dictionary<string, string> Sources(Tender t)
    {
        var s = new Dictionary<string, string>
        {
            ["notice"] = $"Title: {t.Title}. Buyer: {t.BuyerName}, {t.BuyerCity}, {t.BuyerCountry}. CPV {t.Cpv}. " +
                $"Procedure: {t.ProcedureType}. Nature: {t.ContractNature}. Deadline: {t.Deadline:yyyy-MM-dd HH:mm} UTC. Language: {t.Language}.",
        };
        foreach (var lot in t.Lots)
        {
            s[$"{lot.Id}"] = $"Lot {lot.Id}: {lot.Title}. Quantity: {lot.Quantity} {lot.Unit}. Delivery to: {lot.Place}. {lot.Description}";
        }

        foreach (var r in t.Requirements)
        {
            s[r.Source] = $"Requirement ({r.Code}): {r.Text}" + (r.Lots.Count > 0 ? $" Applies to lots: {string.Join(", ", r.Lots)}." : "");
        }

        if (t.Text is { } text) s["notice-text"] = text.Length > 6000 ? text[..6000] : text;
        return s;
    }

    private static readonly Dictionary<string, Dictionary<string, string>> Words = new()
    {
        ["pl"] = new()
        {
            ["buys"] = "{0} ({1}) kupuje: {2}.", ["lots"] = "{0} części; największe ilości: {1}.",
            ["deadline"] = "Termin składania ofert: {0} ({1} dni).", ["closed"] = "Termin składania ofert minął: {0}.",
            ["place"] = "Dostawy do: {0}.", ["req"] = "Wymagany dokument: {0}", ["lang"] = "Ogłoszenie w języku: {0}; oferta w tym samym języku.",
            ["headline"] = "{0}: {1}",
        },
        ["en"] = new()
        {
            ["buys"] = "{0} ({1}) is buying: {2}.", ["lots"] = "{0} lots; largest quantities: {1}.",
            ["deadline"] = "Submission deadline: {0} ({1} days left).", ["closed"] = "Submission deadline passed: {0}.",
            ["place"] = "Deliveries to: {0}.", ["req"] = "Required evidence: {0}", ["lang"] = "Notice language: {0}; bids are expected in the same language.",
            ["headline"] = "{0}: {1}",
        },
        ["de"] = new()
        {
            ["buys"] = "{0} ({1}) beschafft: {2}.", ["lots"] = "{0} Lose; größte Mengen: {1}.",
            ["deadline"] = "Angebotsfrist: {0} (noch {1} Tage).", ["closed"] = "Angebotsfrist abgelaufen: {0}.",
            ["place"] = "Lieferorte: {0}.", ["req"] = "Geforderter Nachweis: {0}", ["lang"] = "Sprache der Bekanntmachung: {0}; Angebote in derselben Sprache.",
            ["headline"] = "{0}: {1}",
        },
    };

    private static TenderSummaryText Template(Tender t, string language)
    {
        var w = Words.GetValueOrDefault(language) ?? Words["en"];
        var culture = CultureInfo.GetCultureInfo(language);
        var title = t.Titles.GetValueOrDefault(language) ?? t.Title;
        var subject = t.CpvLabels.GetValueOrDefault(language) ?? t.CpvLabels.GetValueOrDefault(t.Language) ?? title;
        var country = new RegionInfo(t.BuyerCountry).DisplayName;
        var points = new List<SummaryPoint> { new(string.Format(w["buys"], t.BuyerName, t.BuyerCity ?? t.BuyerCountry, subject), "notice") };

        if (t.Lots.Count > 0)
        {
            var top = t.Lots.Where(l => l.Quantity is not null).OrderByDescending(l => l.Quantity).Take(3)
                .Select(l => $"{l.Title} {l.Quantity:0} {l.Unit} ({l.Id})");
            points.Add(new(string.Format(w["lots"], t.Lots.Count, string.Join("; ", top)), t.Lots[0].Id));
            var places = t.Lots.Select(l => l.Place).OfType<string>().Distinct().ToList();
            if (places.Count > 0) points.Add(new(string.Format(w["place"], string.Join(", ", places)), t.Lots[0].Id));
        }

        if (t.Deadline is { } d)
        {
            var days = (int)Math.Ceiling((d - DateTimeOffset.UtcNow).TotalDays);
            var when = d.ToLocalTime().ToString("f", culture);
            points.Add(new(days >= 0 ? string.Format(w["deadline"], when, days) : string.Format(w["closed"], when), "notice"));
        }

        foreach (var r in t.Requirements.Take(4))
        {
            var text = r.Text.Length > 220 ? r.Text[..220] + "…" : r.Text;
            points.Add(new(string.Format(w["req"], text), r.Source));
        }

        points.Add(new(string.Format(w["lang"], CultureInfo.GetCultureInfo(t.Language).NativeName), "notice"));
        return new TenderSummaryText(language, string.Format(w["headline"], country, title), points, false);
    }
}
