namespace Marketplace.Tenders;

/// <summary>ISO 3166/639 three-letter to two-letter codes for the EU/EEA, as used by eForms.</summary>
public static class Codes
{
    private static readonly Dictionary<string, string> Countries = new(StringComparer.OrdinalIgnoreCase)
    {
        ["AUT"] = "AT", ["BEL"] = "BE", ["BGR"] = "BG", ["HRV"] = "HR", ["CYP"] = "CY", ["CZE"] = "CZ", ["DNK"] = "DK",
        ["EST"] = "EE", ["FIN"] = "FI", ["FRA"] = "FR", ["DEU"] = "DE", ["GRC"] = "GR", ["HUN"] = "HU", ["IRL"] = "IE",
        ["ITA"] = "IT", ["LVA"] = "LV", ["LTU"] = "LT", ["LUX"] = "LU", ["MLT"] = "MT", ["NLD"] = "NL", ["POL"] = "PL",
        ["PRT"] = "PT", ["ROU"] = "RO", ["SVK"] = "SK", ["SVN"] = "SI", ["ESP"] = "ES", ["SWE"] = "SE", ["NOR"] = "NO",
        ["ISL"] = "IS", ["LIE"] = "LI", ["CHE"] = "CH", ["UKR"] = "UA", ["GBR"] = "GB",
    };

    private static readonly Dictionary<string, string> Languages = new(StringComparer.OrdinalIgnoreCase)
    {
        ["BUL"] = "bg", ["CES"] = "cs", ["DAN"] = "da", ["DEU"] = "de", ["ELL"] = "el", ["ENG"] = "en", ["EST"] = "et",
        ["FIN"] = "fi", ["FRA"] = "fr", ["GLE"] = "ga", ["HRV"] = "hr", ["HUN"] = "hu", ["ITA"] = "it", ["LAV"] = "lv",
        ["LIT"] = "lt", ["MLT"] = "mt", ["NLD"] = "nl", ["POL"] = "pl", ["POR"] = "pt", ["RON"] = "ro", ["SLK"] = "sk",
        ["SLV"] = "sl", ["SPA"] = "es", ["SWE"] = "sv",
    };

    public static string Country(string? iso3) =>
        iso3 is null ? "" : Countries.GetValueOrDefault(iso3, iso3.Length == 2 ? iso3.ToUpperInvariant() : iso3);

    public static string Language(string? iso3) =>
        iso3 is null ? "" : Languages.GetValueOrDefault(iso3, iso3.ToLowerInvariant());

    /// <summary>eForms unit codes seen in notices, shown next to quantities.</summary>
    public static readonly Dictionary<string, string> Units = new()
    {
        ["EA"] = "pcs", ["SE"] = "sets", ["PR"] = "pairs", ["KT"] = "kits", ["H87"] = "pcs", ["C62"] = "pcs",
    };

    /// <summary>Plain-language names for the eForms selection-criterion codes we meet in defence notices.</summary>
    public static readonly Dictionary<string, string> Criteria = new()
    {
        ["slc-suit-reg-trade"] = "Enrolment in a trade register",
        ["slc-suit-reg-prof"] = "Enrolment in a professional register",
        ["slc-suit-auth-mbrshp"] = "Authorisation or membership to perform the activity",
        ["slc-abil-ref-supply"] = "References: past deliveries",
        ["slc-abil-ref-services"] = "References: past services",
        ["slc-abil-staff-qualif"] = "Staff qualifications",
        ["slc-abil-qual-cert"] = "Quality assurance certificates",
        ["slc-stand-fin-turnover"] = "Minimum turnover",
        ["slc-stand-fin-insur"] = "Professional risk insurance",
        ["slc-other"] = "Other",
    };
}
