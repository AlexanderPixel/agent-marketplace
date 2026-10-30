namespace Marketplace.Data;

/// <summary>Seeded companies. Fixed ids so the UI and later seed data can refer to them.</summary>
public static class DemoOrgs
{
    public static readonly Guid Bidder = Guid.Parse("11111111-0000-0000-0000-000000000001");
    public static readonly Guid Partner = Guid.Parse("11111111-0000-0000-0000-000000000002");
    public static readonly Guid WeakPartner = Guid.Parse("11111111-0000-0000-0000-000000000003");

    public static IEnumerable<Organization> All() =>
    [
        new Organization
        {
            Id = Bidder, Name = "Sprężyny Mazowsze sp. z o.o.", Country = "PL", City = "Radom", Kind = OrgKind.Supplier, Language = "pl",
            Description = "Producent resorów piórowych i elementów zawieszenia do pojazdów ciężarowych i wojskowych.",
        },
        new Organization
        {
            Id = Partner, Name = "Moravia Pružiny s.r.o.", Country = "CZ", City = "Přerov", Kind = OrgKind.Supplier, Language = "en",
            Description = "Leaf springs and suspension components for trucks and military vehicles.",
        },
        new Organization
        {
            Id = WeakPartner, Name = "Váh Komponenty s.r.o.", Country = "SK", City = "Trenčín", Kind = OrgKind.Supplier, Language = "en",
            Description = "Machined parts and springs for commercial vehicles.",
        },
    ];
}
