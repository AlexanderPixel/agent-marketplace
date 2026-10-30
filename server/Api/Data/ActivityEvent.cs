namespace Marketplace.Data;

/// <summary>
/// One line of the agent activity log (non-negotiable rule 1): who did what, when and why. Append-only.
/// A deal event is written once and shared with the other party through <see cref="SharedWithOrgId"/>.
/// </summary>
public class ActivityEvent
{
    public long Id { get; set; }
    public Guid OrgId { get; set; }
    public Guid? SharedWithOrgId { get; set; }

    /// <summary>The agent or component that acted, e.g. "Qualification agent", "Negotiation agent (Moravia Pružiny)".</summary>
    public required string Agent { get; set; }

    public required string Action { get; set; }
    public required string Reason { get; set; }

    /// <summary>What the event is about, e.g. "tender:723644-2026" or "deal:…".</summary>
    public string? Subject { get; set; }

    /// <summary>Extra structured details as JSON.</summary>
    public string? Data { get; set; }

    public DateTimeOffset CreatedAt { get; set; }
}
