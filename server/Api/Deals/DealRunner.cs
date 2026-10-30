using Marketplace.Activity;
using Marketplace.Data;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Deals;

/// <summary>
/// Runs a negotiation in the background, one offer at a time with a short pause, so both companies watch it live.
/// Each agent logs its own reasoning to its own organisation only; the deal record holds the shared terms.
/// </summary>
public class DealRunner(IServiceScopeFactory scopes, ILogger<DealRunner> log)
{
    public const int MaxMessages = 14;
    private static readonly TimeSpan Pause = TimeSpan.FromMilliseconds(1600);

    public void Start(Guid dealId) => _ = Task.Run(async () =>
    {
        try
        {
            await RunAsync(dealId);
        }
        catch (Exception e)
        {
            log.LogError(e, "Negotiation {Deal} failed", dealId);
        }
    });

    private async Task RunAsync(Guid dealId)
    {
        using var scope = scopes.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDb>();
        var activity = scope.ServiceProvider.GetRequiredService<ActivityLog>();
        var clock = scope.ServiceProvider.GetRequiredService<TimeProvider>();

        // System process acting for both parties: it loads each mandate separately and hands each agent only its own.
        var deal = await db.Deals.IgnoreQueryFilters().FirstAsync(d => d.Id == dealId);
        var mandates = await db.Mandates.IgnoreQueryFilters()
            .Where(m => m.OrgId == deal.BidderOrgId || m.OrgId == deal.PartnerOrgId).ToListAsync();
        var orgs = await db.Organizations.Where(o => o.Id == deal.BidderOrgId || o.Id == deal.PartnerOrgId).ToDictionaryAsync(o => o.Id);
        var buyer = new NegotiationAgent("bidder", mandates.First(m => m.OrgId == deal.BidderOrgId), deal.Gap);
        var seller = new NegotiationAgent("partner", mandates.First(m => m.OrgId == deal.PartnerOrgId), deal.Gap);
        string AgentName(NegotiationAgent a) => $"Deal agent · {orgs[a.Side == "bidder" ? deal.BidderOrgId : deal.PartnerOrgId].Name}";
        Guid OrgOf(NegotiationAgent a) => a.Side == "bidder" ? deal.BidderOrgId : deal.PartnerOrgId;

        Offer? last = null;
        var turn = buyer;
        for (var round = 1; round <= MaxMessages; round++)
        {
            await Task.Delay(Pause);
            var (offer, reason) = turn.Respond(last, round, clock.GetUtcNow());
            if (offer is null)
            {
                deal.Status = DealStatus.Failed;
                deal.Outcome = $"No agreement: {reason}";
                deal.ClosedAt = clock.GetUtcNow();
                await db.SaveChangesAsync();
                await activity.WriteAsync(OrgOf(turn), AgentName(turn), "Stopped negotiating", reason, deal.TenderId);
                await activity.WriteAsync(OrgOf(turn == buyer ? seller : buyer), "Deal agent", "Negotiation ended without agreement",
                    "The other side had no offer left inside its mandate.", deal.TenderId);
                return;
            }

            deal.Offers = [.. deal.Offers, offer];
            await db.SaveChangesAsync();
            await activity.WriteAsync(OrgOf(turn), AgentName(turn),
                offer.Accepts
                    ? $"Accepted {offer.Quantity} {deal.Unit} at €{offer.UnitPrice:0.00}, delivery {offer.Delivery:yyyy-MM-dd}"
                    : $"Offered {offer.Quantity} {deal.Unit} at €{offer.UnitPrice:0.00}, delivery {offer.Delivery:yyyy-MM-dd}",
                reason + " Checked against our pre-approved limits before sending.", deal.TenderId);

            if (offer.Accepts)
            {
                await CloseAsync(db, activity, clock, deal, offer, buyer, seller, mandates, orgs);
                return;
            }

            last = offer;
            turn = turn == buyer ? seller : buyer;
        }

        deal.Status = DealStatus.Failed;
        deal.Outcome = "No agreement within the round limit.";
        await db.SaveChangesAsync();
    }

    private static async Task CloseAsync(
        AppDb db, ActivityLog activity, TimeProvider clock, Deal deal, Offer agreed, NegotiationAgent buyer, NegotiationAgent seller,
        List<Mandate> mandates, Dictionary<Guid, Organization> orgs)
    {
        // Rule 4, checked again at the end: the final terms must sit inside BOTH mandates.
        if (!buyer.WithinMandate(agreed.UnitPrice, agreed.Quantity, agreed.Delivery) ||
            !seller.WithinMandate(agreed.UnitPrice, agreed.Quantity, agreed.Delivery))
        {
            deal.Status = DealStatus.Failed;
            deal.Outcome = "Rejected: final terms fall outside a pre-approved mandate.";
            await db.SaveChangesAsync();
            return;
        }

        deal.UnitPrice = agreed.UnitPrice;
        deal.Quantity = agreed.Quantity;
        deal.Delivery = agreed.Delivery;
        var value = agreed.UnitPrice * agreed.Quantity;
        var reasons = new List<string>();
        foreach (var m in mandates)
        {
            var who = orgs[m.OrgId].Name;
            if (deal.MatchScore < m.AutonomyThreshold) reasons.Add($"{who}: match {deal.MatchScore}% is below its {m.AutonomyThreshold}% threshold");
            if (value > m.AutonomousValueCapEur) reasons.Add($"{who}: value €{value:N0} is above its €{m.AutonomousValueCapEur:N0} cap");
        }

        var now = clock.GetUtcNow();
        var shortfall = agreed.Quantity < deal.Gap ? $" Covers {agreed.Quantity} of the {deal.Gap} {deal.Unit} needed; {deal.Gap - agreed.Quantity} still open." : "";
        if (reasons.Count == 0)
        {
            deal.Status = DealStatus.Closed;
            deal.Autonomous = true;
            deal.ClosedAt = now;
            deal.Outcome = $"Closed by the agents with no human in the loop: {agreed.Quantity} {deal.Unit} at €{agreed.UnitPrice:0.00} (€{value:N0}), delivery {agreed.Delivery:yyyy-MM-dd}." + shortfall;
            foreach (var orgId in new[] { deal.BidderOrgId, deal.PartnerOrgId })
            {
                await activity.WriteAsync(orgId, "Deal agent", $"Closed the deal without a human: €{value:N0}",
                    $"Match {deal.MatchScore}% meets the autonomy threshold, the value is under the cap, and the terms are inside our pre-approved limits.",
                    deal.TenderId);
            }
        }
        else
        {
            deal.Status = DealStatus.AwaitingApproval;
            deal.Outcome = "Terms agreed by the agents; waiting for human approval: " + string.Join("; ", reasons) + "." + shortfall;
            foreach (var orgId in new[] { deal.BidderOrgId, deal.PartnerOrgId })
            {
                await activity.WriteAsync(orgId, "Deal agent", "Agreed terms, handed over for human approval",
                    string.Join("; ", reasons) + ".", deal.TenderId);
            }
        }

        await db.SaveChangesAsync();
    }
}
