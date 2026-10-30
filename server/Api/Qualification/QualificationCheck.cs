using System.Text.RegularExpressions;
using Marketplace.Data;
using Marketplace.Ecertis;

namespace Marketplace.Qualification;

public enum Outcome { Met, Equivalent, Missing, Unclear }

/// <param name="Evidence">The company document that answers it, cited by id and title.</param>
public record EvidenceCheck(
    string Need, Outcome Outcome, string Reason, CompanyDocument? Evidence,
    EcertisMatch? Ecertis = null, Clarification? Clarification = null, List<CompanyDocument>? AlsoSubmit = null);

/// <summary>A question to the buyer, drafted in the notice language with a translation for the bidder. A human sends it.</summary>
public record Clarification(string Language, string Text, string Translation);

public record RequirementCheck(TenderRequirement Requirement, Outcome Outcome, List<EvidenceCheck> Checks);

public enum LotFit { Full, Partial, NotOffered }

public record LotCheck(TenderLot Lot, LotFit Fit, CompanyProduct? Product, int? Capacity, int? Gap, string Reason);

/// <summary>can-supply, partner-needed (in range but short on quantity), not-yet (a red requirement), out-of-range.</summary>
public record Assessment(List<RequirementCheck> Requirements, List<LotCheck> Lots, int NotOffered, string Verdict, int Red, int Amber, List<LotCheck> Gaps);

/// <summary>
/// Rule-based check of tender requirements against a company's own documents and capacity. Conservative by design
/// (rule 7): anything we cannot map to a document is Unclear, which counts as red.
/// </summary>
public static partial class QualificationCheck
{
    private record Rule(Regex Pattern, string Kind, string Need, Dictionary<string, string>? NationalNames = null);

    private static Regex R(string p) => new(p, RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    /// <summary>Order matters: the more specific register rules come first.</summary>
    private static readonly Rule[] Rules =
    [
        new(R(@"wettbewerbsregister"), "competition-register", "Wettbewerbsregister extract (German register of excluded companies)"),
        new(R(@"handelsregister|trade register|slc-suit-reg-trade|krajow\w* rejestr\w* sądow|\bKRS\b|CEIDG|obchodn\w* rejst"), "trade-register",
            "Trade register extract", new() { ["DE"] = "Handelsregisterauszug", ["PL"] = "odpis z KRS", ["CZ"] = "výpis z obchodního rejstříku" }),
        new(R(@"rejestr\w* karn|\bKRK\b|criminal record|führungszeugnis|strafregister|rejstřík trestů"), "criminal-record", "Criminal record extract"),
        new(R(@"\bZUS\b|składek na ubezpieczen|sozialversicherung"), "social-security", "Social security clearance"),
        new(R(@"urzęd\w* skarbow\w*|zaległości podatkow|niezaleganiu.*podatk|steuer"), "tax", "Tax clearance"),
        new(R(@"AQAP"), "aqap2110", "AQAP 2110 certificate"),
        new(R(@"ISO\s*9001"), "iso9001", "ISO 9001 certificate"),
        new(R(@"wykaz\w* dostaw|referenc|slc-abil-ref|należyt\w* wykonan"), "reference", "References for similar deliveries"),
        new(R(@"ubezpieczon\w* od odpowiedzialności|polis\w* OC|slc-stand-fin-insur|haftpflicht"), "insurance", "Liability insurance"),
        new(R(@"^Wadium"), "vadium", "Bid security (wadium)"),
    ];

    public static Assessment Assess(Tender tender, CompanyProfile profile, string companyCountry)
    {
        var requirements = tender.Requirements.Select(r => Check(r, tender.BuyerCountry, tender.Deadline, profile, companyCountry)).ToList();
        var lots = tender.Lots.Select(l => CheckLot(l, profile)).ToList();
        var offered = lots.Where(l => l.Fit != LotFit.NotOffered).ToList();
        var red = requirements.Count(r => r.Outcome is Outcome.Missing or Outcome.Unclear);
        var amber = requirements.Count(r => r.Outcome == Outcome.Equivalent);
        var gaps = offered.Where(l => l.Gap > 0).ToList();
        var inRange = tender.Lots.Count == 0
            ? profile.Products.Any(p => p.Cpv[..3] == tender.Cpv[..Math.Min(3, tender.Cpv.Length)] || p.Keywords.Any(k => tender.Title.Contains(k, StringComparison.OrdinalIgnoreCase)))
            : offered.Count > 0;
        var verdict = !inRange ? "out-of-range" : red > 0 ? "not-yet" : gaps.Count > 0 ? "partner-needed" : "can-supply";
        return new Assessment(requirements, offered, lots.Count - offered.Count, verdict, red, amber, gaps);
    }

    public static RequirementCheck Check(TenderRequirement req, string buyerCountry, DateTimeOffset? deadline, CompanyProfile profile, string companyCountry)
    {
        var text = $"{req.Code} {req.Text}";
        var checks = new List<EvidenceCheck>();
        var kinds = new HashSet<string>();
        foreach (var rule in Rules)
        {
            if (!rule.Pattern.IsMatch(text) || !kinds.Add(rule.Kind)) continue;
            if (rule.Kind == "competition-register") kinds.Add("trade-register"); // "…register" also matches the trade-register rule
            checks.Add(rule.Kind == "reference"
                ? ExperienceCheck.Evaluate(rule.Need, req.Text, deadline, profile)
                : Evaluate(rule, buyerCountry, deadline, profile, companyCountry));
        }

        if (checks.Count == 0)
        {
            checks.Add(new EvidenceCheck("Requirement not recognised", Outcome.Unclear,
                "This requirement could not be mapped to a document type, so it is not counted as met. Check it in the tender documents.", null));
        }

        var outcome = checks.Max(c => c.Outcome == Outcome.Met ? 0 : c.Outcome == Outcome.Equivalent ? 1 : 2) switch
        {
            0 => Outcome.Met,
            1 => Outcome.Equivalent,
            _ => checks.Any(c => c.Outcome == Outcome.Missing) ? Outcome.Missing : Outcome.Unclear,
        };
        return new RequirementCheck(req, outcome, checks);
    }

    private static EvidenceCheck Evaluate(Rule rule, string buyerCountry, DateTimeOffset? deadline, CompanyProfile profile, string companyCountry)
    {
        var at = DateOnly.FromDateTime((deadline ?? DateTimeOffset.UtcNow).UtcDateTime);
        var docs = profile.Documents.Where(d => d.Kind == rule.Kind).ToList();
        var valid = docs.FirstOrDefault(d => d.ValidUntil is null || d.ValidUntil >= at);

        if (rule.Kind == "competition-register")
        {
            if (companyCountry == "DE" && valid is not null)
            {
                return new EvidenceCheck(rule.Need, Outcome.Met, "Your extract is on file.", valid);
            }

            // Only Germany runs this register. eCertis has no entry for it; the closest national evidence is the
            // criminal-record extract (eCertis: participation in a criminal organisation) plus the trade register.
            var krk = profile.Documents.FirstOrDefault(d => d.Kind == "criminal-record" && (d.ValidUntil is null || d.ValidUntil >= at));
            var register = profile.Documents.FirstOrDefault(d => d.Kind == "trade-register");
            if (krk is null)
            {
                return new EvidenceCheck(rule.Need, Outcome.Unclear,
                    $"German register; companies from {companyCountry} cannot get an extract, and you hold no criminal-record extract to offer instead.", null);
            }

            return new EvidenceCheck(rule.Need, Outcome.Equivalent,
                $"German register: companies from {companyCountry} are not in it and cannot get an extract. eCertis has no entry for it, " +
                $"so we propose the closest {companyCountry} evidence, {krk.Title}" + (register is null ? "" : $" together with {register.Title}") +
                ", and ask the buyer to confirm before the deadline. Amber until the buyer answers.",
                krk,
                EcertisCatalog.Match(krk, buyerCountry, "eCertis has no Wettbewerbsregister entry; this is the closest criterion it covers."),
                WettbewerbsregisterQuestion(krk, register, companyCountry),
                register is null ? null : [register]);
        }

        if (rule.Kind == "vadium")
        {
            return new EvidenceCheck(rule.Need, Outcome.Unclear, "A bank or insurance guarantee has to be arranged for this tender; none is on file.", null);
        }

        if (valid is null)
        {
            return docs.Count > 0
                ? new EvidenceCheck(rule.Need, Outcome.Missing, $"Your {docs[0].Title} expires on {docs[0].ValidUntil:yyyy-MM-dd}, before the deadline.", docs[0])
                : new EvidenceCheck(rule.Need, Outcome.Missing, "No such document in your profile.", null);
        }

        if (rule.NationalNames is not null && valid.Country != buyerCountry)
        {
            var asked = rule.NationalNames.GetValueOrDefault(buyerCountry, rule.Need);
            var match = EcertisCatalog.Match(valid, buyerCountry);
            return new EvidenceCheck(rule.Need, Outcome.Equivalent,
                match?.Matched is { } m
                    ? $"The buyer asks for a {asked}. eCertis lists \"{m.Name}\" as the {valid.Country} evidence for the same criterion, and you hold it."
                    : $"The buyer asks for a {asked}; you hold the {valid.Country} counterpart. Submit it as equivalent evidence.",
                valid, match);
        }

        return new EvidenceCheck(rule.Need, Outcome.Met, "Valid on the submission deadline.", valid);
    }

    private static Clarification WettbewerbsregisterQuestion(CompanyDocument krk, CompanyDocument? register, string country)
    {
        var docsDe = register is null ? $"„{krk.Title}“" : $"„{krk.Title}“ und „{register.Title}“";
        var docsPl = register is null ? krk.Title : $"{krk.Title} oraz {register.Title}";
        return new Clarification("de",
            $"Sehr geehrte Damen und Herren, als Bieter mit Sitz in {CountryDe(country)} sind wir nicht im deutschen Wettbewerbsregister erfasst " +
            $"und können keine Wettbewerbsregisterauskunft vorlegen. Wir beabsichtigen, als gleichwertigen Nachweis {docsDe} " +
            "(jeweils aktuell, mit beglaubigter deutscher Übersetzung) einzureichen. Bitte bestätigen Sie, dass diese Nachweise akzeptiert werden. " +
            "Mit freundlichen Grüßen",
            $"Szanowni Państwo, jako wykonawca z siedzibą w kraju {country} nie figurujemy w niemieckim Wettbewerbsregister i nie możemy " +
            $"przedstawić jego wyciągu. Jako dokument równoważny zamierzamy złożyć: {docsPl} (aktualne, z uwierzytelnionym tłumaczeniem " +
            "na język niemiecki). Prosimy o potwierdzenie, że te dokumenty zostaną zaakceptowane. Z poważaniem");
    }

    private static string CountryDe(string c) => c switch { "PL" => "Polen", "CZ" => "Tschechien", "SK" => "der Slowakei", _ => c };

    public static LotCheck CheckLot(TenderLot lot, CompanyProfile profile)
    {
        var title = lot.Title.ToLowerInvariant();
        var product = profile.Products.FirstOrDefault(p => p.Keywords.Any(k => title.Contains(k)));
        if (product is null)
        {
            return new LotCheck(lot, LotFit.NotOffered, null, null, null, "Not in your product range.");
        }

        if (lot.Quantity is not { } qty)
        {
            return new LotCheck(lot, LotFit.Partial, product, product.Capacity, null,
                "In your range, but the notice does not state a quantity. Check the tender documents.");
        }

        var needed = (int)qty;
        return product.Capacity >= needed
            ? new LotCheck(lot, LotFit.Full, product, product.Capacity, 0, $"You can deliver all {needed}.")
            : new LotCheck(lot, LotFit.Partial, product, product.Capacity, needed - product.Capacity,
                $"You can deliver {product.Capacity} of {needed}; {needed - product.Capacity} short.");
    }
}
