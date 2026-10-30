namespace Marketplace.Data;

public enum TenderSource { TED, BZP }

/// <summary>
/// One procurement procedure, keyed by its OCDS OCID. Tenders are public data, so there is no organisation filter.
/// We keep the fields the screens use plus the raw source fields, and build the OCDS release from these on demand.
/// </summary>
public class Tender
{
    public string Id { get; set; } = "";
    public TenderSource Source { get; set; }
    public string SourceId { get; set; } = "";
    public string? Reference { get; set; }
    public string Title { get; set; } = "";
    public string Language { get; set; } = "pl";
    public Dictionary<string, string> Titles { get; set; } = [];
    public string BuyerName { get; set; } = "";
    public string BuyerCountry { get; set; } = "";
    public string? BuyerCity { get; set; }
    public string Cpv { get; set; } = "";
    public Dictionary<string, string> CpvLabels { get; set; } = [];
    public string? ProcedureType { get; set; }
    public string? ContractNature { get; set; }
    public DateTimeOffset PublishedAt { get; set; }
    public DateTimeOffset? Deadline { get; set; }
    public bool IsDefence { get; set; }
    public bool Featured { get; set; }
    public bool HasFullNotice { get; set; }
    public string? NoticeUrl { get; set; }
    public string? DocumentsUrl { get; set; }
    public List<TenderLot> Lots { get; set; } = [];
    public List<TenderRequirement> Requirements { get; set; } = [];
    public List<string> ExclusionGrounds { get; set; } = [];
    public string? Text { get; set; }
    public string? RawJson { get; set; }
}

/// <param name="Source">Where in the notice this lot came from, e.g. "eForms BT-21-Lot / BT-24-Lot, LOT-0004".</param>
public record TenderLot(
    string Id, string Title, decimal? Quantity, string? Unit, string? Description, string? Cpv, string? Place, string Source);

/// <param name="Code">eForms selection-criterion code, e.g. slc-suit-reg-trade; null when the source has no code.</param>
/// <param name="Source">Field and lots this came from, used for citations.</param>
public record TenderRequirement(string? Code, string Text, string Language, List<string> Lots, string Source);
