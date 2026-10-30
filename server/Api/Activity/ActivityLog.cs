using System.Text.Json;
using Marketplace.Data;

namespace Marketplace.Activity;

/// <summary>Every agent action goes through here (rule 1): stored, then pushed to live feeds.</summary>
public class ActivityLog(AppDb db, ActivityHub hub, TimeProvider clock)
{
    public async Task<ActivityEvent> WriteAsync(
        Guid orgId, string agent, string action, string reason,
        string? subject = null, object? data = null, Guid? sharedWithOrgId = null, CancellationToken ct = default)
    {
        var e = new ActivityEvent
        {
            OrgId = orgId,
            SharedWithOrgId = sharedWithOrgId,
            Agent = agent,
            Action = action,
            Reason = reason,
            Subject = subject,
            Data = data is null ? null : JsonSerializer.Serialize(data, JsonSerializerOptions.Web),
            CreatedAt = clock.GetUtcNow(),
        };
        db.ActivityEvents.Add(e);
        await db.SaveChangesAsync(ct);
        hub.Publish(e);
        return e;
    }
}
