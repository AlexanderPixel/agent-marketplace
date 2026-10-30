using Marketplace.Data;
using Marketplace.Qualification;

namespace Marketplace.Documents;

/// <summary>
/// A certificate or official extract the tender asks for, as a copy to attach to the bid: Polish text and a German working
/// translation, like the agreement documents. Only the viewing company's own documents; the partner attaches its own.
/// </summary>
/// <param name="Need">What the tender asks for, e.g. "AQAP 2110 certificate".</param>
/// <param name="Why">The requirement that asks for it, or why it is listed.</param>
/// <param name="DemoRequirement">Not named in the TED notice; added for the demo as a typical requirement of a defence buyer.</param>
/// <param name="ProvidedBy">The consortium member whose certificate answers the requirement; null when nobody holds one.</param>
/// <param name="Equivalence">Why a certificate from the holder's country counts for the German requirement.</param>
public record TenderCertificate(
    string Id, string Kind, Dictionary<string, string> Title, Dictionary<string, string> Body, string Need, string Why,
    string Issuer, DateOnly? ValidUntil, bool ValidAtDeadline, bool DemoRequirement,
    CertificateHolder? ProvidedBy = null, string? Equivalence = null, string? Country = null);

public record CertificateHolder(Guid OrgId, string Name, string Country, string Role);

/// <summary>A company that can bring certificates to the bid: the bidder, or a partner whose deal for this tender closed.</summary>
public record ConsortiumMember(Organization Company, CompanyProfile Profile, string Role);

public static class TenderCertificates
{
    // Demo: the seven certificates a defence buyer usually asks of suppliers of vehicle parts, as BAAINBw words them. The demo
    // tender's TED notice names only the Wettbewerbsregister, so these stand in until the Vergabeunterlagen are parsed.
    // The consortium covers them together: the bidder holds three, Moravia (CZ) and Váh (SK) two each.
    public static readonly (string Kind, string Need)[] DemoRequired =
    [
        ("aqap2110", "QM-System nach AQAP 2110 (NATO)"),
        ("iso9001", "QM-System nach DIN EN ISO 9001:2015"),
        ("iso14001", "Umweltmanagement nach DIN EN ISO 14001:2015"),
        ("iatf16949", "QM-System für die Fahrzeugindustrie nach IATF 16949:2016"),
        ("iso3834", "Schweißtechnische Qualitätsanforderungen nach DIN EN ISO 3834-2"),
        ("iso45001", "Arbeitsschutzmanagement nach DIN ISO 45001:2018"),
        ("iso27001", "Informationssicherheit nach DIN EN ISO/IEC 27001 (Umgang mit VS-NfD-Unterlagen)"),
    ];

    /// <summary>National accreditation bodies; all sign the EA multilateral agreement, as Germany's DAkkS does.</summary>
    private static readonly Dictionary<string, string> Accreditation = new()
    {
        ["PL"] = "PCA (Polskie Centrum Akredytacji)", ["CZ"] = "ČIA (Český institut pro akreditaci)",
        ["SK"] = "SNAS (Slovenská národná akreditačná služba)", ["DE"] = "DAkkS",
    };

    /// <summary>References go into the wykaz dostaw and the wadium is paid, so neither is a copy to attach here.</summary>
    private static readonly HashSet<string> NotCertificates = ["reference", "vadium"];

    private static readonly Dictionary<string, (string Pl, string De, string StatementPl, string StatementDe, bool Scope)> Kinds = new()
    {
        ["aqap2110"] = ("Certyfikat AQAP 2110:2016", "Zertifikat AQAP 2110:2016",
            "Niniejszym zaświadcza się, że system zapewnienia jakości {0} spełnia wymagania NATO AQAP 2110:2016 w zakresie podanym powyżej.",
            "Hiermit wird bescheinigt, dass das Qualitätssicherungssystem von {0} im oben genannten Geltungsbereich die Anforderungen der NATO AQAP 2110:2016 erfüllt.", true),
        ["iso9001"] = ("Certyfikat systemu zarządzania jakością ISO 9001:2015", "Zertifikat Qualitätsmanagementsystem ISO 9001:2015",
            "Niniejszym zaświadcza się, że system zarządzania jakością {0} spełnia wymagania normy ISO 9001:2015 w zakresie podanym powyżej.",
            "Hiermit wird bescheinigt, dass das Qualitätsmanagementsystem von {0} im oben genannten Geltungsbereich die Anforderungen der ISO 9001:2015 erfüllt.", true),
        ["iso14001"] = ("Certyfikat systemu zarządzania środowiskowego ISO 14001:2015", "Zertifikat Umweltmanagementsystem ISO 14001:2015",
            "Niniejszym zaświadcza się, że system zarządzania środowiskowego {0} spełnia wymagania normy ISO 14001:2015 w zakresie podanym powyżej.",
            "Hiermit wird bescheinigt, dass das Umweltmanagementsystem von {0} im oben genannten Geltungsbereich die Anforderungen der ISO 14001:2015 erfüllt.", true),
        ["iatf16949"] = ("Certyfikat IATF 16949:2016 (motoryzacyjny system zarządzania jakością)", "Zertifikat IATF 16949:2016 (QM-System Automobilindustrie)",
            "Niniejszym zaświadcza się, że system zarządzania jakością {0} spełnia wymagania IATF 16949:2016 w połączeniu z ISO 9001:2015 w zakresie podanym powyżej.",
            "Hiermit wird bescheinigt, dass das Qualitätsmanagementsystem von {0} im oben genannten Geltungsbereich die Anforderungen der IATF 16949:2016 in Verbindung mit ISO 9001:2015 erfüllt.", true),
        ["iso3834"] = ("Certyfikat wymagań jakości w spawalnictwie EN ISO 3834-2", "Zertifikat Schweißtechnische Qualitätsanforderungen EN ISO 3834-2",
            "Niniejszym zaświadcza się, że {0} spełnia kompleksowe wymagania jakości dotyczące spawania materiałów metalowych wg EN ISO 3834-2 w zakresie podanym powyżej.",
            "Hiermit wird bescheinigt, dass {0} im oben genannten Geltungsbereich die umfassenden Qualitätsanforderungen für das Schmelzschweißen metallischer Werkstoffe nach EN ISO 3834-2 erfüllt.", true),
        ["iso45001"] = ("Certyfikat systemu zarządzania BHP ISO 45001:2018", "Zertifikat Arbeitsschutzmanagementsystem ISO 45001:2018",
            "Niniejszym zaświadcza się, że system zarządzania bezpieczeństwem i higieną pracy {0} spełnia wymagania normy ISO 45001:2018 w zakresie podanym powyżej.",
            "Hiermit wird bescheinigt, dass das Managementsystem für Sicherheit und Gesundheit bei der Arbeit von {0} im oben genannten Geltungsbereich die Anforderungen der ISO 45001:2018 erfüllt.", true),
        ["iso27001"] = ("Certyfikat systemu zarządzania bezpieczeństwem informacji ISO/IEC 27001:2022", "Zertifikat Informationssicherheits-Managementsystem ISO/IEC 27001:2022",
            "Niniejszym zaświadcza się, że system zarządzania bezpieczeństwem informacji {0} spełnia wymagania normy ISO/IEC 27001:2022 w zakresie podanym powyżej.",
            "Hiermit wird bescheinigt, dass das Informationssicherheits-Managementsystem von {0} im oben genannten Geltungsbereich die Anforderungen der ISO/IEC 27001:2022 erfüllt.", true),
        ["trade-register"] = ("Odpis z rejestru handlowego", "Handelsregisterauszug",
            "{0} jest wpisana do rejestru i nie znajduje się w likwidacji ani w upadłości.",
            "{0} ist im Register eingetragen und befindet sich weder in Liquidation noch in Insolvenz.", false),
        ["criminal-record"] = ("Informacja z rejestru karnego o podmiocie zbiorowym", "Auskunft aus dem Strafregister über eine juristische Person",
            "{0} nie figuruje w rejestrze karnym podmiotów zbiorowych.",
            "{0} ist im Strafregister für juristische Personen nicht eingetragen.", false),
        ["social-security"] = ("Zaświadczenie o niezaleganiu ze składkami na ubezpieczenia społeczne", "Unbedenklichkeitsbescheinigung des Sozialversicherungsträgers",
            "{0} nie zalega z opłacaniem składek na ubezpieczenia społeczne.",
            "{0} hat keine Rückstände bei den Sozialversicherungsbeiträgen.", false),
        ["tax"] = ("Zaświadczenie o niezaleganiu w podatkach", "Steuerliche Unbedenklichkeitsbescheinigung",
            "{0} nie zalega z płatnościami podatków.",
            "{0} hat keine Steuerrückstände.", false),
        ["insurance"] = ("Polisa ubezpieczenia odpowiedzialności cywilnej", "Betriebshaftpflichtversicherung (Versicherungsnachweis)",
            "{0} jest ubezpieczona od odpowiedzialności cywilnej w zakresie prowadzonej działalności.",
            "{0} ist für ihre Geschäftstätigkeit haftpflichtversichert.", false),
    };

    /// <param name="members">The bidder first, then partners with a closed deal for this tender. The demo certificates are
    /// covered by the first member that holds a valid one; a partner sees only the rows it covers itself.</param>
    public static List<TenderCertificate> For(Tender tender, CompanyProfile profile, Organization company, IReadOnlyList<ConsortiumMember>? members = null)
    {
        var deadline = DateOnly.FromDateTime((tender.Deadline ?? DateTimeOffset.UtcNow).UtcDateTime);
        var found = new Dictionary<string, TenderCertificate>();

        void Add(CompanyDocument doc, string need, string why, bool demo)
        {
            if (NotCertificates.Contains(doc.Kind) || found.ContainsKey(doc.Id)) return;
            var (pl, de) = Bodies(doc, profile, company);
            found[doc.Id] = new TenderCertificate(
                $"cert-{doc.Id}", doc.Kind, new() { ["pl"] = TitlePl(doc), ["de"] = TitleDe(doc) }, new() { ["pl"] = pl, ["de"] = de },
                need, why, doc.Issuer, doc.ValidUntil, doc.ValidUntil is null || doc.ValidUntil >= deadline, demo);
        }

        foreach (var req in QualificationCheck.Assess(tender, profile, company.Country).Requirements)
        {
            foreach (var check in req.Checks)
            {
                var why = check.Outcome == Outcome.Equivalent ? $"{req.Requirement.Text} (equivalent evidence; the buyer confirms)" : req.Requirement.Text;
                foreach (var doc in new[] { check.Evidence }.Concat(check.AlsoSubmit ?? []).OfType<CompanyDocument>()) Add(doc, check.Need, why, false);
            }
        }

        members ??= [new ConsortiumMember(company, profile, "bidder")];
        var viewerIsBidder = members[0].Company.Id == company.Id;
        foreach (var (kind, need) in DemoRequired)
        {
            var holder = members
                .Select(m => (Member: m, Doc: m.Profile.Documents.Where(d => d.Kind == kind && (d.ValidUntil is null || d.ValidUntil >= deadline))
                    .OrderByDescending(d => d.ValidUntil).FirstOrDefault()))
                .FirstOrDefault(x => x.Doc is not null);
            const string why = "Typical for a defence buyer of vehicle parts; not named in the TED notice (demo).";
            if (holder.Doc is null)
            {
                if (viewerIsBidder) found[$"missing-{kind}"] = Missing(kind, need, why);
                continue;
            }

            var (m, doc) = (holder.Member, holder.Doc);
            if (!viewerIsBidder && m.Company.Id != company.Id) continue;
            found.Remove(doc.Id); // a requirement-driven copy of the same document gets the consortium details instead
            var (pl, de) = Bodies(doc, m.Profile, m.Company);
            found[doc.Id] = new TenderCertificate(
                $"cert-{doc.Id}", doc.Kind, new() { ["pl"] = TitlePl(doc), ["de"] = TitleDe(doc) }, new() { ["pl"] = pl, ["de"] = de },
                need, why, doc.Issuer, doc.ValidUntil, true, true,
                new CertificateHolder(m.Company.Id, m.Company.Name, m.Company.Country, m.Role), Equivalent(doc, tender.BuyerCountry), doc.Country);
        }

        return [.. found.Values];
    }

    /// <summary>Why a certificate issued in another member state counts for the buyer.</summary>
    private static string Equivalent(CompanyDocument doc, string buyerCountry)
    {
        if (doc.Country == buyerCountry) return "Issued in the buyer's country.";
        if (doc.Kind == "aqap2110")
            return $"Issued by {doc.Issuer}, the {doc.Country} national authority for government quality assurance. NATO members recognise each " +
                   "other's AQAP certification (STANAG 4107), so BAAINBw accepts it like a German one.";
        var body = Accreditation.GetValueOrDefault(doc.Country, "the national accreditation body");
        return $"Issued by {doc.Issuer}, accredited by {body}. {body.Split(' ')[0]} and Germany's DAkkS both sign the EA multilateral agreement, " +
               "so the buyer must accept it as equivalent to a DAkkS-accredited certificate (§ 49 VgV). A German translation is attached.";
    }

    private static TenderCertificate Missing(string kind, string need, string why)
    {
        var title = Kinds.TryGetValue(kind, out var k) ? k : (Pl: need, De: need, StatementPl: "", StatementDe: "", Scope: false);
        return new TenderCertificate($"missing-{kind}", kind, new() { ["pl"] = title.Pl, ["de"] = title.De },
            new()
            {
                ["pl"] = $"# {title.Pl}\n\nNikt w konsorcjum nie ma ważnego certyfikatu. Bez niego oferta nie spełnia wymagania: {need}.",
                ["de"] = $"# {title.De}\n\nKein Konsortialmitglied hat ein gültiges Zertifikat. Ohne es erfüllt das Angebot die Anforderung nicht: {need}.",
            },
            need, why, "", null, false, true);
    }

    private static string TitlePl(CompanyDocument doc) => Kinds.TryGetValue(doc.Kind, out var k) ? k.Pl : doc.Title;
    private static string TitleDe(CompanyDocument doc) => Kinds.TryGetValue(doc.Kind, out var k) ? k.De : doc.Title;

    private static (string Pl, string De) Bodies(CompanyDocument doc, CompanyProfile profile, Organization company)
    {
        var kind = Kinds.GetValueOrDefault(doc.Kind);
        var number = $"{doc.Kind.Split('-')[0].ToUpperInvariant()}/{doc.IssuedOn?.Year ?? 2026}/{doc.Id.Sum(c => c) % 9000 + 1000}";
        var products = string.Join(", ", profile.Products.Select(p => p.Name));
        static string Date(DateOnly? d, string none) => d?.ToString("dd.MM.yyyy") ?? none;

        var pl = $"""
            > Demo: przykładowa treść dokumentu, nie prawdziwy certyfikat. Do oferty załącz skan oryginału.

            # {TitlePl(doc)}

            - **Dokument:** {doc.Title}
            - **Numer:** {number}
            - **Posiadacz:** {company.Name}, {company.City} ({company.Country})
            - **Wystawca:** {doc.Issuer} ({doc.Country})
            - **Data wydania:** {Date(doc.IssuedOn, "—")}
            - **Ważny do:** {Date(doc.ValidUntil, "bezterminowo")}
            {(kind.Scope ? $"- **Zakres:** projektowanie, produkcja i dostawy: {products}\n" : "")}
            {(kind.StatementPl is null ? "" : string.Format(kind.StatementPl, $"**{company.Name}**"))}
            """;
        var de = $"""
            > Demo: Beispielinhalt, kein echtes Zertifikat. Arbeitsübersetzung, nicht beglaubigt. Dem Angebot ist ein Scan des Originals beizufügen.

            # {TitleDe(doc)}

            - **Dokument:** {doc.Title}
            - **Nummer:** {number}
            - **Inhaber:** {company.Name}, {company.City} ({company.Country})
            - **Aussteller:** {doc.Issuer} ({doc.Country})
            - **Ausstellungsdatum:** {Date(doc.IssuedOn, "—")}
            - **Gültig bis:** {Date(doc.ValidUntil, "unbefristet")}
            {(kind.Scope ? $"- **Geltungsbereich:** Entwicklung, Herstellung und Lieferung: {products}\n" : "")}
            {(kind.StatementDe is null ? "" : string.Format(kind.StatementDe, $"**{company.Name}**"))}
            """;
        return (pl, de);
    }
}
