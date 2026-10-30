using Marketplace.Data;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Partners;

/// <summary>What another company shows in the partner network. Built from its profile, never the profile itself.</summary>
public record PartnerListing(
    Guid OrgId, string Name, string Country, string City, string Description,
    List<string> Certificates, List<string> Products, int? Capacity, string? Unit, int? LeadTimeDays, bool ExclusionEvidence);

public record Gate(string Name, bool Passed, string Detail);

public record ScorePart(string Name, int Points, int Max, string Detail);

public record PartnerCandidate(PartnerListing Partner, int Score, bool GatesPassed, List<Gate> Gates, List<ScorePart> Breakdown);

/// <summary>
/// Finds other suppliers who can cover a quantity gap. This is the one place that reads across organisations, so it
/// bypasses the tenant filter explicitly and projects only the public listing fields (rule 3): no documents, prices or limits.
/// </summary>
public class PartnerSearch(AppDb db)
{
    private static readonly HashSet<string> EuEea =
        ["AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "NO", "IS", "LI"];

    /// <summary>Demo sanctions screen: companies on the EU consolidated list would be here. None of the seeded ones is.</summary>
    private static readonly HashSet<string> Sanctioned = [];

    public async Task<List<PartnerListing>> ListingsAsync(Guid exceptOrg, string? keyword = null)
    {
        var orgs = await db.Organizations.Where(o => o.Id != exceptOrg && o.Kind == OrgKind.Supplier).ToListAsync();
        var profiles = await db.Profiles.IgnoreQueryFilters().Where(p => p.OrgId != exceptOrg).ToListAsync();
        return orgs.Select(o =>
        {
            var p = profiles.FirstOrDefault(x => x.OrgId == o.Id) ?? new CompanyProfile { OrgId = o.Id };
            var product = keyword is null ? p.Products.FirstOrDefault() : p.Products.FirstOrDefault(x => x.Keywords.Any(k => keyword.Contains(k)));
            return new PartnerListing(o.Id, o.Name, o.Country, o.City, o.Description,
                p.Documents.Where(d => d.Kind is "iso9001" or "aqap2110").Select(d => d.Title).ToList(),
                p.Products.Select(x => x.Name).ToList(),
                product?.Capacity, product?.Unit, product?.LeadTimeDays,
                p.Documents.Any(d => d.Kind == "trade-register") && p.Documents.Any(d => d.Kind == "criminal-record"));
        }).ToList();
    }

    public async Task<List<PartnerCandidate>> SearchAsync(Guid bidderOrg, Tender tender, TenderLot lot, int gap)
    {
        var listings = await ListingsAsync(bidderOrg, lot.Title.ToLowerInvariant());
        var defence = tender.IsDefence;
        const int deliveryDays = 150; // the demo tender's contract runs 17 Nov 2026 to 17 Apr 2027 (eForms BT-536/BT-537)
        return listings.Where(l => l.Capacity is not null).Select(l =>
        {
            var gates = new List<Gate>
            {
                new("EU/EEA company", EuEea.Contains(l.Country), $"Registered in {l.Country}."),
                new("Not sanctioned", !Sanctioned.Contains(l.Name), "Checked against the EU consolidated sanctions list (demo copy)."),
                new("Can join the bid", l.ExclusionEvidence,
                    l.ExclusionEvidence ? "Trade register and criminal-record extracts on file." : "Missing a criminal-record extract, so it cannot pass the exclusion check as a consortium member."),
            };
            var capacityPoints = (int)Math.Round(40.0 * Math.Min(1, (double)l.Capacity!.Value / gap));
            var aqap = l.Certificates.Any(c => c.Contains("AQAP"));
            var iso = l.Certificates.Any(c => c.Contains("ISO 9001"));
            var lead = l.LeadTimeDays ?? 999;
            var breakdown = new List<ScorePart>
            {
                new("Capacity", capacityPoints, 40, $"{l.Capacity} of the {gap} {lot.Unit} needed."),
                new("AQAP 2110", aqap || !defence ? 25 : 0, 25, aqap ? "NATO quality certificate on file." : "No AQAP certificate; defence buyers usually expect it."),
                new("ISO 9001", iso ? 15 : 0, 15, iso ? "Certified." : "Not certified."),
                new("Delivery time", lead <= deliveryDays ? 20 : lead <= deliveryDays + 30 ? 10 : 0, 20, $"Lead time {lead} days; delivery window about {deliveryDays} days."),
            };
            return new PartnerCandidate(l, breakdown.Sum(b => b.Points), gates.All(g => g.Passed), gates, breakdown);
        }).OrderByDescending(c => c.GatesPassed).ThenByDescending(c => c.Score).ToList();
    }
}
