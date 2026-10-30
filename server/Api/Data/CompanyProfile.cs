namespace Marketplace.Data;

/// <summary>A supplier's own evidence and products. Private: filtered by organisation in <see cref="AppDb"/>.</summary>
public class CompanyProfile
{
    public Guid OrgId { get; set; }
    public List<CompanyDocument> Documents { get; set; } = [];
    public List<CompanyProduct> Products { get; set; } = [];
    public List<OwnershipLink> Ownership { get; set; } = [];
}

/// <param name="Kind">Evidence kind used for matching, e.g. trade-register, criminal-record, iso9001, aqap2110, reference.</param>
public record CompanyDocument(
    string Id, string Kind, string Title, string Issuer, string Country, DateOnly? IssuedOn, DateOnly? ValidUntil, string? Note = null);

/// <param name="Keywords">Lower-case terms in any language that identify the product in lot titles.</param>
/// <param name="Capacity">Units the company can deliver within a typical tender delivery window.</param>
public record CompanyProduct(
    string Id, string Name, List<string> Keywords, string Cpv, int Capacity, string Unit, decimal UnitPriceEur, int LeadTimeDays);
