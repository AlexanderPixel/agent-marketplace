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
    public record Result(bool Won);
    public record DemoStart(string TenderId);

    public static void MapAutoBidEndpoints(this WebApplication app)
    {
        var api = app.MapGroup("/api/autobid");

        api.MapGet("/", async (AppDb db, OrgContext org, TelegramBot bot) =>
        {
            org.Require();
            var settings = await db.AutoBidSettings.FirstOrDefaultAsync();
            var runs = await db.AutoBidRuns.OrderByDescending(r => r.CreatedAt).Take(100).ToListAsync();
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

        api.MapGet("/runs/{id:guid}", async Task<IResult> (Guid id, AppDb db, OrgContext org) =>
        {
            org.Require();
            var run = await db.AutoBidRuns.FirstOrDefaultAsync(r => r.Id == id);
            return run is null ? TypedResults.NotFound() : TypedResults.Ok(run);
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

        // A person records the award. A win becomes a structured reference, so later tenders that ask for experience can use it.
        api.MapPost("/runs/{id:guid}/result", async Task<IResult> (Guid id, Result body, AppDb db, OrgContext org, ActivityLog log, TimeProvider clock) =>
        {
            var orgId = org.Require();
            var run = await db.AutoBidRuns.FirstOrDefaultAsync(r => r.Id == id);
            if (run is null) return TypedResults.NotFound();
            if (run.Stage != AutoBidStage.Signed || run.Result is not null) return TypedResults.BadRequest("Only a signed bid without a result can be marked.");
            run.Result = body.Won ? "won" : "lost";
            run.UpdatedAt = clock.GetUtcNow();
            if (!body.Won)
            {
                await db.SaveChangesAsync();
                await log.WriteAsync(orgId, "Auto-bid agent", $"Marked {run.TenderRef} as lost", "Recorded by a person. Nothing is added to the references.", run.TenderId);
                return TypedResults.Ok(new { message = "Recorded as lost." });
            }

            var tender = await db.Tenders.FirstAsync(t => t.Id == run.TenderId);
            var profile = await db.Profiles.FirstAsync();
            var deal = run.DealId is { } dealId ? await db.Deals.FirstOrDefaultAsync(d => d.Id == dealId) : null;
            var lot = tender.Lots.FirstOrDefault(l => Qualification.QualificationCheck.CheckLot(l, profile).Fit != Qualification.LotFit.NotOffered);
            // Only the company's own share counts as its experience; the partner's quantity is the partner's reference.
            var own = lot is null ? (int?)null : (int)(lot.Quantity ?? 0) - (deal?.Quantity ?? 0);
            var completed = deal?.Delivery ?? DateOnly.FromDateTime((tender.Deadline ?? clock.GetUtcNow()).UtcDateTime).AddMonths(6);
            var reference = new CompanyDocument(
                $"won-{run.TenderRef}", "reference", $"Won: {run.Title}", tender.BuyerName, tender.BuyerCountry,
                DateOnly.FromDateTime(clock.GetUtcNow().UtcDateTime), null,
                $"Created from the won tender {tender.Source} {tender.SourceId}. Ask the buyer for a reference letter after delivery.",
                new DeliveryRecord(tender.BuyerName, lot?.Title ?? run.Title, own, lot?.Unit, null, completed, tender.SourceId,
                    $"Won tender {tender.Source} {tender.SourceId}"));
            profile.Documents = [.. profile.Documents.Where(d => d.Id != reference.Id), reference];
            await db.SaveChangesAsync();
            await log.WriteAsync(orgId, "Auto-bid agent", $"Marked {run.TenderRef} as won and added it to the references",
                $"{own} {lot?.Unit} of {lot?.Title} for {tender.BuyerName}, delivery due {completed:yyyy-MM-dd}. It counts as experience once delivered; " +
                "the contract value is left empty until the award notice states it.", run.TenderId);
            return TypedResults.Ok(new { message = "Recorded as won and added to your references." });
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
