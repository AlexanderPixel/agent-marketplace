using Marketplace.Data;

namespace Marketplace.Compliance;

public record ComponentView(BomComponent Component, bool Eligible, decimal Share);

public record SwapSuggestion(string ComponentId, string Component, string From, string FromCountry, BomAlternative To, decimal CostDelta, decimal NewEuContent, bool ReachesThreshold);

public record ControlCheck(string Company, string Country, string Role, List<OwnershipLink> Chain, bool EuControlled, string Detail);

public record SanctionHit(string Name, string Country, bool Hit, string Detail);

/// <summary>
/// SAFE (Reg. (EU) 2025/1106) and EDIP eligibility: components from outside the EU, EEA-EFTA and Ukraine may not exceed 35% of
/// component cost (so at least 65% "EU content"), and the companies involved must not be controlled from a non-associated
/// third country. Real arithmetic on the seeded BOM; ownership and sanctions data are demo copies.
/// </summary>
public static class BuyEuropean
{
    public const decimal Threshold = 65m;

    public static readonly HashSet<string> Eligible =
    [
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
        "RO", "SK", "SI", "ES", "SE", "NO", "IS", "LI", "UA",
    ];

    /// <summary>Demo excerpt of the EU consolidated sanctions list, so the screen has something real to compare against.</summary>
    private static readonly (string Name, string Country)[] SanctionsList =
    [
        ("Kalashnikov Concern JSC", "RU"), ("Uralvagonzavod", "RU"), ("Promtekhnologiya LLC", "RU"), ("JSC Kurganmashzavod", "RU"),
        ("Shaanxi Baoji Special Vehicles", "CN"),
        // Fictional, for the demo: a listed party that is already in the BOM.
        ("Severo-Ural Steel Shot LLC", "RU"),
    ];

    /// <summary>Listed parties: no contracts, orders, payments or other resources may go to them (EU asset freeze).</summary>
    public static bool Sanctioned(string name) => SanctionsList.Any(s => s.Name.Equals(name.Trim(), StringComparison.OrdinalIgnoreCase));

    public const string SanctionsDetail =
        "On the EU consolidated sanctions list (asset freeze). No orders, payments or other resources may go to this party; stop and replace the supplier.";

    public static decimal EuContent(IEnumerable<BomComponent> components)
    {
        var list = components.ToList();
        var total = list.Sum(c => c.CostEur);
        return total == 0 ? 0 : Math.Round(100 * list.Where(c => Eligible.Contains(c.Country)).Sum(c => c.CostEur) / total, 1);
    }

    public static List<ComponentView> Components(BillOfMaterials bom)
    {
        var total = bom.Components.Sum(c => c.CostEur);
        return bom.Components.Select(c => new ComponentView(c, Eligible.Contains(c.Country), Math.Round(100 * c.CostEur / total, 1))).ToList();
    }

    /// <summary>Every single swap of a non-eligible supplier to a qualified EU alternative, cheapest way past 65% first.</summary>
    /// <summary>The note on the alternative a signed change replaced; swapping back to it is an undo.</summary>
    public const string PreviousSupplier = "Previous supplier";

    public static List<SwapSuggestion> Swaps(BillOfMaterials bom) => bom.Components
        .Where(c => !Eligible.Contains(c.Country))
        .SelectMany(c => c.Alternatives.Where(a => Eligible.Contains(a.Country) && !Sanctioned(a.Supplier)).Select(a =>
        {
            var swapped = bom.Components.Select(x => x.Id == c.Id ? x with { Supplier = a.Supplier, Country = a.Country, CostEur = a.CostEur } : x);
            var pct = EuContent(swapped);
            return new SwapSuggestion(c.Id, c.Name, c.Supplier, c.Country, a, a.CostEur - c.CostEur, pct, pct >= Threshold);
        }))
        .OrderByDescending(s => s.ReachesThreshold).ThenBy(s => s.CostDelta).ThenByDescending(s => s.NewEuContent)
        .ToList();

    public static ControlCheck Control(Organization org, CompanyProfile? profile, string role)
    {
        var chain = profile?.Ownership ?? [];
        var foreign = chain.FirstOrDefault(l => l.Share > 50 && !Eligible.Contains(l.Country));
        return foreign is null
            ? new ControlCheck(org.Name, org.Country, role, chain, true, "Established and controlled in the EU.")
            : new ControlCheck(org.Name, org.Country, role, chain, false,
                $"Controlled from {foreign.Country} via {foreign.Entity} ({foreign.Share}%). Needs SAFE guarantees approved by its home state before it can take part.");
    }

    public static List<SanctionHit> Screen(IEnumerable<(string Name, string Country)> parties) => parties
        .Distinct()
        .Select(p =>
        {
            var hit = SanctionsList.FirstOrDefault(s => s.Name.Equals(p.Name, StringComparison.OrdinalIgnoreCase));
            return new SanctionHit(p.Name, p.Country, hit != default, hit != default ? SanctionsDetail : "No match.");
        })
        .ToList();
}
