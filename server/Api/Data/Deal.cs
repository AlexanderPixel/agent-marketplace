using Marketplace.Documents;

namespace Marketplace.Data;

public enum DealStatus { Negotiating, AwaitingApproval, Closed, Failed }

/// <summary>
/// A company's pre-approved negotiation mandate. Private to its organisation: the other side's agent never sees it (rule 3),
/// and the engine enforces it in code (rule 4).
/// </summary>
public class Mandate
{
    public Guid OrgId { get; set; }
    public string Product { get; set; } = "";
    public bool Buying { get; set; }
    public decimal TargetPrice { get; set; }
    /// <summary>Buying: the most we pay per unit. Selling: the least we accept.</summary>
    public decimal LimitPrice { get; set; }
    public int MinQuantity { get; set; }
    public int MaxQuantity { get; set; }
    /// <summary>Buying: latest acceptable delivery. Selling: earliest possible delivery.</summary>
    public DateOnly DeliveryLimit { get; set; }
    public int AutonomyThreshold { get; set; } = 85;
    public decimal AutonomousValueCapEur { get; set; } = 200_000;
}

/// <summary>Deal terms only. This is everything the two sides share (rule 3).</summary>
public record Offer(int Round, string Side, decimal UnitPrice, int Quantity, DateOnly Delivery, string Message, bool Accepts, DateTimeOffset At);

/// <summary>A partner deal for one lot. Visible to both parties, so it holds terms only, never either side's limits.</summary>
public class Deal
{
    public Guid Id { get; set; }
    public string TenderId { get; set; } = "";
    public string TenderRef { get; set; } = "";
    public string LotId { get; set; } = "";
    public string LotTitle { get; set; } = "";
    public string Unit { get; set; } = "";
    public int Gap { get; set; }
    public Guid BidderOrgId { get; set; }
    public Guid PartnerOrgId { get; set; }
    public int MatchScore { get; set; }
    public string DealType { get; set; } = "subcontract";
    public DealStatus Status { get; set; }
    public bool Autonomous { get; set; }
    public string? Outcome { get; set; }
    public decimal? UnitPrice { get; set; }
    public int? Quantity { get; set; }
    public DateOnly? Delivery { get; set; }
    public List<Offer> Offers { get; set; } = [];
    public DateTimeOffset CreatedAt { get; set; }
    public DateTimeOffset? ClosedAt { get; set; }
    public string? ApprovedBy { get; set; }
    public List<AgreementDocument> Documents { get; set; } = [];
    public DateTimeOffset? DocumentsAt { get; set; }
}
