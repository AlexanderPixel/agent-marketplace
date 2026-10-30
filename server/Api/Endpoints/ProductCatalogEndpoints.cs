using Marketplace.Data;
using Marketplace.Products;
using Marketplace.Qualification;
using Marketplace.Tenancy;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Endpoints;

/// <summary>The company's own products with their technical sheets, matched against buyer specifications and open tenders.</summary>
public static class ProductCatalogEndpoints
{
    public static void MapProductCatalogEndpoints(this WebApplication app)
    {
        var api = app.MapGroup("/api/products");

        api.MapGet("/", async (AppDb db, OrgContext org) =>
        {
            org.Require();
            var profile = await db.Profiles.FirstOrDefaultAsync();
            return (profile?.Products ?? []).Select(p => new
            {
                p.Id, p.Name, p.Category, p.Origin, p.Summary, p.Cpv, p.Capacity, p.Unit, p.UnitPriceEur, p.LeadTimeDays,
                specs = p.Specs?.Count ?? 0,
                needs = MarketNeeds.All.Count(n => SpecMatch.Relevant(p, n)),
            });
        });

        api.MapGet("/{id}", async Task<IResult> (string id, AppDb db, OrgContext org, TimeProvider clock) =>
        {
            org.Require();
            var profile = await db.Profiles.FirstOrDefaultAsync();
            var product = profile?.Products.FirstOrDefault(p => p.Id == id);
            if (product is null) return TypedResults.NotFound();

            var only = new CompanyProfile { Products = [product] };
            var now = clock.GetUtcNow();
            // Open tenders first; recently closed ones show that this demand exists even when nothing is open today.
            var recent = await db.Tenders.Where(t => t.Deadline > now.AddDays(-365)).ToListAsync();
            var matched = recent
                .Select(t => (Tender: t, Lots: t.Lots.Where(l => QualificationCheck.CheckLot(l, only).Fit != LotFit.NotOffered).ToList()))
                .Where(x => x.Lots.Count > 0 || (x.Tender.Lots.Count == 0 && product.Keywords.Any(k => x.Tender.Title.Contains(k, StringComparison.OrdinalIgnoreCase))))
                .OrderBy(x => x.Tender.Deadline < now).ThenBy(x => x.Tender.Deadline < now ? -x.Tender.Deadline!.Value.ToUnixTimeSeconds() : x.Tender.Deadline!.Value.ToUnixTimeSeconds())
                .Take(10)
                .Select(x => new
                {
                    open = x.Tender.Deadline > now,
                    x.Tender.Id, x.Tender.SourceId, x.Tender.Source, x.Tender.Title, x.Tender.Titles, x.Tender.BuyerName, x.Tender.BuyerCountry, x.Tender.Deadline,
                    lots = x.Lots.Select(l => new { l.Id, l.Title, l.Quantity, l.Unit }),
                });

            var needs = MarketNeeds.All.Where(n => SpecMatch.Relevant(product, n)).Select(n => SpecMatch.Check(product, n))
                .OrderBy(m => m.Verdict == "met" ? 0 : m.Verdict == "open" ? 1 : 2);
            return TypedResults.Ok(new { product, needs, tenders = matched });
        });
    }
}
