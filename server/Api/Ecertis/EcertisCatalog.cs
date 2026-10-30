using System.Text.Json;
using System.Text.RegularExpressions;

namespace Marketplace.Ecertis;

public record EcertisEvidence(string Name, string? Issuer, string Country, string? Url, string? Validity);

public record EcertisMatch(
    string CriterionId, string CriterionName, string Url, List<EcertisEvidence> BuyerCountry, List<EcertisEvidence> CompanyCountry,
    EcertisEvidence? Matched, string? Note);

public record EcertisCriterion(string Id, string Name, string Type, Dictionary<string, List<EcertisEvidence>> EvidenceByCountry);

/// <summary>
/// The European Commission's eCertis mapping of which national document proves which EU criterion, from the cached
/// REST responses in demo-data/ecertis (GET ecertisrest/criteria/{id}?countryFilter=XX). Native-language names win.
/// </summary>
public static partial class EcertisCatalog
{
    public const string TradeRegister = "87b3fa26-3549-4f92-b8e0-3fd8f04bf5c7";
    public const string CriminalOrganisation = "005eb9ed-1347-4ca3-bb29-9bc0db64e1ab";

    public static string Link(string criterionId) => $"https://ec.europa.eu/tools/ecertis/#/criterion/detail/{criterionId}";

    /// <summary>Which eCertis criterion answers a document kind from <see cref="Data.CompanyDocument"/>.</summary>
    public static readonly Dictionary<string, string> CriterionForKind = new()
    {
        ["trade-register"] = TradeRegister,
        ["criminal-record"] = CriminalOrganisation,
    };

    public static Dictionary<string, EcertisCriterion> Criteria { get; private set; } = [];
    public static List<(string Id, string Name, string Type)> AllCriteria { get; private set; } = [];

    public static void Load(string dir)
    {
        if (!Directory.Exists(dir)) return;
        var criteria = new Dictionary<string, EcertisCriterion>();
        // "trade-register-DE-native.json" → country DE, native names; plain files fill in what is missing.
        foreach (var file in Directory.GetFiles(dir, "*.json").OrderByDescending(f => f.Contains("-native")))
        {
            var m = FileRegex().Match(Path.GetFileName(file));
            if (!m.Success) continue;
            var country = m.Groups[1].Value;
            var root = JsonDocument.Parse(File.ReadAllText(file)).RootElement;
            var id = root.TryGetProperty("ParentCriterion", out var parent) ? parent.GetProperty("Id").GetProperty("value").GetString()! : root.GetProperty("Id").GetProperty("value").GetString()!;
            if (!criteria.TryGetValue(id, out var criterion))
            {
                criterion = new EcertisCriterion(id, Value(root, "Name") ?? id, Value(root, "TypeCode") ?? "", []);
                criteria[id] = criterion;
            }

            if (criterion.EvidenceByCountry.ContainsKey(country)) continue;
            var evidence = new List<EcertisEvidence>();
            foreach (var sub in root.TryGetProperty("SubCriterion", out var s) ? s.EnumerateArray() : Enumerable.Empty<JsonElement>())
            foreach (var group in sub.TryGetProperty("RequirementGroup", out var g) ? g.EnumerateArray() : Enumerable.Empty<JsonElement>())
            foreach (var e in group.TryGetProperty("TypeOfEvidence", out var t) ? t.EnumerateArray() : Enumerable.Empty<JsonElement>())
            {
                var name = Value(e, "Name")?.Replace("(Automatische Übersetzung)", "").Trim();
                if (name is null || evidence.Any(x => x.Name == name)) continue;
                var issuer = e.TryGetProperty("EvidenceIssuerParty", out var parties)
                    ? parties.EnumerateArray()
                        .Where(p => p.TryGetProperty("PartyName", out var pn) && pn.GetArrayLength() > 0)
                        .Select(p => Value(p.GetProperty("PartyName")[0], "Name")?.Replace("(Automatische Übersetzung)", "").Trim())
                        .FirstOrDefault()
                    : null;
                string? url = null, validity = null;
                if (e.TryGetProperty("EvidenceDocumentReference", out var refs) && refs.GetArrayLength() > 0)
                {
                    var r = refs[0];
                    if (r.TryGetProperty("Attachment", out var a) && a.TryGetProperty("ExternalReference", out var x) && x.TryGetProperty("URI", out var u))
                        url = u.GetProperty("value").GetString();
                    if (r.TryGetProperty("ValidityPeriod", out var v) && v.TryGetProperty("description", out var d) && d.GetArrayLength() > 0)
                        validity = d[0].TryGetProperty("value", out var dv) ? dv.GetString() : null;
                }

                evidence.Add(new EcertisEvidence(name, issuer, country, url, validity));
            }

            criterion.EvidenceByCountry[country] = evidence;
        }

        Criteria = criteria;
        var list = Path.Combine(dir, "criteria-en.json");
        if (File.Exists(list))
        {
            AllCriteria = JsonDocument.Parse(File.ReadAllText(list)).RootElement.GetProperty("criterion").EnumerateArray()
                .Select(c => (c.GetProperty("Id").GetProperty("value").GetString()!, Value(c, "Name")!, c.GetProperty("CriteriaTypeID").GetProperty("value").GetString()!))
                .ToList();
            foreach (var (id, name, _) in AllCriteria)
            {
                if (criteria.TryGetValue(id, out var c)) criteria[id] = c with { Name = name };
            }
        }
    }

    /// <summary>
    /// eCertis view of one company document: which criterion it proves, what the buyer's country would accept, and the
    /// company country's evidence entry that matches the document (by shared issuer or title words).
    /// </summary>
    public static EcertisMatch? Match(Data.CompanyDocument doc, string buyerCountry, string? note = null)
    {
        if (!CriterionForKind.TryGetValue(doc.Kind, out var id) || !Criteria.TryGetValue(id, out var c)) return null;
        var buyer = c.EvidenceByCountry.GetValueOrDefault(buyerCountry) ?? [];
        var own = c.EvidenceByCountry.GetValueOrDefault(doc.Country) ?? [];
        var words = Words($"{doc.Title} {doc.Issuer}");
        var matched = own.MaxBy(e => Words($"{e.Name} {e.Issuer}").Intersect(words).Count());
        return new EcertisMatch(c.Id, c.Name, Link(c.Id), buyer, own, matched,
            note ?? (own.Count == 0 ? $"eCertis data for {doc.Country} is not cached in this demo." : null));
    }

    private static HashSet<string> Words(string s) =>
        WordRegex().Matches(s.ToLowerInvariant()).Select(m => m.Value).Where(w => w.Length > 4).ToHashSet();

    [GeneratedRegex(@"\p{L}+")]
    private static partial Regex WordRegex();

    private static string? Value(JsonElement e, string key) =>
        e.TryGetProperty(key, out var v) && v.TryGetProperty("value", out var s) ? s.GetString() : null;

    [GeneratedRegex(@"-([A-Z]{2})(-native)?\.json$")]
    private static partial Regex FileRegex();
}
