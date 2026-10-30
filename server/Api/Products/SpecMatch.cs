using System.Globalization;
using System.Text.Json;
using Marketplace.Data;

namespace Marketplace.Products;

/// <param name="Key">Spec key this row checks; null for rows a bidder confirms in the offer (samples, training, scopes).</param>
/// <param name="Op">min, above, range, equals, yes, oneOf, mention, moa (lower is better) or info.</param>
public record NeedRow(string Ser, string Section, string Description, string Required, string? Key, string Op,
    decimal? A, decimal? B, string? Unit, List<string>? Values);

/// <summary>A buyer's technical specification: what the market asks for, row by row, with where it came from.</summary>
public record MarketNeed(string Id, string Title, string Buyer, string Country, string Category, string? Classification,
    string Source, string? Note, List<NeedRow> Rows);

public enum RowStatus { Met, Missing, Unclear, Info }

public record RowCheck(NeedRow Row, RowStatus Status, string? Yours, string Reason);

/// <summary>Met: every checked row passes. Open: nothing fails but some rows need the buyer's answer. Gap: a row fails.</summary>
public record NeedMatch(MarketNeed Need, string Verdict, int Met, int Checked, List<RowCheck> Rows);

/// <summary>Buyer specifications loaded from <c>demo-data/needs</c>.</summary>
public static class MarketNeeds
{
    public static List<MarketNeed> All { get; private set; } = [];

    public static void Load(string folder)
    {
        if (!Directory.Exists(folder)) return;
        All = Directory.GetFiles(folder, "*.json")
            .Select(f => JsonSerializer.Deserialize<MarketNeed>(File.ReadAllText(f), JsonSerializerOptions.Web)!)
            .ToList();
    }
}

/// <summary>
/// Compares a product's technical sheet with a buyer's specification. Conservative (rule 7): a value the sheet does not
/// state, or a requirement that is ambiguous, is never counted as met.
/// </summary>
public static class SpecMatch
{
    private static readonly CultureInfo En = CultureInfo.GetCultureInfo("en-GB");

    /// <summary>Rifles compete with rifles: a sniper-rifle specification is also checked against an assault rifle.</summary>
    public static bool Relevant(CompanyProduct p, MarketNeed n) =>
        p.Category is { } c && (c == n.Category || (c.EndsWith("rifle") && n.Category.EndsWith("rifle")));

    public static NeedMatch Check(CompanyProduct product, MarketNeed need)
    {
        var specs = (product.Specs ?? []).ToDictionary(s => s.Key);
        var rows = need.Rows.Select(r => CheckRow(r, specs.GetValueOrDefault(r.Key ?? ""), product)).ToList();
        var scored = rows.Where(r => r.Status != RowStatus.Info).ToList();
        var verdict = scored.Any(r => r.Status == RowStatus.Missing) ? "gap" : scored.Any(r => r.Status == RowStatus.Unclear) ? "open" : "met";
        return new NeedMatch(need, verdict, scored.Count(r => r.Status == RowStatus.Met), scored.Count, rows);
    }

    private static RowCheck CheckRow(NeedRow r, SpecValue? s, CompanyProduct p)
    {
        if (r.Key is null || r.Op == "info") return new(r, RowStatus.Info, null, "Confirm in your offer.");
        var yours = s is null ? null : Show(s);
        if (s is null) return new(r, r.Op == "mention" ? RowStatus.Missing : RowStatus.Unclear, null, "Not on your spec sheet.");

        var n = s.Number;
        var text = (s.Text ?? "").ToLowerInvariant();
        string F(decimal? v) => v?.ToString("0.##", En) ?? "";
        return r.Op switch
        {
            "min" when n is not null => n >= r.A ? Ok() : Gap($"Below the minimum of {F(r.A)} {r.Unit}."),
            "above" when n is not null => n > r.A ? Ok() : Gap($"Not above {F(r.A)} {r.Unit}."),
            "range" when n is not null => n >= r.A && n <= r.B ? Ok() : Gap($"Outside {F(r.A)}–{F(r.B)} {r.Unit}."),
            "moa" when n is not null => n <= r.A
                ? Ok()
                : new(r, RowStatus.Unclear, yours, $"The spec says \"{r.Required}\". A lower MOA is better, so this most likely means at most {F(r.A)} MOA; yours is {F(n)}. Ask the buyer."),
            "equals" => r.Values!.Any(v => text.Contains(v)) ? Ok() : Gap($"Required: {r.Required}."),
            "yes" => text.StartsWith("yes") ? Ok() : text.StartsWith("no") ? Gap("Required: yes.") : new(r, RowStatus.Unclear, yours, "Not stated clearly."),
            "oneOf" => Country(text.ToUpperInvariant()),
            "mention" => Ok("Stated on your spec sheet."),
            _ => new(r, RowStatus.Unclear, yours, "Could not be compared automatically."),
        };

        RowCheck Ok(string why = "Meets the requirement.") => new(r, RowStatus.Met, yours, why);
        RowCheck Gap(string why) => new(r, RowStatus.Missing, yours, why);
        RowCheck Country(string code) =>
            r.Values!.Contains(code)
                ? Ok()
                : new(r, RowStatus.Unclear, yours,
                    $"Allowed: {r.Required}. \"Group A\" is not defined in the specification; ask the buyer whether {code} counts as Group A.");
    }

    private static string Show(SpecValue s) =>
        s.Number is { } n ? $"{n.ToString("0.##", En)} {s.Unit}".Trim() + (s.Text is null ? "" : $" ({s.Text})") : s.Text ?? "";
}
