namespace Marketplace.Data;

/// <summary>Seed profiles for the demo companies. The bidder can make 480 of the 800 leaf-spring sets the demo tender asks for.</summary>
public static class DemoProfiles
{
    private static readonly List<string> LeafSpringKeywords =
        ["feder, blatt", "blattfeder", "leaf spring", "resor", "pružin", "listov", "pruzin"];

    /// <summary>Pre-approved mandates. Bidder buys up to €420/set; Moravia sells from €385; Váh from €400.</summary>
    public static IEnumerable<Mandate> Mandates() =>
    [
        new Mandate
        {
            OrgId = DemoOrgs.Bidder, Product = "Leaf springs (Blattfedern), sets", Buying = true, TargetPrice = 380, LimitPrice = 420,
            MinQuantity = 300, MaxQuantity = 400, DeliveryLimit = new(2027, 3, 15), AutonomyThreshold = 85, AutonomousValueCapEur = 200_000,
        },
        new Mandate
        {
            OrgId = DemoOrgs.Partner, Product = "Leaf springs, sets", Buying = false, TargetPrice = 430, LimitPrice = 385,
            MinQuantity = 100, MaxQuantity = 400, DeliveryLimit = new(2027, 2, 1), AutonomyThreshold = 85, AutonomousValueCapEur = 250_000,
        },
        new Mandate
        {
            OrgId = DemoOrgs.WeakPartner, Product = "Leaf springs, sets", Buying = false, TargetPrice = 445, LimitPrice = 400,
            MinQuantity = 50, MaxQuantity = 300, DeliveryLimit = new(2027, 3, 7), AutonomyThreshold = 85, AutonomousValueCapEur = 150_000,
        },
    ];

    public static IEnumerable<CompanyProfile> All() =>
    [
        new CompanyProfile
        {
            OrgId = DemoOrgs.Bidder,
            Documents =
            [
                new("pl-krs", "trade-register", "Odpis z KRS (KRS 0000412377)", "Krajowy Rejestr Sądowy", "PL", new(2026, 10, 2), null),
                new("pl-krk", "criminal-record", "Informacja z KRK o podmiocie zbiorowym", "Krajowy Rejestr Karny", "PL", new(2026, 9, 18), new(2027, 3, 18)),
                new("pl-zus", "social-security", "Zaświadczenie o niezaleganiu ze składkami", "ZUS", "PL", new(2026, 9, 25), new(2026, 12, 25)),
                new("pl-us", "tax", "Zaświadczenie o niezaleganiu w podatkach", "Urząd Skarbowy w Radomiu", "PL", new(2026, 9, 25), new(2026, 12, 25)),
                new("pl-iso", "iso9001", "ISO 9001:2015", "TÜV NORD Polska", "PL", new(2025, 4, 1), new(2028, 3, 31)),
                new("pl-aqap", "aqap2110", "AQAP 2110:2016", "Wojskowe Centrum Normalizacji, Jakości i Kodyfikacji", "PL", new(2024, 6, 1), new(2027, 5, 31)),
                new("pl-ref-1", "reference", "Referencje: 1 200 kpl. resorów dla 2. Regionalnej Bazy Logistycznej (2025)", "2. RBLog", "PL", new(2025, 12, 10), null),
                new("pl-ref-2", "reference", "Referencje: resory do Jelcz 442 dla Jelcz sp. z o.o. (2024)", "Jelcz sp. z o.o.", "PL", new(2024, 11, 5), null),
                new("pl-oc", "insurance", "Polisa OC działalności, suma 5 mln PLN", "PZU SA", "PL", new(2026, 1, 1), new(2026, 12, 31)),
            ],
            Products =
            [
                new("leaf-spring", "Resory piórowe wielopiórowe (Blattfedern)", LeafSpringKeywords, "34320000", 480, "SE", 410m, 90),
                new("bushing", "Tuleje i sworznie resorów", ["federbolzen", "buchse", "tuleja", "sworzeń"], "34320000", 3000, "EA", 18m, 45),
            ],
        },
        new CompanyProfile
        {
            OrgId = DemoOrgs.Partner,
            Documents =
            [
                new("cz-or", "trade-register", "Výpis z obchodního rejstříku", "Krajský soud v Ostravě", "CZ", new(2026, 10, 1), null),
                new("cz-rt", "criminal-record", "Výpis z evidence Rejstříku trestů právnických osob", "Rejstřík trestů", "CZ", new(2026, 9, 20), new(2027, 3, 20)),
                new("cz-iso", "iso9001", "ISO 9001:2015", "TÜV SÜD Czech", "CZ", new(2025, 2, 1), new(2028, 1, 31)),
                new("cz-aqap", "aqap2110", "AQAP 2110:2016", "Úřad pro obrannou standardizaci, katalogizaci a státní ověřování jakosti", "CZ", new(2025, 3, 1), new(2028, 2, 28)),
                new("cz-ref", "reference", "Reference: leaf springs for Tatra T815 (2025)", "Tatra Trucks a.s.", "CZ", new(2025, 9, 1), null),
            ],
            Products = [new("leaf-spring", "Listové pružiny (leaf springs)", LeafSpringKeywords, "34320000", 400, "SE", 395m, 75)],
        },
        new CompanyProfile
        {
            OrgId = DemoOrgs.WeakPartner,
            Documents =
            [
                new("sk-or", "trade-register", "Výpis z obchodného registra", "Okresný súd Trenčín", "SK", new(2026, 8, 10), null),
                new("sk-rt", "criminal-record", "Výpis z registra trestov právnických osôb", "Generálna prokuratúra SR", "SK", new(2026, 9, 1), new(2027, 3, 1)),
                new("sk-iso", "iso9001", "ISO 9001:2015", "SGS Slovakia", "SK", new(2024, 5, 1), new(2027, 4, 30)),
            ],
            Products = [new("leaf-spring", "Listové pružiny", LeafSpringKeywords, "34320000", 300, "SE", 430m, 110)],
        },
    ];
}
