namespace Marketplace.Data;

/// <summary>A product's bill of materials with component origins. Private to the company that owns it.</summary>
public class BillOfMaterials
{
    public Guid OrgId { get; set; }
    public string Product { get; set; } = "";
    public string Unit { get; set; } = "set";
    public List<BomComponent> Components { get; set; } = [];
}

/// <param name="Alternatives">Other qualified suppliers for the same component.</param>
/// <param name="Change">A supplier change prepared for this component, waiting for or past a person's signature.</param>
/// <param name="Source">Where the component was read from, when it came from a specification (rule 2: cite the source).</param>
public record BomComponent(string Id, string Name, string Supplier, string Country, decimal CostEur, List<BomAlternative> Alternatives,
    SupplierChange? Change = null, ComponentSource? Source = null);

/// <param name="Quote">The line the values were taken from, verbatim.</param>
public record ComponentSource(string Document, string Location, string Quote);

/// <summary>
/// Switching one component to another supplier, prepared by the compliance agent: drafts for both suppliers and the
/// reason, sent to a person to sign. The BOM only changes once that person signs (rule 5); sending stays with them.
/// </summary>
/// <param name="Stage">"awaiting" until a person signs, then "signed". A declined change is removed.</param>
public record SupplierChange(
    Guid Id, string FromSupplier, string FromCountry, decimal FromCostEur, BomAlternative To,
    decimal EuBefore, decimal EuAfter, List<ChangeDocument> Documents, string Stage,
    DateTimeOffset CreatedAt, string? DecidedBy = null, DateTimeOffset? DecidedAt = null, long? TelegramMessageId = null);

/// <param name="Kind">order (to the new supplier), termination (to the current one) or memo (internal reason).</param>
/// <param name="Body">Markdown.</param>
public record ChangeDocument(string Kind, string Title, string Recipient, string Body);

public record BomAlternative(string Supplier, string Country, decimal CostEur, string Note);

/// <summary>One link in a company's ownership chain, up to the ultimate owner.</summary>
public record OwnershipLink(string Entity, string Country, decimal Share, string Role);
