using Marketplace.Activity;
using Marketplace.Ai;
using Marketplace.Data;
using Marketplace.Tenancy;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Endpoints;

public static class CoreEndpoints
{
    public static void MapCoreEndpoints(this WebApplication app)
    {
        var api = app.MapGroup("/api");

        api.MapGet("/health", (AiService ai) => new { ok = true, ai = ai.Enabled, model = ai.Enabled ? ai.Model : null });

        api.MapGet("/orgs", async (AppDb db) =>
            await db.Organizations.OrderBy(o => o.Name).ToListAsync());

        api.MapGet("/activity", async (AppDb db, OrgContext org, string? subject, int? take) =>
        {
            org.Require();
            var query = db.ActivityEvents.AsQueryable();
            if (!string.IsNullOrEmpty(subject))
            {
                query = query.Where(a => a.Subject == subject);
            }

            return await query.OrderByDescending(a => a.Id).Take(take ?? 200).ToListAsync();
        });

        api.MapGet("/activity/stream", (ActivityHub hub, OrgContext org, CancellationToken ct) =>
        {
            var orgId = org.Require();
            return TypedResults.ServerSentEvents(Stream(hub, orgId, ct), eventType: "activity");
        });
    }

    private static async IAsyncEnumerable<ActivityEvent> Stream(
        ActivityHub hub, Guid orgId, [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken ct)
    {
        var (id, reader) = hub.Subscribe(orgId);
        try
        {
            await foreach (var e in reader.ReadAllAsync(ct))
            {
                yield return e;
            }
        }
        finally
        {
            hub.Unsubscribe(id);
        }
    }
}
