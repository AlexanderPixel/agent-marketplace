using Marketplace.Activity;
using Marketplace.Data;
using Marketplace.Documents;
using Marketplace.Partners;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Deals;

/// <summary>Deal steps shared by the API (a person clicks) and the auto-bid agent (no one clicks).</summary>
public static class DealOps
{
    /// <summary>Opens a negotiation with a partner that passed every hard gate and starts the agents.</summary>
    public static async Task<Deal> OpenAsync(
        AppDb db, ActivityLog log, DealRunner runner, TimeProvider clock, Guid orgId, Tender tender, TenderLot lot, int gap, PartnerCandidate candidate)
    {
        var deal = new Deal
        {
            Id = Guid.NewGuid(), TenderId = tender.Id, TenderRef = tender.SourceId, LotId = lot.Id, LotTitle = lot.Title,
            Unit = lot.Unit ?? "", Gap = gap, BidderOrgId = orgId, PartnerOrgId = candidate.Partner.OrgId,
            MatchScore = candidate.Score, Status = DealStatus.Negotiating, CreatedAt = clock.GetUtcNow(),
        };
        db.Deals.Add(deal);
        await db.SaveChangesAsync();
        await log.WriteAsync(orgId, "Deal agent", $"Opened a negotiation with {candidate.Partner.Name} for {gap} {lot.Unit}",
            $"Best partner for the gap ({candidate.Score}% match, all hard gates passed). The agent will share deal terms only.",
            tender.Id, sharedWithOrgId: candidate.Partner.OrgId);
        runner.Start(deal.Id);
        return deal;
    }

    /// <summary>Drafts the agreement documents for a closed deal. Drafts only: people review and sign them.</summary>
    public static async Task<List<AgreementDocument>> DraftDocumentsAsync(AppDb db, ActivityLog log, TimeProvider clock, Deal d, Guid orgId)
    {
        var tender = await db.Tenders.FirstAsync(t => t.Id == d.TenderId);
        var orgs = await db.Organizations.Where(o => o.Id == d.BidderOrgId || o.Id == d.PartnerOrgId).ToDictionaryAsync(o => o.Id);
        var now = clock.GetUtcNow();
        d.Documents = AgreementDrafter.Draft(d, tender, orgs[d.BidderOrgId], orgs[d.PartnerOrgId], DateOnly.FromDateTime(now.UtcDateTime));
        d.DocumentsAt = now;
        await db.SaveChangesAsync();
        await log.WriteAsync(orgId, "Document agent",
            $"Drafted {d.Documents.Count} documents in Polish and German: {string.Join(", ", d.Documents.Select(x => x.Title.GetValueOrDefault("de") ?? x.Title.First().Value))}",
            "The deal closed. German tenders need a Nachunternehmervertrag plus the Verpflichtungserklärung, Unterauftragnehmerverzeichnis " +
            "and Eigenerklärung. Built only from the agreed deal terms and both companies' names; drafts wait for human review and signature.",
            d.TenderId, sharedWithOrgId: d.BidderOrgId == orgId ? d.PartnerOrgId : d.BidderOrgId);
        return d.Documents;
    }

    /// <summary>A person approves terms the agents agreed below the autonomy threshold.</summary>
    public static async Task ApproveAsync(AppDb db, ActivityLog log, TimeProvider clock, Deal d, Guid orgId, string approvedBy)
    {
        d.Status = DealStatus.Closed;
        d.ClosedAt = clock.GetUtcNow();
        d.ApprovedBy = approvedBy;
        d.Outcome = $"Approved by a person at {approvedBy}: {d.Quantity} {d.Unit} at €{d.UnitPrice:0.00}.";
        await db.SaveChangesAsync();
        await log.WriteAsync(orgId, "Deal agent", "Deal approved by a person", "The match was below the autonomy threshold, so a human decided.",
            d.TenderId, sharedWithOrgId: d.BidderOrgId == orgId ? d.PartnerOrgId : d.BidderOrgId);
    }
}
