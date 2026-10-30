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
public record TenderCertificate(
    string Id, string Kind, Dictionary<string, string> Title, Dictionary<string, string> Body, string Need, string Why,
    string Issuer, DateOnly? ValidUntil, bool ValidAtDeadline, bool DemoRequirement);

public static class TenderCertificates
{
    // Demo: quality certificates a defence buyer usually asks of suppliers of vehicle parts. The demo tender's TED notice names
    // only the Wettbewerbsregister, so these stand in until the Vergabeunterlagen are parsed for certificate requirements.
    private static readonly (string Kind, string Need)[] DemoRequired = [("aqap2110", "AQAP 2110 certificate"), ("iso9001", "ISO 9001 certificate")];

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

    public static List<TenderCertificate> For(Tender tender, CompanyProfile profile, Organization company)
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

        foreach (var (kind, need) in DemoRequired)
        {
            var doc = profile.Documents.Where(d => d.Kind == kind).OrderByDescending(d => d.ValidUntil).FirstOrDefault();
            if (doc is not null) Add(doc, need, "Typical for a defence buyer of vehicle parts; not named in the TED notice (demo).", true);
        }

        return [.. found.Values];
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
