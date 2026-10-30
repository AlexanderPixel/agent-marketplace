namespace Marketplace.Data;

/// <summary>
/// Extra demo data for the bidder: three more product lines (and the references behind them) so more of the loaded
/// tenders fall in its range. Removable: every item's id starts with <see cref="Prefix"/>. Turn it off with
/// <c>DemoData:ExtendedProfile=false</c>, remove it from a running demo with <c>DELETE /api/profile/extended</c>,
/// or delete this file and its two call sites (DemoSeeder, ProfileEndpoints).
/// None of the keywords match a lot of the P0 demo tender (723644-2026), so its leaf-spring gap is unchanged.
/// </summary>
public static class DemoProfileExtras
{
    public const string Prefix = "ext-";

    public static readonly Guid OrgId = DemoOrgs.Bidder;

    public static List<CompanyProduct> Products() =>
    [
        new($"{Prefix}vehicle-parts", "Części zamienne do pojazdów wojskowych (zawieszenie, układ jezdny)",
            ["części do pojazdów", "części zamienne do pojazdów", "spare parts for military vehicles"], "35400000", 2000, "EA", 140m, 60),
        new($"{Prefix}fire-bodies", "Zabudowy pożarnicze i ratownicze na podwoziach ciężarowych",
            ["ratowniczo-gaśnicz", "ratowniczo – gaśnicz", "wóz strażacki", "wozy strażackie"], "34144210", 6, "EA", 285_000m, 210),
        new($"{Prefix}rescue-equipment", "Sprzęt ratowniczy i wyposażenie ochrony ludności (OLiOC)",
            ["ochrony ludności", "olioc", "obrony cywilnej", "sprzęt ratownicz"], "35110000", 1500, "EA", 320m, 45),
    ];

    public static List<CompanyDocument> Documents() =>
    [
        new($"{Prefix}ref-fire", "reference", "Referencje: 4 zabudowy GBA dla Komendy Wojewódzkiej PSP w Radomiu (2025)", "KW PSP Radom", "PL",
            new(2025, 9, 15), null,
            Delivery: new("Komenda Wojewódzka PSP w Radomiu", "Zabudowy ratowniczo-gaśnicze GBA", 4, "EA", 1_120_000m, new(2025, 8, 29), null, "Reference letter")),
        new($"{Prefix}ref-olioc", "reference", "Referencje: sprzęt OLiOC dla Starostwa Powiatowego w Zwoleniu (2026)", "Starostwo Powiatowe w Zwoleniu", "PL",
            new(2026, 6, 20), null,
            Delivery: new("Starostwo Powiatowe w Zwoleniu", "Sprzęt ratowniczy i wyposażenie ochrony ludności", 640, "EA", 186_000m, new(2026, 6, 5), null, "Reference letter")),
    ];

    public static void Apply(CompanyProfile profile)
    {
        Remove(profile);
        profile.Products = [.. profile.Products, .. Products()];
        profile.Documents = [.. profile.Documents, .. Documents()];
    }

    /// <summary>Removes only the extension's items; anything else in the profile is left as it is.</summary>
    public static void Remove(CompanyProfile profile)
    {
        profile.Products = profile.Products.Where(p => !p.Id.StartsWith(Prefix)).ToList();
        profile.Documents = profile.Documents.Where(d => !d.Id.StartsWith(Prefix)).ToList();
    }
}
