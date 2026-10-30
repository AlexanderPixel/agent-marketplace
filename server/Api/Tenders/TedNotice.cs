using System.Globalization;
using System.Text.Json;
using System.Text.RegularExpressions;
using Marketplace.Data;

namespace Marketplace.Tenders;

/// <summary>
/// Maps a TED eForms notice (the field map the TED Search API returns, keyed by eForms field id) to a <see cref="Tender"/>.
/// Works for the full field set and for the short list rows (title, buyer, CPV, deadline only).
/// </summary>
public static partial class TedNotice
{
    public static readonly string[] SearchFields =
    [
        "publication-number", "publication-date", "notice-type", "notice-title", "buyer-name", "buyer-country",
        "organisation-city-buyer", "classification-cpv", "main-classification-proc", "main-classification-lot",
        "procedure-identifier", "internal-identifier-proc", "procedure-type", "contract-nature-main-proc",
        "authority-main-activity", "official-language", "identifier-lot", "title-lot", "description-lot",
        "place-of-performance-city-lot", "selection-criterion-lot", "selection-criterion-description-lot",
        "exclusion-grounds", "deadline-receipt-tender-date-lot", "deadline-receipt-tender-time-lot",
        "deadline-receipt-request", "document-url-lot", "links",
    ];

    public static Tender Map(JsonElement n, string rawJson)
    {
        var pub = Str(n, "publication-number") ?? "";
        var language = Codes.Language(Str(n, "official-language") ?? "POL");
        var cpvs = List(n, "main-classification-proc");
        if (cpvs.Count == 0) cpvs = List(n, "classification-cpv");
        var cpv = cpvs.FirstOrDefault() ?? "";

        var titles = new Dictionary<string, string>();
        var cpvLabels = new Dictionary<string, string>();
        if (n.TryGetProperty("notice-title", out var nt) && nt.ValueKind == JsonValueKind.Object)
        {
            // TED titles read "Country – CPV label – buyer's title", translated into every EU language.
            foreach (var p in nt.EnumerateObject())
            {
                var parts = p.Value.GetString()!.Split(" – ", 3);
                var lang = Codes.Language(p.Name);
                titles[lang] = parts.Length == 3 ? $"{parts[1]} – {parts[2]}" : p.Value.GetString()!;
                if (parts.Length == 3) cpvLabels[lang] = parts[1];
            }
        }

        var (buyerName, _) = Multi(n, "buyer-name");
        var lotIds = List(n, "identifier-lot");
        var (lotTitles, _) = Multi(n, "title-lot");
        var (lotDescriptions, _) = Multi(n, "description-lot");
        var lotCpvs = List(n, "main-classification-lot");
        var lotCities = List(n, "place-of-performance-city-lot");
        var lots = lotIds.Select((id, i) =>
        {
            var description = At(lotDescriptions, i);
            var (qty, unit) = Quantity(description);
            return new TenderLot(id, At(lotTitles, i) ?? id, qty, unit, qty is null ? description : null,
                At(lotCpvs, i), At(lotCities, i), $"eForms BT-21-Lot / BT-24-Lot, {id}");
        }).ToList();

        var codes = List(n, "selection-criterion-lot");
        var (criteria, criteriaLang) = Multi(n, "selection-criterion-description-lot");
        var requirements = criteria
            .Select((text, i) => (code: At(codes, i), text, lot: At(lotIds, i)))
            .GroupBy(c => (c.code, c.text))
            .Select(g => new TenderRequirement(g.Key.code, g.Key.text, Codes.Language(criteriaLang),
                g.Select(c => c.lot).OfType<string>().Distinct().ToList(), "eForms BT-750-Lot (selection criterion)"))
            .ToList();

        var activity = List(n, "authority-main-activity");
        var buyer = buyerName.FirstOrDefault() ?? "";
        return new Tender
        {
            Id = "ocds-ted-" + (Str(n, "procedure-identifier") ?? pub),
            Source = TenderSource.TED,
            SourceId = pub,
            Reference = Str(n, "internal-identifier-proc"),
            Title = titles.GetValueOrDefault(language) ?? titles.Values.FirstOrDefault() ?? pub,
            Language = language,
            Titles = titles,
            BuyerName = buyer,
            BuyerCountry = Codes.Country(Str(n, "buyer-country")),
            BuyerCity = Str(n, "organisation-city-buyer"),
            Cpv = cpv,
            CpvLabels = cpvLabels,
            ProcedureType = Str(n, "procedure-type"),
            ContractNature = Str(n, "contract-nature-main-proc"),
            PublishedAt = Date(Str(n, "publication-date")) ?? DateTimeOffset.MinValue,
            Deadline = Deadline(n),
            IsDefence = activity.Contains("defence") || cpv.StartsWith("35") || Defence.IsMatch(buyer),
            HasFullNotice = lotIds.Count > 0,
            NoticeUrl = $"https://ted.europa.eu/en/notice/-/detail/{pub}",
            DocumentsUrl = List(n, "document-url-lot").FirstOrDefault(),
            Lots = lots,
            Requirements = requirements,
            ExclusionGrounds = List(n, "exclusion-grounds").Distinct().ToList(),
            RawJson = rawJson,
        };
    }

    [GeneratedRegex(@"wojsk|zbroj|obron|bundeswehr|baainbw|armée|armament|defen[cs]e|rblog|jednostka wojskowa|policj|straż graniczna", RegexOptions.IgnoreCase)]
    private static partial Regex DefenceRegex();
    public static readonly Regex Defence = DefenceRegex();

    [GeneratedRegex(@"^\s*(\d[\d.,]*)\s*([A-Z0-9]{2,3})\s*$")]
    private static partial Regex QuantityRegex();

    /// <summary>German defence notices put the quantity alone in the lot description, e.g. "800 SE".</summary>
    private static (decimal?, string?) Quantity(string? description)
    {
        var m = description is null ? null : QuantityRegex().Match(description);
        if (m is not { Success: true }) return (null, null);
        var value = decimal.Parse(m.Groups[1].Value.Replace(".", "").Replace(",", "."), CultureInfo.InvariantCulture);
        return (value, m.Groups[2].Value);
    }

    private static DateTimeOffset? Deadline(JsonElement n)
    {
        var date = List(n, "deadline-receipt-tender-date-lot").FirstOrDefault();
        var time = List(n, "deadline-receipt-tender-time-lot").FirstOrDefault();
        if (date is not null && time is not null)
        {
            return DateTimeOffset.Parse($"{date[..10]}T{time}", CultureInfo.InvariantCulture);
        }

        return Date(List(n, "deadline-receipt-request").FirstOrDefault()) ?? Date(date, endOfDay: true);
    }

    /// <summary>eForms dates come as "2026-11-10+01:00" (date with offset) or a full timestamp.</summary>
    public static DateTimeOffset? Date(string? s, bool endOfDay = false)
    {
        if (string.IsNullOrEmpty(s)) return null;
        if (s.Contains('T')) return DateTimeOffset.Parse(s, CultureInfo.InvariantCulture);
        var offset = s.Length > 10 ? s[10..] : "+00:00";
        return DateTimeOffset.Parse($"{s[..10]}T{(endOfDay ? "23:59:00" : "00:00:00")}{offset}", CultureInfo.InvariantCulture);
    }

    private static string? At(List<string> list, int i) => i < list.Count ? list[i] : null;

    private static string? Str(JsonElement n, string key) =>
        !n.TryGetProperty(key, out var v) ? null
        : v.ValueKind == JsonValueKind.String ? v.GetString()
        : v.ValueKind == JsonValueKind.Array && v.GetArrayLength() > 0 ? v[0].ToString()
        : v.ValueKind == JsonValueKind.Object ? Multi(n, key).values.FirstOrDefault()
        : null;

    private static List<string> List(JsonElement n, string key) =>
        n.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Array
            ? v.EnumerateArray().Select(x => x.ToString()).ToList()
            : [];

    /// <summary>Multilingual fields look like {"deu": ["..."]}; returns the values in the first language.</summary>
    private static (List<string> values, string? language) Multi(JsonElement n, string key)
    {
        if (!n.TryGetProperty(key, out var v) || v.ValueKind != JsonValueKind.Object) return ([], null);
        foreach (var p in v.EnumerateObject())
        {
            return p.Value.ValueKind == JsonValueKind.Array
                ? (p.Value.EnumerateArray().Select(x => x.ToString()).ToList(), p.Name)
                : ([p.Value.ToString()], p.Name);
        }

        return ([], null);
    }
}
