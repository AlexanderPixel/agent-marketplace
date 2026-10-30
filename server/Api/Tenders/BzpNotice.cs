using System.Globalization;
using System.Text.Json;
using System.Text.RegularExpressions;
using Marketplace.Data;

namespace Marketplace.Tenders;

/// <summary>Maps an e-Zamówienia BZP notice (mo-board API) to a <see cref="Tender"/>. BZP already uses OCDS OCIDs.</summary>
public static partial class BzpNotice
{
    /// <summary>Sections of the notice that hold qualification requirements, shown with their BZP number for citations.</summary>
    private static readonly Dictionary<string, string> RequirementSections = new()
    {
        ["5.4"] = "Warunki udziału w postępowaniu",
        ["5.6"] = "Podmiotowe środki dowodowe (wykluczenie)",
        ["5.7"] = "Podmiotowe środki dowodowe (warunki udziału)",
        ["5.8"] = "Przedmiotowe środki dowodowe",
        ["5.11"] = "Inne wymagane oświadczenia lub dokumenty",
        ["6.4.1"] = "Wadium",
    };

    public static Tender Map(JsonElement n)
    {
        var cpvRaw = n.GetProperty("cpvCode").GetString() ?? "";
        var cpvMatch = CpvRegex().Match(cpvRaw);
        var text = n.TryGetProperty("text", out var t) ? t.GetString() : null;
        var buyer = n.GetProperty("organizationName").GetString() ?? "";
        var cpv = cpvMatch.Success ? cpvMatch.Groups[1].Value : "";
        var ocid = n.GetProperty("tenderId").GetString() ?? n.GetProperty("noticeNumber").GetString()!;
        var title = Regex.Replace(n.GetProperty("orderObject").GetString() ?? "", @"\s+", " ").Trim();

        return new Tender
        {
            Id = ocid,
            Source = TenderSource.BZP,
            SourceId = n.GetProperty("bzpNumber").GetString() ?? "",
            Title = title,
            Language = "pl",
            Titles = new() { ["pl"] = title },
            BuyerName = buyer,
            BuyerCountry = n.GetProperty("organizationCountry").GetString() ?? "PL",
            BuyerCity = n.GetProperty("organizationCity").GetString(),
            Cpv = cpv,
            CpvLabels = cpvMatch.Success ? new() { ["pl"] = cpvMatch.Groups[2].Value.Trim() } : [],
            ProcedureType = "basic",
            ContractNature = n.GetProperty("orderType").GetString()?.ToLowerInvariant() switch
            {
                "delivery" => "supplies", "services" => "services", "works" => "works", var other => other,
            },
            PublishedAt = DateTimeOffset.Parse(n.GetProperty("publicationDate").GetString()!, CultureInfo.InvariantCulture),
            Deadline = n.TryGetProperty("submittingOffersDate", out var d) && d.GetString() is { Length: > 0 } ds
                ? DateTimeOffset.Parse(ds, CultureInfo.InvariantCulture) : null,
            IsDefence = cpv.StartsWith("35") || TedNotice.Defence.IsMatch(buyer),
            HasFullNotice = text is not null,
            NoticeUrl = $"https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/id/{n.GetProperty("objectId").GetString()}",
            DocumentsUrl = $"https://ezamowienia.gov.pl/mp-client/search/list/{ocid}",
            Requirements = text is null ? [] : Requirements(text),
            Text = text,
        };
    }

    private static List<TenderRequirement> Requirements(string text)
    {
        var result = new List<TenderRequirement>();
        var parts = SectionRegex().Split(text);
        for (var i = 1; i + 1 < parts.Length; i += 2)
        {
            if (!RequirementSections.TryGetValue(parts[i], out var name)) continue;
            var body = parts[i + 1].Trim().TrimStart(':').Trim();
            var colon = body.IndexOf(':');
            if (colon >= 0 && colon < 200) body = body[(colon + 1)..].Trim();
            if (body.Length == 0 || body.StartsWith("Nie") || body.StartsWith("nie dotyczy")) continue;
            if (body.Length > 1500) body = body[..1500] + "…";
            result.Add(new TenderRequirement(null, $"{name}: {body}", "pl", [], $"Ogłoszenie BZP, pkt {parts[i]}"));
        }

        return result;
    }

    [GeneratedRegex(@"^(\d{8})-\d\s*\((.*)\)")]
    private static partial Regex CpvRegex();

    [GeneratedRegex(@"(?:^|\s)(\d\.\d+(?:\.\d+)?)\.\)", RegexOptions.Multiline)]
    private static partial Regex SectionRegex();
}
