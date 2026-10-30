namespace Marketplace.Data;

public enum AutoBidStage { Qualifying, FindingPartner, Negotiating, Drafting, AwaitingSignature, Signed, Declined, Stopped }

/// <summary>Whether a company lets its agents work tenders on their own. Off until a person turns it on.</summary>
public class AutoBidSettings
{
    public Guid OrgId { get; set; }
    public bool Enabled { get; set; }
}

/// <summary>
/// One tender the auto-bid agent took on for a company, from the qualification check to the person's decision.
/// The agents prepare everything; the run ends at a human Sign or Don't sign (rule 5).
/// </summary>
public class AutoBidRun
{
    public Guid Id { get; set; }
    public Guid OrgId { get; set; }
    public string TenderId { get; set; } = "";
    public string TenderRef { get; set; } = "";
    public string Title { get; set; } = "";
    public string Verdict { get; set; } = "";
    public AutoBidStage Stage { get; set; }
    public Guid? DealId { get; set; }
    /// <summary>What happened last, in a sentence a person can read.</summary>
    public string Note { get; set; } = "";
    public DateTimeOffset CreatedAt { get; set; }
    public DateTimeOffset UpdatedAt { get; set; }
    public DateTimeOffset? DecidedAt { get; set; }
    public string? DecidedBy { get; set; }
    public long? TelegramMessageId { get; set; }
    /// <summary>"won" or "lost" once the buyer has awarded the contract; a win becomes a reference.</summary>
    public string? Result { get; set; }

    public bool IsOpen => Stage is not (AutoBidStage.Signed or AutoBidStage.Declined or AutoBidStage.Stopped);
}
