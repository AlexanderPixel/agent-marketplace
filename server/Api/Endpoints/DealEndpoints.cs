using Marketplace.Activity;
using Marketplace.Data;
using Marketplace.Deals;
using Marketplace.Documents;
using Marketplace.Partners;
using Marketplace.Tenancy;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Endpoints;

public static class DealEndpoints
{
    public record StartDeal(string TenderId, string LotId, Guid PartnerOrgId, int Gap);

    public record MandateUpdate(decimal TargetPrice, decimal LimitPrice, int MinQuantity, int MaxQuantity, DateOnly DeliveryLimit, int AutonomyThreshold, decimal AutonomousValueCapEur);

    public static void MapDealEndpoints(this WebApplication app)
    {
        var api = app.MapGroup("/api");

        api.MapGet("/mandate", async (AppDb db, OrgContext org) =>
        {
            org.Require();
            return await db.Mandates.FirstOrDefaultAsync();
        });

        api.MapPut("/mandate", async Task<IResult> (MandateUpdate body, AppDb db, OrgContext org, ActivityLog log) =>
        {
            var orgId = org.Require();
            var m = await db.Mandates.FirstOrDefaultAsync();
            if (m is null) return TypedResults.NotFound();
            (m.TargetPrice, m.LimitPrice, m.MinQuantity, m.MaxQuantity, m.DeliveryLimit, m.AutonomyThreshold, m.AutonomousValueCapEur) =
                (body.TargetPrice, body.LimitPrice, body.MinQuantity, body.MaxQuantity, body.DeliveryLimit, body.AutonomyThreshold, body.AutonomousValueCapEur);
            await db.SaveChangesAsync();
            await log.WriteAsync(orgId, "Mandate", "A person updated the pre-approved negotiation limits",
                "Agents may only agree inside these limits.");
            return TypedResults.Ok(m);
        });

        api.MapGet("/deals", async (AppDb db, OrgContext org) =>
        {
            org.Require();
            var deals = await db.Deals.OrderByDescending(d => d.CreatedAt).ToListAsync();
            var names = await db.Organizations.ToDictionaryAsync(o => o.Id, o => new { o.Name, o.Country });
            return deals.Select(d => new { deal = d, bidder = names[d.BidderOrgId], partner = names[d.PartnerOrgId] });
        });

        api.MapGet("/deals/{id:guid}", async Task<IResult> (Guid id, AppDb db, OrgContext org) =>
        {
            var orgId = org.Require();
            var d = await db.Deals.FirstOrDefaultAsync(x => x.Id == id);
            if (d is null) return TypedResults.NotFound();
            var orgs = await db.Organizations.Where(o => o.Id == d.BidderOrgId || o.Id == d.PartnerOrgId).ToDictionaryAsync(o => o.Id);
            var names = orgs.ToDictionary(o => o.Key, o => new { o.Value.Name, o.Value.Country, o.Value.City });
            // The viewer's own certificates only: the profile is scoped to the calling organisation, and the partner attaches its own.
            var tender = await db.Tenders.FirstOrDefaultAsync(t => t.Id == d.TenderId);
            var profile = await db.Profiles.FirstOrDefaultAsync();
            var certificates = tender is null || profile is null ? [] : TenderCertificates.For(tender, profile, orgs[orgId]);
            return TypedResults.Ok(new { deal = d, bidder = names[d.BidderOrgId], partner = names[d.PartnerOrgId], certificates });
        });

        api.MapPost("/deals", async Task<IResult> (
            StartDeal body, AppDb db, OrgContext org, PartnerSearch search, DealRunner runner, ActivityLog log, TimeProvider clock) =>
        {
            var orgId = org.Require();
            var tender = await db.Tenders.FindAsync(body.TenderId);
            var lot = tender?.Lots.FirstOrDefault(l => l.Id == body.LotId);
            if (tender is null || lot is null) return TypedResults.NotFound();
            var candidate = (await search.SearchAsync(orgId, tender, lot, body.Gap)).FirstOrDefault(c => c.Partner.OrgId == body.PartnerOrgId);
            if (candidate is null) return TypedResults.BadRequest("Not a candidate for this gap.");
            if (!candidate.GatesPassed) return TypedResults.BadRequest("A hard gate failed for this partner.");

            var deal = await DealOps.OpenAsync(db, log, runner, clock, orgId, tender, lot, body.Gap, candidate);
            return TypedResults.Ok(new { deal.Id });
        });

        api.MapPost("/deals/{id:guid}/documents", async Task<IResult> (Guid id, AppDb db, OrgContext org, ActivityLog log, TimeProvider clock) =>
        {
            var orgId = org.Require();
            var d = await db.Deals.FirstOrDefaultAsync(x => x.Id == id);
            if (d is null) return TypedResults.NotFound();
            if (d.Status != DealStatus.Closed) return TypedResults.BadRequest("The deal is not closed yet.");
            await DealOps.DraftDocumentsAsync(db, log, clock, d, orgId);
            return TypedResults.Ok(d.Documents);
        });

        api.MapPost("/deals/{id:guid}/approve", async Task<IResult> (Guid id, AppDb db, OrgContext org, ActivityLog log, TimeProvider clock) =>
        {
            var orgId = org.Require();
            var d = await db.Deals.FirstOrDefaultAsync(x => x.Id == id);
            if (d is null) return TypedResults.NotFound();
            if (d.Status != DealStatus.AwaitingApproval) return TypedResults.BadRequest("Nothing to approve.");
            var me = await db.Organizations.FirstAsync(o => o.Id == orgId);
            await DealOps.ApproveAsync(db, log, clock, d, orgId, me.Name);
            return TypedResults.Ok();
        });
    }
}
