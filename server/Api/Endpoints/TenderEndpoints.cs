using Marketplace.Data;
using Marketplace.Tenders;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Endpoints;

public static class TenderEndpoints
{
    public record TenderSummary(
        string Id, TenderSource Source, string SourceId, string? Reference, string Title, string Language,
        Dictionary<string, string> Titles, string BuyerName, string BuyerCountry, string? BuyerCity, string Cpv,
        Dictionary<string, string> CpvLabels, DateTimeOffset PublishedAt, DateTimeOffset? Deadline, bool IsDefence,
        bool Featured, bool HasFullNotice, int LotCount, decimal? TotalQuantity);

    public static void MapTenderEndpoints(this WebApplication app)
    {
        var api = app.MapGroup("/api/tenders");

        api.MapGet("/", async (AppDb db, string? q, string? country, string? cpv, string? source, bool? defence, bool? open) =>
        {
            var all = await db.Tenders
                .Select(t => new { t.Id, t.Source, t.SourceId, t.Reference, t.Title, t.Language, t.Titles, t.BuyerName,
                    t.BuyerCountry, t.BuyerCity, t.Cpv, t.CpvLabels, t.PublishedAt, t.Deadline, t.IsDefence, t.Featured,
                    t.HasFullNotice, t.Lots })
                .ToListAsync();

            var now = DateTimeOffset.UtcNow;
            var rows = all
                .Where(t => string.IsNullOrEmpty(country) || t.BuyerCountry == country)
                .Where(t => string.IsNullOrEmpty(cpv) || t.Cpv.StartsWith(cpv))
                .Where(t => string.IsNullOrEmpty(source) || t.Source.ToString() == source)
                .Where(t => defence != true || t.IsDefence)
                .Where(t => open != true || t.Deadline is null || t.Deadline > now)
                .Where(t => string.IsNullOrWhiteSpace(q)
                    || $"{t.Title} {string.Join(' ', t.Titles.Values)} {t.BuyerName} {t.SourceId} {t.Reference} {t.Cpv}"
                        .Contains(q.Trim(), StringComparison.OrdinalIgnoreCase))
                .OrderByDescending(t => t.Featured)
                .ThenByDescending(t => t.Deadline > now)
                .ThenBy(t => t.Deadline > now ? t.Deadline : DateTimeOffset.MaxValue)
                .ThenByDescending(t => t.PublishedAt)
                .Select(t => new TenderSummary(t.Id, t.Source, t.SourceId, t.Reference, t.Title, t.Language, t.Titles,
                    t.BuyerName, t.BuyerCountry, t.BuyerCity, t.Cpv, t.CpvLabels, t.PublishedAt, t.Deadline, t.IsDefence,
                    t.Featured, t.HasFullNotice, t.Lots.Count, t.Lots.Sum(l => l.Quantity)))
                .ToList();

            return new { total = all.Count, rows };
        });

        api.MapGet("/stats", async (AppDb db) => new
        {
            total = await db.Tenders.CountAsync(),
            ted = await db.Tenders.CountAsync(t => t.Source == TenderSource.TED),
            bzp = await db.Tenders.CountAsync(t => t.Source == TenderSource.BZP),
            defence = await db.Tenders.CountAsync(t => t.IsDefence),
            open = await db.Tenders.CountAsync(t => t.Deadline > DateTimeOffset.UtcNow),
            countries = await db.Tenders.Select(t => t.BuyerCountry).Distinct().OrderBy(c => c).ToListAsync(),
        });

        api.MapGet("/{id}", async Task<IResult> (AppDb db, string id) =>
            await db.Tenders.FindAsync(id) is { } t ? TypedResults.Ok(t) : TypedResults.NotFound());

        api.MapGet("/{id}/ocds", async Task<IResult> (AppDb db, string id) =>
            await db.Tenders.FindAsync(id) is { } t
                ? TypedResults.Ok(new
                {
                    uri = $"/api/tenders/{id}/ocds",
                    version = "1.1",
                    publishedDate = DateTimeOffset.UtcNow,
                    publisher = new { name = "EU Tender Agent" },
                    releases = new[] { Ocds.Release(t) },
                })
                : TypedResults.NotFound());

        api.MapPost("/import/ted/{publicationNumber}", async Task<IResult> (TenderStore store, string publicationNumber, CancellationToken ct) =>
        {
            try
            {
                return await store.ImportFromTedAsync(publicationNumber, ct) is { } t
                    ? TypedResults.Ok(new { t.Id })
                    : TypedResults.NotFound();
            }
            catch (HttpRequestException e)
            {
                return TypedResults.Problem($"TED is not reachable: {e.Message}", statusCode: 502);
            }
        });
    }
}
