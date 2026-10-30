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
public record BomComponent(string Id, string Name, string Supplier, string Country, decimal CostEur, List<BomAlternative> Alternatives);

public record BomAlternative(string Supplier, string Country, decimal CostEur, string Note);

/// <summary>One link in a company's ownership chain, up to the ultimate owner.</summary>
public record OwnershipLink(string Entity, string Country, decimal Share, string Role);
