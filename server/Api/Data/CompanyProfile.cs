namespace Marketplace.Data;

/// <summary>A supplier's own evidence and products. Private: filtered by organisation in <see cref="AppDb"/>.</summary>
public class CompanyProfile
{
    public Guid OrgId { get; set; }
    public List<CompanyDocument> Documents { get; set; } = [];
    public List<CompanyProduct> Products { get; set; } = [];
    public List<OwnershipLink> Ownership { get; set; } = [];
    public List<StaffMember> Staff { get; set; } = [];
}

/// <summary>An employee whose CV can go into a wykaz osób. Credentials are owned by the person, not the company.</summary>
public record StaffMember(
    string Id, string Name, string Role, int YearsExperience, string Education, List<string> Languages,
    List<StaffCredential> Credentials, List<string> Projects, string Availability);

public record StaffCredential(string Title, string Issuer, DateOnly? ValidUntil);

/// <param name="Kind">Evidence kind used for matching, e.g. trade-register, criminal-record, iso9001, aqap2110, reference.</param>
/// <param name="Delivery">For references only: what was delivered, so experience thresholds can be checked.</param>
public record CompanyDocument(
    string Id, string Kind, string Title, string Issuer, string Country, DateOnly? IssuedOn, DateOnly? ValidUntil, string? Note = null,
    DeliveryRecord? Delivery = null);

/// <summary>
/// A completed (or contracted) delivery behind a reference. Null fields are unknown, never guessed: a threshold that
/// needs an unknown value stays unclear (rule 7).
/// </summary>
/// <param name="Completed">When the delivery is (or will be) done. A reference only counts once this date has passed.</param>
/// <param name="Source">Where the record came from, e.g. "Won tender TED 723644-2026" or "Entered by hand".</param>
public record DeliveryRecord(
    string Buyer, string Subject, int? Quantity, string? Unit, decimal? ValueEur, DateOnly Completed, string? TenderRef, string Source);

/// <param name="Keywords">Lower-case terms in any language that identify the product in lot titles.</param>
/// <param name="Capacity">Units the company can deliver within a typical tender delivery window.</param>
/// <param name="Category">What kind of product it is, matched against buyers' specifications (e.g. sniper-rifle, assault-rifle).</param>
/// <param name="Origin">Country of manufacture (ISO 3166-1 alpha-2).</param>
/// <param name="Specs">The technical sheet: measured values and plain-text facts, keyed so specifications can be checked.</param>
public record CompanyProduct(
    string Id, string Name, List<string> Keywords, string Cpv, int Capacity, string Unit, decimal UnitPriceEur, int LeadTimeDays,
    string? Category = null, string? Origin = null, string? Summary = null, List<SpecValue>? Specs = null);

/// <summary>One line of a product's technical sheet: a number with its unit, or a text value (yes/no, a name, a mechanism).</summary>
public record SpecValue(string Key, string Label, decimal? Number = null, string? Unit = null, string? Text = null);
