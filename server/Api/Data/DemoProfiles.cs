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

    /// <summary>Cost per leaf-spring set: 58% from the EU, so short of the 65% SAFE/EDIP rule until one supplier is swapped.</summary>
    public static BillOfMaterials Bom() => new()
    {
        OrgId = DemoOrgs.Bidder,
        Product = "Resor piórowy 4-piórowy (FEDER, BLATT-, GESCHICHTET), 1 komplet",
        Components =
        [
            new("steel", "Spring steel flat bar 51CrV4", "Ovako Hofors AB", "SE", 83, []),
            new("eye-forging", "Spring eye forging blanks", "Bharat Forge Ltd", "IN", 44,
                [new("Kuźnia Jawor S.A.", "PL", 47, "Qualified 2024; 3 weeks lead time")]),
            new("bushing", "Rubber-metal bushings", "Ningbo Tuopu Group", "CN", 18,
                [new("Trelleborg Antivibration Solutions", "SE", 19, "Qualified for military vehicles; same drawing")]),
            new("u-bolts", "U-bolts M22 with nuts", "Kuźnia Jawor S.A.", "PL", 16, []),
            new("coating", "Zinc-flake corrosion coating", "NOF Metal Coatings North America", "US", 9,
                [new("Dörken Coatings GmbH", "DE", 9.5m, "Same spec class; DIN EN ISO 10683")]),
            new("bronze", "Bronze eye bushings", "KS Gleitlager GmbH", "DE", 8, []),
            new("interleaf", "Anti-friction interleaf pads (PTFE)", "Daikin Industries", "JP", 7,
                [new("Röchling Industrial SE", "DE", 7.8m, "UHMW-PE, PFAS-free")]),
            new("bolt", "Centre bolt M16 with nut", "Norm Cıvata", "TR", 6, []),
            new("clips", "Rebound clips", "Kuźnia Jawor S.A.", "PL", 5, []),
            new("packaging", "Packaging and pallet", "Drewpal sp. z o.o.", "PL", 4, []),
        ],
    };

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
                new("pl-ref-1", "reference", "Referencje: 1 200 kpl. resorów dla 2. Regionalnej Bazy Logistycznej (2025)", "2. RBLog", "PL", new(2025, 12, 10), null,
                    Delivery: new("2. Regionalna Baza Logistyczna", "Resory piórowe", 1200, "SE", null, new(2025, 11, 30), null, "Reference letter")),
                new("pl-ref-2", "reference", "Referencje: resory do Jelcz 442 dla Jelcz sp. z o.o. (2024)", "Jelcz sp. z o.o.", "PL", new(2024, 11, 5), null,
                    Delivery: new("Jelcz sp. z o.o.", "Resory do Jelcz 442", null, null, null, new(2024, 10, 31), null, "Reference letter")),
                new("pl-oc", "insurance", "Polisa OC działalności, suma 5 mln PLN", "PZU SA", "PL", new(2026, 1, 1), new(2026, 12, 31)),
            ],
            Ownership =
            [
                new("Sprężyny Mazowsze sp. z o.o.", "PL", 100, "bidder"),
                new("Mazowsze Industrial Holding S.A.", "PL", 100, "parent"),
                new("Jan i Anna Wróbel (osoby fizyczne)", "PL", 82, "ultimate"),
            ],
            Staff =
            [
                new("s-kowalczyk", "mgr inż. Tomasz Kowalczyk", "Kierownik kontraktu", 18, "Politechnika Warszawska, Mechanika i Budowa Maszyn",
                    ["pl", "en", "de"],
                    [new("Pełnomocnik ds. AQAP 2110", "WCNJiK", new(2027, 5, 31)), new("PRINCE2 Practitioner", "PeopleCert", new(2027, 9, 30)),
                     new("Poświadczenie bezpieczeństwa – Poufne", "ABW", new(2029, 2, 28))],
                    ["Dostawy resorów dla 2. RBLog, 1 200 kpl. (2025)", "Program resorów Jelcz 442 (2024)"], "od 2026-11-15"),
                new("s-nowak", "inż. Katarzyna Nowak", "Pełnomocnik ds. jakości", 12, "Politechnika Radomska, Inżynieria Materiałowa",
                    ["pl", "en"],
                    [new("Auditor wiodący ISO 9001", "TÜV NORD Akademie", new(2028, 3, 31)), new("Audytor AQAP 2110", "WCNJiK", new(2027, 5, 31))],
                    ["Wdrożenie AQAP 2110 (2024)", "Kontrola jakości resorów dla Jelcz (2024)"], "od 2026-11-15"),
                new("s-zielinski", "Marek Zieliński", "Technolog obróbki cieplnej", 21, "Technikum Mechaniczne w Radomiu",
                    ["pl"],
                    [new("Uprawnienia UDT – obsługa pieców hartowniczych", "UDT", new(2028, 6, 30))],
                    ["Hartowanie resorów 51CrV4 dla 2. RBLog (2025)"], "od 2026-11-15"),
                new("s-weber", "Anna Weber", "Specjalista ds. eksportu i przetargów", 7, "Uniwersytet Ekonomiczny w Krakowie",
                    ["pl", "de", "en"],
                    [new("Goethe-Zertifikat C1", "Goethe-Institut", null), new("Kontrola obrotu towarami o znaczeniu strategicznym", "MRiT", new(2026, 10, 1))],
                    ["Oferty dla Bundeswehr (BAAINBw) 2025"], "od 2026-11-01"),
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
                new("cz-ref", "reference", "Reference: leaf springs for Tatra T815 (2025)", "Tatra Trucks a.s.", "CZ", new(2025, 9, 1), null,
                    Delivery: new("Tatra Trucks a.s.", "Leaf springs for Tatra T815", null, null, null, new(2025, 8, 31), null, "Reference letter")),
            ],
            Ownership =
            [
                new("Moravia Pružiny s.r.o.", "CZ", 100, "partner"),
                new("Moravia Holding a.s.", "CZ", 100, "parent"),
                new("Petr Novák (natural person)", "CZ", 64, "ultimate"),
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
            Ownership =
            [
                new("Váh Komponenty s.r.o.", "SK", 100, "partner"),
                new("Tianrun Europe Holding B.V.", "NL", 100, "parent"),
                new("Shandong Tianrun Auto Parts Co., Ltd.", "CN", 51, "ultimate"),
            ],
            Products = [new("leaf-spring", "Listové pružiny", LeafSpringKeywords, "34320000", 300, "SE", 430m, 110)],
        },
    ];
}
