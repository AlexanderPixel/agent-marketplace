namespace Marketplace.Ecertis;

/// <param name="Online">Can be ordered online, no office visit.</param>
/// <param name="Cost">As charged at the time of writing; check the linked page before ordering.</param>
public record ObtainableDocument(string Name, string Where, string Url, bool Online, string Cost, string Time);

/// <summary>
/// Where a Polish company actually gets each kind of evidence, so "the Polish equivalent is Y" comes with how to obtain Y.
/// Fees and turnaround are the published ones; the linked page is the source of truth.
/// </summary>
public static class PolishSources
{
    public const string TranslationNote =
        "German buyers usually ask for a sworn translation into German (tłumacz przysięgły). Allow a few working days for it.";

    public static readonly Dictionary<string, List<ObtainableDocument>> ByKind = new()
    {
        ["trade-register"] =
        [
            new("Aktualny odpis z KRS", "Ministry of Justice, KRS search", "https://ekrs.ms.gov.pl/web/wyszukiwarka-krs", true, "Free", "Immediately, as a PDF"),
            new("Wydruk z CEIDG (sole traders)", "CEIDG on biznes.gov.pl", "https://aplikacja.ceidg.gov.pl/ceidg/ceidg.public.ui/search.aspx", true, "Free", "Immediately"),
        ],
        ["criminal-record"] =
        [
            new("Informacja z KRK o podmiocie zbiorowym", "Krajowy Rejestr Karny, e-KRK", "https://ekrk.ms.gov.pl", true, "20 zł online, 30 zł on paper", "Usually within one working day online"),
        ],
        ["social-security"] =
        [
            new("Zaświadczenie o niezaleganiu w opłacaniu składek", "ZUS, through eZUS (PUE)", "https://www.zus.pl/ezus", true, "Free", "Up to 7 days, often the same day"),
        ],
        ["tax"] =
        [
            new("Zaświadczenie o niezaleganiu w podatkach (ZAW-NZ)", "Urząd Skarbowy, through e-Urząd Skarbowy", "https://www.podatki.gov.pl/e-urzad-skarbowy/", true, "21 zł", "Up to 7 days"),
        ],
        ["reference"] =
        [
            new("Referencje or protokół odbioru", "The buyer of an earlier delivery", "https://ezamowienia.gov.pl", false, "Free", "Ask the buyer; usually a few days"),
        ],
        ["iso9001"] = [new("ISO 9001 certificate", "Your certification body", "https://www.pca.gov.pl", false, "Under your certification contract", "Renew before it expires")],
        ["aqap2110"] = [new("AQAP 2110 certificate", "Your certification body (WCNJiK for defence)", "https://www.wojsko-polskie.pl/wcnjik/", false, "Under your certification contract", "Renew before it expires")],
    };
}
