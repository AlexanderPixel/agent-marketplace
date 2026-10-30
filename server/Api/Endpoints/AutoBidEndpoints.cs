using Marketplace.Activity;
using Marketplace.AutoBid;
using Marketplace.Data;
using Marketplace.Tenancy;
using Marketplace.Telegram;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Endpoints;

public static class AutoBidEndpoints
{
    public record Toggle(bool Enabled);
    public record Decision(bool Sign);
    public record DemoStart(string TenderId);

    /// <summary>
    /// Runs are written in the company's language (Telegram speaks it too). The page shows them in the language the viewer
    /// picked: the note is re-filled from its template and the title taken from the tender's own translations.
    /// </summary>
    private static async Task InViewerLanguageAsync(AppDb db, OrgContext org, HttpRequest request, List<AutoBidRun> runs)
    {
        var viewer = request.Headers.AcceptLanguage.ToString() is { Length: >= 2 } l ? l[..2] : null;
        if (viewer is null || runs.Count == 0) return;
        var company = await db.Organizations.Where(o => o.Id == org.OrgId).Select(o => o.Language).FirstAsync();
        var ids = runs.Select(r => r.TenderId).Distinct().ToList();
        var titles = await db.Tenders.Where(t => ids.Contains(t.Id)).ToDictionaryAsync(t => t.Id, t => t.Titles);
        foreach (var r in runs)
        {
            r.Note = AutoBidText.Translate(r.Note, company, viewer);
            if (titles.GetValueOrDefault(r.TenderId)?.GetValueOrDefault(viewer) is { Length: > 0 } title) r.Title = title;
        }
    }

    public static void MapAutoBidEndpoints(this WebApplication app)
    {
        var api = app.MapGroup("/api/autobid");

        api.MapGet("/", async (AppDb db, OrgContext org, TelegramBot bot, HttpRequest request) =>
        {
            org.Require();
            var settings = await db.AutoBidSettings.FirstOrDefaultAsync();
            var runs = await db.AutoBidRuns.AsNoTracking().OrderByDescending(r => r.CreatedAt).Take(100).ToListAsync();
            await InViewerLanguageAsync(db, org, request, runs);
            return new { enabled = settings?.Enabled ?? false, telegram = new { bot.Configured, bot.Connected }, runs };
        });

        api.MapPut("/", async (Toggle body, AppDb db, OrgContext org, ActivityLog log, AutoBidAgent agent) =>
        {
            var orgId = org.Require();
            var settings = await db.AutoBidSettings.FirstOrDefaultAsync();
            if (settings is null) db.AutoBidSettings.Add(settings = new AutoBidSettings { OrgId = orgId });
            settings.Enabled = body.Enabled;
            await db.SaveChangesAsync();
            await log.WriteAsync(orgId, "Auto-bid agent", body.Enabled ? "A person turned auto-bid on" : "A person turned auto-bid off",
                body.Enabled
                    ? "The agents may now take on open tenders the company qualifies for and prepare them up to the signature."
                    : "The agents will not take on new tenders. Bids already waiting for a decision stay as they are.");
            if (body.Enabled) agent.Wake();
            return new { settings.Enabled };
        });

        // The watched demo: the server runs every step, the page only follows it, so a refresh never breaks it.
        api.MapPost("/demo", async Task<IResult> (DemoStart body, OrgContext org, AutoBidAgent agent) =>
        {
            var orgId = org.Require();
            var runId = await agent.StartDemoAsync(orgId, body.TenderId);
            return runId is null ? TypedResults.BadRequest("The company does not qualify for this tender.") : TypedResults.Ok(new { runId });
        });

        api.MapGet("/runs/{id:guid}", async Task<IResult> (Guid id, AppDb db, OrgContext org, HttpRequest request) =>
        {
            org.Require();
            var run = await db.AutoBidRuns.AsNoTracking().FirstOrDefaultAsync(r => r.Id == id);
            if (run is null) return TypedResults.NotFound();
            await InViewerLanguageAsync(db, org, request, [run]);
            return TypedResults.Ok(run);
        });

        api.MapPost("/scan", (OrgContext org, AutoBidAgent agent) =>
        {
            org.Require();
            agent.Wake();
            return TypedResults.Accepted((string?)null);
        });

        api.MapPost("/runs/{id:guid}/decision", async Task<IResult> (Guid id, Decision body, AppDb db, OrgContext org, AutoBidDecisions decisions) =>
        {
            var orgId = org.Require();
            if (!await db.AutoBidRuns.AnyAsync(r => r.Id == id)) return TypedResults.NotFound();
            var me = await db.Organizations.FirstAsync(o => o.Id == orgId);
            return TypedResults.Ok(new { message = await decisions.DecideAsync(id, body.Sign, $"{me.Name} (in the app)") });
        });

        api.MapPost("/telegram/test", async Task<IResult> (AppDb db, OrgContext org, TelegramBot bot) =>
        {
            org.Require();
            if (!bot.Connected) return TypedResults.BadRequest("Telegram is not connected yet: send /start to your bot first.");
            var me = await db.Organizations.FirstAsync(o => o.Id == org.OrgId);
            await bot.SendAsync(new AutoBidText(me.Language)["test"]);
            return TypedResults.Ok();
        });
    }
}
