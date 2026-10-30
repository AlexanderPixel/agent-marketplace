using Marketplace.Activity;
using Marketplace.Data;
using Marketplace.Partners;
using Marketplace.Tenancy;

namespace Marketplace.Endpoints;

public static class PartnerEndpoints
{
    public static void MapPartnerEndpoints(this WebApplication app)
    {
        app.MapGet("/api/partners", async (PartnerSearch search, OrgContext org) => await search.ListingsAsync(org.Require()));

        app.MapPost("/api/partners/search", async Task<IResult> (
            SearchRequest body, PartnerSearch search, AppDb db, OrgContext org, ActivityLog log, CancellationToken ct) =>
        {
            var orgId = org.Require();
            var tender = await db.Tenders.FindAsync([body.TenderId], ct);
            var lot = tender?.Lots.FirstOrDefault(l => l.Id == body.LotId);
            if (tender is null || lot is null) return TypedResults.NotFound();

            var candidates = await search.SearchAsync(orgId, tender, lot, body.Gap);
            var best = candidates.FirstOrDefault();
            await log.WriteAsync(orgId, "Partner search agent",
                $"Searched the partner network for {body.Gap} {lot.Unit} of {lot.Title} ({tender.SourceId}, {lot.Id}): " +
                $"{candidates.Count} candidates" + (best is null ? "" : $", best {best.Partner.Name} ({best.Partner.Country}) at {best.Score}%"),
                "Your capacity check found a gap. Only public listings were read: capacity, certificates and country; " +
                "no partner documents, prices or limits.",
                subject: tender.Id, data: candidates.Select(c => new { c.Partner.Name, c.Score, c.GatesPassed }), ct: ct);
            return TypedResults.Ok(new { tender = new { tender.Id, tender.SourceId, tender.Title, tender.Titles }, lot, body.Gap, candidates });
        });
    }

    public record SearchRequest(string TenderId, string LotId, int Gap);
}
