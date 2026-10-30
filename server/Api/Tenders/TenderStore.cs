using System.Text.Json;
using Marketplace.Data;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Tenders;

/// <summary>
/// Loads the cached demo notices on start (so the demo never depends on a live call) and imports a TED notice on demand.
/// </summary>
public class TenderStore(AppDb db, IHttpClientFactory http, ILogger<TenderStore> log)
{
    public const string FeaturedPublication = "723644-2026";

    public static async Task SeedAsync(AppDb db, string dataPath)
    {
        if (await db.Tenders.AnyAsync()) return;

        var tenders = new Dictionary<string, Tender>();
        foreach (var file in Directory.GetFiles(Path.Combine(dataPath, "ted/notices"), "*.json"))
        {
            var raw = await File.ReadAllTextAsync(file);
            var t = TedNotice.Map(JsonDocument.Parse(raw).RootElement, raw);
            t.Featured = t.SourceId == FeaturedPublication;
            tenders[t.Id] = t;
        }

        var listFile = Path.Combine(dataPath, "ted/pl-defence-notices.json");
        if (File.Exists(listFile))
        {
            foreach (var n in JsonDocument.Parse(await File.ReadAllTextAsync(listFile)).RootElement.EnumerateArray())
            {
                var t = TedNotice.Map(n, n.GetRawText());
                if (!tenders.Values.Any(x => x.SourceId == t.SourceId)) tenders.TryAdd(t.Id, t);
            }
        }

        var bzpFile = Path.Combine(dataPath, "bzp/notices.json");
        if (File.Exists(bzpFile))
        {
            foreach (var n in JsonDocument.Parse(await File.ReadAllTextAsync(bzpFile)).RootElement.EnumerateArray())
            {
                var t = BzpNotice.Map(n);
                // A notice can be republished with a new version; keep the most complete one.
                if (!tenders.TryGetValue(t.Id, out var existing) || (!existing.HasFullNotice && t.HasFullNotice))
                {
                    tenders[t.Id] = t;
                }
            }
        }

        foreach (var t in tenders.Values) Normalize(t);
        db.Tenders.AddRange(tenders.Values);
        await db.SaveChangesAsync();
    }

    /// <summary>Postgres timestamptz only takes UTC.</summary>
    private static void Normalize(Tender t)
    {
        t.PublishedAt = t.PublishedAt.ToUniversalTime();
        t.Deadline = t.Deadline?.ToUniversalTime();
    }

    /// <summary>Fetches one notice from the TED Search API (anonymous) and upserts it.</summary>
    public async Task<Tender?> ImportFromTedAsync(string publicationNumber, CancellationToken ct)
    {
        var client = http.CreateClient();
        var body = new { query = $"publication-number={publicationNumber}", fields = TedNotice.SearchFields, limit = 1 };
        using var response = await client.PostAsJsonAsync("https://api.ted.europa.eu/v3/notices/search", body, ct);
        if (!response.IsSuccessStatusCode)
        {
            log.LogWarning("TED search for {Publication} failed: {Status}", publicationNumber, response.StatusCode);
            return null;
        }

        using var doc = JsonDocument.Parse(await response.Content.ReadAsStringAsync(ct));
        var notice = doc.RootElement.GetProperty("notices").EnumerateArray().FirstOrDefault();
        if (notice.ValueKind != JsonValueKind.Object) return null;

        var tender = TedNotice.Map(notice, notice.GetRawText());
        var existing = await db.Tenders.FindAsync([tender.Id], ct);
        if (existing is not null)
        {
            db.Tenders.Remove(existing);
            await db.SaveChangesAsync(ct);
        }

        Normalize(tender);
        db.Tenders.Add(tender);
        await db.SaveChangesAsync(ct);
        return tender;
    }
}
