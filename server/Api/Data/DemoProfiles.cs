namespace Marketplace.Data;

/// <summary>Seed profiles for the demo companies. The bidder can make 480 of the 800 leaf-spring sets the demo tender asks for.</summary>
public static class DemoProfiles
{
    private static readonly List<string> LeafSpringKeywords =
        ["feder, blatt", "blattfeder", "leaf spring", "resor", "pružin", "listov", "pruzin"];

    /// <summary>Terms that identify assault rifles in lot titles (CPV 35321200, rifles) in Polish, English, German, Czech and Slovak.</summary>
    /// <summary>Terms that identify sniper and precision rifles in notice titles and specifications.</summary>
    private static readonly List<string> SniperKeywords = ["sniper", "karabin wyborowy", "wyborow", "scharfschützengewehr", "präzisionsgewehr", "odstřelovač", "ostrostrel", "precision rifle", "bolt-action"];

    private static readonly List<string> RifleKeywords = ["ak-47", "ak47", "broni", "broń", "kałasznikow", "kalashnikov", "karabinek", "karabin", "assault rifle", "rifle", "sturmgewehr", "gewehr", "útočná puška", "samopal"];

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
            OrgId = DemoOrgs.WeakPartner, Product = "AK-47 assault rifles, 7.62×39 mm, pieces", Buying = false, TargetPrice = 850, LimitPrice = 720,
            MinQuantity = 50, MaxQuantity = 300, DeliveryLimit = new(2027, 3, 7), AutonomyThreshold = 85, AutonomousValueCapEur = 250_000,
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
                new("leaf-spring", "Resory piórowe wielopiórowe (Blattfedern)", LeafSpringKeywords, "34320000", 480, "SE", 410m, 90,
                    "leaf-spring", "PL", "Multi-leaf suspension springs for military trucks and wheeled vehicles.",
                    [new("leaves", "Leaves", 4, "pcs"), new("material", "Material", Text: "51CrV4 spring steel"), new("origin", "Country of manufacture", Text: "PL")]),
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
                new("sk-lic", "export-licence", "Povolenie na obchodovanie s výrobkami obranného priemyslu", "Ministerstvo hospodárstva SR", "SK", new(2025, 6, 1), new(2030, 5, 31)),
            ],
            Ownership =
            [
                new("Váh Komponenty s.r.o.", "SK", 100, "partner"),
                new("Tianrun Europe Holding B.V.", "NL", 100, "parent"),
                new("Shandong Tianrun Auto Parts Co., Ltd.", "CN", 51, "ultimate"),
            ],
            Products =
            [
                // Published AK-47 figures: gas-operated, 7.62×39 mm, 30-round magazine, iron sights.
                new("ak-47", "AK-47 assault rifles (7.62×39 mm)", RifleKeywords, "35321200", 300, "EA", 780m, 120,
                    "assault-rifle", "SK", "Select-fire assault rifle in 7.62×39 mm with iron sights, for infantry and police.",
                    [
                        new("caliber_mm", "Caliber", 7.62m, "mm", "7.62×39 mm"),
                        new("mechanism", "Operating mechanism", Text: "gas-operated, rotating bolt"),
                        new("feed", "Feed", Text: "automatic, detachable box magazine"),
                        new("magazine_rounds", "Magazine capacity", 30, "rounds"),
                        new("barrel_mm", "Barrel length", 415, "mm"),
                        new("overall_mm", "Overall length", 880, "mm"),
                        new("rifling", "Rifling", Text: "4 grooves, right-hand, 1 turn in 240 mm"),
                        new("locking", "Locking", Text: "rotating bolt, 2 lugs"),
                        new("weight_bare_kg", "Weight without magazine", 3.47m, "kg"),
                        new("weight_mag_kg", "Weight of empty magazine", 0.33m, "kg"),
                        new("muzzle_mps", "Muzzle velocity", 715, "m/s"),
                        new("range_m", "Effective range", 400, "m"),
                        new("rate_rpm", "Rate of fire", 600, "rounds/min"),
                        new("scope_type", "Sight", Text: "iron sights"),
                        new("night_mount", "Night device mount", Text: "no"),
                        new("bipod", "Bipod", Text: "no"),
                        new("ammo_caliber", "Ammunition", Text: "7.62×39 mm"),
                        new("origin", "Country of manufacture", Text: "SK"),
                    ]),
                // Demo product: a bolt-action precision rifle so the sniper-rifle specification has a real contender.
                new("vk-308", "VK-308 bolt-action sniper rifle (7.62×51 mm NATO)", SniperKeywords, "35321200", 120, "EA", 6200m, 150,
                    "sniper-rifle", "SK", "Bolt-action precision rifle in 7.62×51 mm NATO with a straight day scope, bipod and Picatinny rail.",
                    [
                        new("caliber_mm", "Caliber", 7.62m, "mm", "7.62×51 mm NATO"),
                        new("mechanism", "Operating mechanism", Text: "bolt action"),
                        new("feed", "Feed", Text: "manual"),
                        new("magazine_rounds", "Magazine capacity", 10, "rounds"),
                        new("barrel_mm", "Barrel length", 610, "mm"),
                        new("overall_mm", "Overall length", 1150, "mm"),
                        new("rifling", "Rifling", Text: "4 grooves, right-hand, 1 turn in 279 mm (1:11 in)"),
                        new("locking", "Locking", Text: "rotating bolt, 3 lugs"),
                        new("weight_full_kg", "Weight with empty magazine and scope", 6.9m, "kg"),
                        new("weight_mag_kg", "Weight of empty magazine", 0.12m, "kg"),
                        new("weight_bare_kg", "Weight without magazine and scope", 5.4m, "kg"),
                        new("muzzle_mps", "Muzzle velocity", 830, "m/s"),
                        new("range_m", "Effective range", 1000, "m"),
                        new("accuracy_factory_moa", "Accuracy at factory range", 0.5m, "MOA"),
                        new("accuracy_max_moa", "Accuracy at max effective range", 0.75m, "MOA"),
                        new("scope_type", "Telescope sight", Text: "straight"),
                        new("night_mount", "Night device mount", Text: "yes, Picatinny rail"),
                        new("bipod", "Bipod", Text: "yes"),
                        new("life_weapon_rounds", "Weapon life", 20000, "rounds"),
                        new("life_barrel_rounds", "Barrel life", 8000, "rounds"),
                        new("ammo_caliber", "Ammunition", Text: "7.62×51 mm NATO"),
                        new("ammo_weight_g", "Cartridge weight", 25.4m, "g"),
                        new("ammo_origin", "Ammunition origin", Text: "CZ"),
                        new("origin", "Country of manufacture", Text: "SK"),
                    ]),
            ],
        },
    ];
}
