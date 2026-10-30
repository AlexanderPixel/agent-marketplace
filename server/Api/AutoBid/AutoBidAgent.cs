using System.Globalization;
using Marketplace.Activity;
using Marketplace.Data;
using Marketplace.Deals;
using Marketplace.Partners;
using Marketplace.Qualification;
using Marketplace.Tenancy;
using Marketplace.Telegram;
using Microsoft.EntityFrameworkCore;
using static Marketplace.Telegram.TelegramBot;

namespace Marketplace.AutoBid;

/// <summary>
/// Works tenders on its own for companies that turned auto-bid on: finds an open tender the company qualifies for,
/// covers a quantity gap with a partner (the agents negotiate inside both mandates), drafts the documents, then stops
/// and asks a person to sign. Every step goes to the activity log (rule 1); hard limits stay enforced by the deal engine.
/// </summary>
public class AutoBidAgent(IServiceScopeFactory scopes, TelegramBot bot, TimeProvider clock, IConfiguration config, ILogger<AutoBidAgent> logger)
    : BackgroundService
{
    private const string Agent = "Auto-bid agent";
    private readonly SemaphoreSlim wake = new(0);
    private readonly SemaphoreSlim busy = new(1, 1);
    private TimeSpan Interval => TimeSpan.FromSeconds(config.GetValue("AutoBid:IntervalSeconds", 60));

    /// <summary>Runs a scan now instead of waiting for the next interval.</summary>
    public void Wake() => wake.Release();

    protected override async Task ExecuteAsync(CancellationToken stop)
    {
        while (!stop.IsCancellationRequested)
        {
            try
            {
                await ScanAsync(stop);
            }
            catch (Exception e) when (!stop.IsCancellationRequested)
            {
                logger.LogError(e, "Auto-bid scan failed");
            }
            await wake.WaitAsync(Interval, stop).ContinueWith(_ => { }, TaskScheduler.Default);
        }
    }

    private async Task ScanAsync(CancellationToken ct)
    {
        if (!await busy.WaitAsync(0, ct)) return;
        try
        {
            List<Guid> orgIds;
            using (var scope = scopes.CreateScope())
                orgIds = await scope.ServiceProvider.GetRequiredService<AppDb>().AutoBidSettings.IgnoreQueryFilters()
                    .Where(s => s.Enabled).Select(s => s.OrgId).ToListAsync(ct);
            foreach (var orgId in orgIds) await WorkForAsync(orgId, ct);
        }
        finally
        {
            busy.Release();
        }
    }

    /// <summary>One company: at most one tender in flight at a time, so a person is never flooded with requests.</summary>
    private async Task WorkForAsync(Guid orgId, CancellationToken ct)
    {
        using var scope = scopes.CreateScope();
        var sp = scope.ServiceProvider;
        sp.GetRequiredService<OrgContext>().OrgId = orgId;
        var db = sp.GetRequiredService<AppDb>();
        var log = sp.GetRequiredService<ActivityLog>();

        if (!(await db.AutoBidSettings.FirstOrDefaultAsync(ct))?.Enabled ?? true) return;
        var runs = await db.AutoBidRuns.ToListAsync(ct);
        if (runs.Any(r => r.IsOpen)) return;

        var company = await db.Organizations.FirstAsync(o => o.Id == orgId, ct);
        var profile = await db.Profiles.FirstOrDefaultAsync(ct);
        if (profile is null) return;
        var now = clock.GetUtcNow();
        var seen = runs.Select(r => r.TenderId).ToHashSet();
        var open = await db.Tenders.Where(t => t.Deadline > now.AddDays(2)).OrderByDescending(t => t.Featured).ThenBy(t => t.Deadline).ToListAsync(ct);

        // Conservative (rule 7): act alone only when a specific lot matches a product, never on a category match.
        var pick = open.Where(t => !seen.Contains(t.Id))
            .Select(t => (Tender: t, A: QualificationCheck.Assess(t, profile, company.Country)))
            .FirstOrDefault(x => x.A.Verdict is "can-supply" or "partner-needed" && x.A.Lots.Count > 0);
        if (pick.Tender is null) return;

        var (tender, a) = pick;
        var run = new AutoBidRun
        {
            Id = Guid.NewGuid(), OrgId = orgId, TenderId = tender.Id, TenderRef = tender.SourceId,
            Title = tender.Titles.GetValueOrDefault(company.Language) ?? tender.Title, Verdict = a.Verdict,
            Stage = AutoBidStage.Qualifying, CreatedAt = now, UpdatedAt = now,
            Note = a.Verdict == "can-supply" ? "Qualifies and has the capacity." : $"Qualifies; short by {a.Gaps[0].Gap} {a.Gaps[0].Lot.Unit} on {a.Gaps[0].Lot.Id}.",
        };
        db.AutoBidRuns.Add(run);
        await db.SaveChangesAsync(ct);
        await log.WriteAsync(orgId, Agent, $"Took on {tender.SourceId}: {run.Title}",
            $"Auto-bid is on, the tender is open until {tender.Deadline:yyyy-MM-dd} and the qualification check says {a.Verdict} " +
            $"({a.Requirements.Count - a.Red - a.Amber} met, {a.Amber} via equivalent, 0 red). {run.Note}", tender.Id, ct: ct);

        try
        {
            await PrepareAsync(sp, db, log, run, tender, a, ct);
        }
        catch (Exception e)
        {
            logger.LogError(e, "Auto-bid run {Run} failed", run.Id);
            await StopAsync(db, log, run, "Something went wrong while preparing the bid; nothing was sent.", ct);
        }
    }

    private async Task PrepareAsync(IServiceProvider sp, AppDb db, ActivityLog log, AutoBidRun run, Tender tender, Assessment a, CancellationToken ct)
    {
        Deal? deal = null;
        if (a.Verdict == "partner-needed")
        {
            var gap = a.Gaps[0];
            await StageAsync(db, run, AutoBidStage.FindingPartner, $"Looking for a partner for {gap.Gap} {gap.Lot.Unit}.", ct);
            var candidates = await sp.GetRequiredService<PartnerSearch>().SearchAsync(run.OrgId, tender, gap.Lot, gap.Gap!.Value);
            var best = candidates.FirstOrDefault(c => c.GatesPassed);
            if (best is null)
            {
                await StopAsync(db, log, run, "No partner passed the hard gates, so the bid cannot be completed.", ct);
                return;
            }

            await StageAsync(db, run, AutoBidStage.Negotiating, $"Negotiating with {best.Partner.Name} inside your limits.", ct);
            deal = await DealOps.OpenAsync(db, log, sp.GetRequiredService<DealRunner>(), clock, run.OrgId, tender, gap.Lot, gap.Gap.Value, best);
            run.DealId = deal.Id;
            deal = await WaitForDealAsync(db, deal.Id, ct);
            if (deal.Status == DealStatus.Failed)
            {
                await StopAsync(db, log, run, $"The agents found no deal inside the limits: {deal.Outcome}", ct);
                return;
            }

            if (deal.Status == DealStatus.Closed)
            {
                await StageAsync(db, run, AutoBidStage.Drafting, "Deal closed. Drafting the agreement documents.", ct);
                await DealOps.DraftDocumentsAsync(db, log, clock, deal, run.OrgId);
            }
        }

        var company = await db.Organizations.FirstAsync(o => o.Id == run.OrgId, ct);
        var summary = await sp.GetRequiredService<TenderSummaryService>().SummariseAsync(tender, company.Language, ct);
        var partner = deal is null ? null : await db.Organizations.FirstAsync(o => o.Id == deal.PartnerOrgId, ct);
        await StageAsync(db, run, AutoBidStage.AwaitingSignature,
            deal?.Status == DealStatus.AwaitingApproval
                ? "Ready. The partner deal is below your autonomy threshold, so your Sign also approves it."
                : "Ready to sign. Waiting for your decision.", ct);

        run.TelegramMessageId = await bot.SendAsync(Compose(run, tender, a, summary, deal, partner), Keyboard(run, tender), ct);
        await db.SaveChangesAsync(ct);
        await log.WriteAsync(run.OrgId, Agent,
            run.TelegramMessageId is null ? "Bid ready; waiting for a decision in the app" : "Bid ready; sent a sign request to Telegram",
            "Everything up to the signature is prepared. Signing and submitting stay with a person (rule 5).", tender.Id, ct: ct);
    }

    private static async Task<Deal> WaitForDealAsync(AppDb db, Guid dealId, CancellationToken ct)
    {
        // The deal runner writes from its own scope; reload so this context does not keep its stale copy.
        var deal = await db.Deals.FirstAsync(x => x.Id == dealId, ct);
        for (var i = 0; i < 180 && deal.Status == DealStatus.Negotiating; i++)
        {
            await Task.Delay(TimeSpan.FromSeconds(1), ct);
            await db.Entry(deal).ReloadAsync(ct);
        }
        return deal;
    }

    private async Task StageAsync(AppDb db, AutoBidRun run, AutoBidStage stage, string note, CancellationToken ct)
    {
        (run.Stage, run.Note, run.UpdatedAt) = (stage, note, clock.GetUtcNow());
        await db.SaveChangesAsync(ct);
    }

    private async Task StopAsync(AppDb db, ActivityLog log, AutoBidRun run, string why, CancellationToken ct)
    {
        await StageAsync(db, run, AutoBidStage.Stopped, why, ct);
        await log.WriteAsync(run.OrgId, Agent, $"Stopped working on {run.TenderRef}", why, run.TenderId, ct: ct);
        await bot.SendAsync($"⏸ <b>Auto-bid stopped</b>: {H(run.Title)}\n{H(why)}", ct: ct);
    }

    private object[][] Keyboard(AutoBidRun run, Tender tender)
    {
        var rows = new List<object[]> { new[] { Button("✅ Sign", $"sign:{run.Id}"), Button("❌ Don't sign", $"decline:{run.Id}") } };
        var links = new List<object>();
        // Telegram only accepts public http(s) links on buttons; local app links stay in the message text.
        if (!bot.AppBaseUrl.Contains("localhost") && !bot.AppBaseUrl.Contains("127.0.0.1"))
            links.Add(Link("Open in EU Patriot", $"{bot.AppBaseUrl}/tenders/{Uri.EscapeDataString(tender.Id)}"));
        if (tender.NoticeUrl is { } notice) links.Add(Link("Notice on TED", notice));
        if (links.Count > 0) rows.Add([.. links]);
        return [.. rows];
    }

    private string Compose(AutoBidRun run, Tender tender, Assessment a, TenderSummaryText summary, Deal? deal, Organization? partner)
    {
        var en = CultureInfo.GetCultureInfo("en-GB");
        var days = tender.Deadline is { } dl ? (int)Math.Ceiling((dl - clock.GetUtcNow()).TotalDays) : (int?)null;
        var lines = new List<string>
        {
            $"🛡 <b>Ready to sign: {H(run.Title)}</b>",
            $"{H(tender.BuyerName)}{(tender.BuyerCity is null ? "" : ", " + H(tender.BuyerCity))} ({H(tender.BuyerCountry)}) · {H(tender.Source.ToString())} {H(tender.SourceId)}",
            $"Deadline: <b>{tender.Deadline?.ToString("d MMM yyyy, HH:mm", en)} UTC</b>{(days is null ? "" : $" ({days} days left)")}",
            "",
            $"<b>Your part</b>",
        };
        lines.AddRange(a.Lots.Select(l => $"• {H(l.Lot.Id)} {H(l.Lot.Title)}: {l.Lot.Quantity:0} {H(l.Lot.Unit)} (you make {l.Capacity})"));
        lines.Add($"Requirements: {a.Requirements.Count - a.Red - a.Amber} met, {a.Amber} via an equivalent document, 0 missing.");

        if (deal is not null && partner is not null)
        {
            lines.Add("");
            lines.Add("<b>Partner deal</b>");
            lines.Add($"{H(partner.Name)} ({H(partner.Country)}): {deal.Quantity} {H(deal.Unit)} at €{deal.UnitPrice?.ToString("N2", en)}" +
                      $" = €{(deal.UnitPrice * deal.Quantity)?.ToString("N0", en)}, delivery {deal.Delivery?.ToString("d MMM yyyy", en)}");
            lines.Add(deal.Status == DealStatus.Closed
                ? $"Closed by the agents inside both mandates ({deal.MatchScore}% match). {deal.Documents.Count} drafts in PL and DE."
                : $"Agreed by the agents but below your autonomy threshold ({deal.MatchScore}% match): <b>Sign also approves this deal.</b>");
        }

        lines.Add("");
        lines.Add($"<b>Summary</b>{(summary.ByAi ? " (AI)" : "")}");
        lines.Add(H(summary.Headline));
        lines.AddRange(summary.Points.Take(5).Select(p => $"• {H(p.Text)}"));

        lines.Add("");
        lines.Add($"Details: {H(bot.AppBaseUrl)}/tenders/{H(Uri.EscapeDataString(tender.Id))}");
        if (deal is not null) lines.Add($"Documents: {H(bot.AppBaseUrl)}/deals/{deal.Id}/documents");
        lines.Add("");
        lines.Add("<i>Sign records your go-ahead. The qualified e-signatures and the submission on the buyer's portal stay with you.</i>");

        var text = string.Join("\n", lines);
        return text.Length <= 4000 ? text : text[..3990] + "…";
    }
}
