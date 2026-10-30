using Marketplace.Activity;
using Marketplace.Data;
using Marketplace.Intelligence;
using Marketplace.Tenancy;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Endpoints;

public static class IntelligenceEndpoints
{
    /// <summary>CPV families where a prime's award pulls in suspension and running-gear parts.</summary>
    private static readonly string[] VehicleCpv = ["354", "3442", "5063", "5060", "341"];

    public static void MapIntelligenceEndpoints(this WebApplication app)
    {
        app.MapGet("/api/intelligence", async (AppDb db, OrgContext org, ActivityLog log) =>
        {
            var orgId = org.Require();
            var awards = PrimeAwards.All;
            var since = new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero);

            // Real monthly demand: Polish and German defence notices in our feed.
            var notices = await db.Tenders.Where(t => t.IsDefence && t.PublishedAt >= since)
                .Select(t => new { t.PublishedAt, t.BuyerCountry, t.Source }).ToListAsync();
            var monthly = notices.GroupBy(n => n.PublishedAt.ToString("yyyy-MM")).OrderBy(g => g.Key)
                .Select(g => new { month = g.Key, ted = g.Count(n => n.Source == TenderSource.TED), bzp = g.Count(n => n.Source == TenderSource.BZP) });

            var primes = awards.GroupBy(a => a.Prime).Select(g => new
            {
                prime = g.Key,
                awards = g.Count(),
                value = g.Sum(a => a.Value ?? 0),
                withValue = g.Count(a => a.Value is not null),
                entities = g.SelectMany(a => a.Winners.Distinct()).Where(w => w.Contains(g.Key, StringComparison.OrdinalIgnoreCase))
                    .GroupBy(w => w).OrderByDescending(x => x.Count()).Select(x => new { name = x.Key, awards = x.Count() }).Take(6),
            });
            var platforms = awards.Where(a => a.Platform is not null).GroupBy(a => a.Platform!)
                .Select(g => new { platform = g.Key, awards = g.Count(), primes = g.Select(a => a.Prime).Distinct(), value = g.Sum(a => a.Value ?? 0) })
                .OrderByDescending(p => p.awards).Take(14);
            var buyers = awards.GroupBy(a => (a.Buyer, a.BuyerCountry)).OrderByDescending(g => g.Count()).Take(6)
                .Select(g => new { buyer = g.Key.Buyer, country = g.Key.BuyerCountry, awards = g.Count() });

            // Tier-2 opportunities for this company: vehicle awards where a prime will buy suspension parts.
            var profile = await db.Profiles.FirstOrDefaultAsync();
            var makesVehicleParts = profile?.Products.Any(p => p.Cpv.StartsWith("343")) ?? false;
            var relevant = makesVehicleParts ? awards.Where(a => VehicleCpv.Any(a.Cpv.StartsWith)).ToList() : [];
            var opportunities = relevant.SelectMany(a => a.Winners.Distinct().Select(w => (winner: w, award: a)))
                .Where(x => x.winner.Contains("Rheinmetall", StringComparison.OrdinalIgnoreCase) || x.winner.Contains("KNDS", StringComparison.OrdinalIgnoreCase))
                .GroupBy(x => x.winner)
                .Select(g => new
                {
                    prime = g.Key,
                    awards = g.Count(),
                    value = g.Sum(x => x.award.Value ?? 0),
                    platforms = g.Select(x => x.award.Platform).OfType<string>().Distinct().Take(5),
                    latest = g.Max(x => x.award.PublishedAt),
                    notices = g.Select(x => x.award.PublicationNumber).Distinct().Take(4),
                })
                .OrderByDescending(o => o.awards).Take(5).ToList();
            if (opportunities.Count > 0)
            {
                await log.WriteAsync(orgId, "Demand agent",
                    $"Found {relevant.Count} 2026 prime awards for military vehicles; best tier-2 lead: {opportunities[0].prime} ({opportunities[0].awards} awards)",
                    "Your products (leaf springs, CPV 34320000) go into wheeled and tracked vehicles; primes that win vehicle and maintenance contracts buy them in. " +
                    "Source: TED contract award notices, cited by publication number.");
            }

            return new
            {
                totals = new
                {
                    awards = awards.Count,
                    awardValue = awards.Sum(a => a.Value ?? 0),
                    withValue = awards.Count(a => a.Value is not null),
                    safeTotal = SafeAllocations.All.Sum(a => a.Bn),
                    notices = notices.Count,
                },
                safe = SafeAllocations.All.Select(a => new { country = a.Country, bn = a.Bn }),
                monthly,
                primes,
                platforms,
                buyers,
                frameworks = awards.Where(a => a.Framework),
                recent = awards.Take(12),
                opportunities,
            };
        });
    }
}
