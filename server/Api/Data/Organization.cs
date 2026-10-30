namespace Marketplace.Data;

public enum OrgKind
{
    Supplier,
    Buyer,
}

/// <summary>A company or public buyer using the platform. Every private row belongs to one organisation.</summary>
public class Organization
{
    public Guid Id { get; set; }
    public required string Name { get; set; }

    /// <summary>ISO 3166-1 alpha-2, e.g. PL, CZ, DE.</summary>
    public required string Country { get; set; }

    public required string City { get; set; }
    public OrgKind Kind { get; set; }
    public string Description { get; set; } = "";

    /// <summary>UI language the organisation's users default to (ISO 639-1).</summary>
    public string Language { get; set; } = "pl";
}
