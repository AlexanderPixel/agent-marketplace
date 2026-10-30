using System.Text.Json;
using System.Text.RegularExpressions;
using Marketplace.Tenders;

namespace Marketplace.Intelligence;

public record PrimeAward(
    string PublicationNumber, DateTimeOffset PublishedAt, string Buyer, string BuyerCountry, List<string> Winners, string Prime,
    string Cpv, decimal? Value, Dictionary<string, string> Titles, string? Platform, bool Framework, string Url);

/// <summary>
/// 2026 TED contract award notices won by Rheinmetall and KNDS entities (cached in demo-data/ted/prime-awards.json).
/// Each prime award creates tier-2 demand: spare parts, sub-assemblies and services the prime buys in.
/// </summary>
public static partial class PrimeAwards
{
    public static List<PrimeAward> All { get; private set; } = [];

    public static void Load(string file)
    {
        if (!File.Exists(file)) return;
        All = JsonDocument.Parse(File.ReadAllText(file)).RootElement.EnumerateArray().Select(n =>
        {
            var winners = First(n, "winner-name");
            var titles = new Dictionary<string, string>();
            foreach (var p in n.GetProperty("notice-title").EnumerateObject())
            {
                var parts = p.Value.GetString()!.Split(" – ", 3);
                titles[Codes.Language(p.Name)] = parts.Length == 3 ? $"{parts[1]} – {parts[2]}" : p.Value.GetString()!;
            }

            var original = titles.GetValueOrDefault("de") ?? titles.GetValueOrDefault("en") ?? titles.Values.First();
            var platform = PlatformRegex().Match(original);
            var raw = n.TryGetProperty("total-value", out var v)
                ? v.ValueKind == JsonValueKind.Array ? (v.GetArrayLength() > 0 ? v[0].GetDecimal() : 0) : v.GetDecimal()
                : 0;
            decimal? value = raw > 0 ? raw : null; // TED uses -1 for "not published"
            var pub = n.GetProperty("publication-number").GetString()!;
            return new PrimeAward(
                pub, TedNotice.Date(n.GetProperty("publication-date").GetString()) ?? DateTimeOffset.MinValue,
                First(n, "buyer-name").FirstOrDefault() ?? "", Codes.Country(n.GetProperty("buyer-country")[0].GetString()),
                winners, winners.Any(w => w.Contains("Rheinmetall", StringComparison.OrdinalIgnoreCase)) ? "Rheinmetall" : "KNDS",
                n.GetProperty("classification-cpv")[0].GetString() ?? "", value, titles,
                platform.Success && platform.Groups[1].Value != "Systemtechnik" ? platform.Groups[1].Value.Trim() : null,
                original.Contains("Rahmen", StringComparison.OrdinalIgnoreCase) || original.Contains("framework", StringComparison.OrdinalIgnoreCase),
                $"https://ted.europa.eu/en/notice/-/detail/{pub}");
        }).OrderByDescending(a => a.PublishedAt).ToList();
    }

    private static List<string> First(JsonElement n, string key)
    {
        foreach (var p in n.GetProperty(key).EnumerateObject())
        {
            return p.Value.EnumerateArray().Select(x => x.GetString() ?? "").ToList();
        }

        return [];
    }

    /// <summary>German notices name the weapon system: "… am System Mungo", "System Boxer", "Waffensystem PzH 2000".</summary>
    [GeneratedRegex(@"(?:Waffen)?[Ss]ystem(?:s)?\s+([A-Z][\w\-]*(?:\s(?:\d[\w\-]*|[A-Z]{2,}[\w\-]*))?)")]
    private static partial Regex PlatformRegex();
}

/// <summary>
/// SAFE loan allocations per member state, as indicated by the European Commission in September 2025 (EUR bn).
/// Seed data for the demo; check the Commission's current figures before relying on them.
/// </summary>
public static class SafeAllocations
{
    public static readonly (string Country, decimal Bn)[] All =
    [
        ("PL", 43.7m), ("RO", 16.6m), ("FR", 16.2m), ("HU", 16.2m), ("IT", 14.9m), ("BE", 8.3m), ("LT", 6.4m), ("PT", 5.8m),
        ("LV", 5.7m), ("BG", 3.3m), ("EE", 2.7m), ("SK", 2.3m), ("CZ", 2.1m), ("HR", 1.7m), ("CY", 1.2m), ("ES", 1.0m), ("FI", 1.0m), ("GR", 0.8m),
    ];
}
