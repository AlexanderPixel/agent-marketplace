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
public class AutoBidAgent(IServiceScopeFactory scopes, TelegramBot bot, TimeProvider clock, IConfiguration config, IHttpClientFactory http,
    ILogger<AutoBidAgent> logger)
    : BackgroundService
{
    private const string Agent = "Auto-bid agent";
    private readonly SemaphoreSlim wake = new(0);
    private readonly SemaphoreSlim busy = new(1, 1);
    private TimeSpan Interval => TimeSpan.FromSeconds(config.GetValue("AutoBid:IntervalSeconds", 60));
    private TimeSpan ResultInterval => TimeSpan.FromMinutes(config.GetValue("AutoBid:ResultCheckMinutes", 360));

    /// <summary>Runs a scan now instead of waiting for the next interval.</summary>
    public void Wake() => wake.Release();

    /// <summary>
    /// Starts a watched demo run on one tender, independent of the auto-bid switch. It runs on the server, so closing or
    /// refreshing the page never stops it; the page just follows the run. Returns the run, or null if the company does not
    /// qualify for this tender. An open run is returned instead of starting a second one.
    /// </summary>
    public async Task<Guid?> StartDemoAsync(Guid orgId, string tenderId)
    {
        using (var scope = scopes.CreateScope())
        {
            scope.ServiceProvider.GetRequiredService<OrgContext>().OrgId = orgId;
            var open = await scope.ServiceProvider.GetRequiredService<AppDb>().AutoBidRuns
                .Where(r => r.Stage != AutoBidStage.Signed && r.Stage != AutoBidStage.Declined && r.Stage != AutoBidStage.Stopped)
                .OrderByDescending(r => r.CreatedAt).FirstOrDefaultAsync();
            if (open is not null) return open.Id;
        }

        var created = new TaskCompletionSource<Guid?>(TaskCreationOptions.RunContinuationsAsynchronously);
        _ = Task.Run(async () =>
        {
            try
            {
                await WorkForAsync(orgId, CancellationToken.None, tenderId, created);
            }
            catch (Exception e)
            {
                logger.LogError(e, "Demo run failed");
            }
            finally
            {
                created.TrySetResult(null);
            }
        });
        return await created.Task;
    }

    private static Task Beat(AutoBidRun run, int ms, CancellationToken ct) => run.Demo ? Task.Delay(ms, ct) : Task.CompletedTask;

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

            try
            {
                await CheckResultsAsync(stop);
            }
            catch (Exception e) when (!stop.IsCancellationRequested)
            {
                logger.LogError(e, "Award result check failed");
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

    /// <summary>
    /// Signed bids whose deadline has passed: looks for the buyer's award notice and records won or lost from it, so nobody
    /// has to. Each bid is checked at most once per <see cref="ResultInterval"/>; no notice yet means it keeps waiting.
    /// </summary>
    private async Task CheckResultsAsync(CancellationToken ct)
    {
        var now = clock.GetUtcNow();
        List<(Guid Id, Guid OrgId)> due;
        using (var scope = scopes.CreateScope())
        {
            var db = scope.ServiceProvider.GetRequiredService<AppDb>();
            var deadlines = db.Tenders.Where(t => t.Deadline < now).Select(t => t.Id);
            due = (await db.AutoBidRuns.IgnoreQueryFilters()
                    .Where(r => r.Stage == AutoBidStage.Signed && r.Result == null && deadlines.Contains(r.TenderId))
                    .Where(r => r.ResultCheckedAt == null || r.ResultCheckedAt < now - ResultInterval)
                    .Select(r => new { r.Id, r.OrgId }).ToListAsync(ct))
                .Select(r => (r.Id, r.OrgId)).ToList();
        }

        foreach (var (id, orgId) in due)
        {
            using var scope = scopes.CreateScope();
            var sp = scope.ServiceProvider;
            sp.GetRequiredService<OrgContext>().OrgId = orgId;
            var db = sp.GetRequiredService<AppDb>();
            var run = await db.AutoBidRuns.FirstAsync(r => r.Id == id, ct);
            var tender = await db.Tenders.FirstAsync(t => t.Id == run.TenderId, ct);
            var company = await db.Organizations.FirstAsync(o => o.Id == orgId, ct);
            var profile = await db.Profiles.FirstAsync(ct);

            var notices = await AwardResults.FindAsync(http.CreateClient(), tender, ct);
            var notice = AwardResults.Deciding(notices, AwardResults.OfferedLots(tender, profile).Select(l => l.Id).ToList());
            run.ResultCheckedAt = now;
            if (notice is null)
            {
                await db.SaveChangesAsync(ct);
                continue;
            }

            var won = AwardResults.Names(notice, company.Name);
            var tx = new AutoBidText(company.Language);
            run.Note = won ? tx["noteWon", notice.PublicationNumber]
                : notice.Winners.Count == 0 ? tx["noteNoAward", notice.PublicationNumber]
                : tx["noteLost", string.Join(", ", notice.Winners.Distinct()), notice.PublicationNumber];
            await AwardResults.RecordAsync(db, sp.GetRequiredService<ActivityLog>(), clock, run, won, notice, ct);
            await bot.SendAsync($"<b>{H(tx[won ? "won" : "lost"])}</b>: {H(run.Title)}\n{H(run.Note)}", ct: ct);
        }
    }

    /// <summary>One company: at most one tender in flight at a time, so a person is never flooded with requests.</summary>
    private async Task WorkForAsync(Guid orgId, CancellationToken ct, string? demoTender = null, TaskCompletionSource<Guid?>? created = null)
    {
        using var scope = scopes.CreateScope();
        var sp = scope.ServiceProvider;
        sp.GetRequiredService<OrgContext>().OrgId = orgId;
        var db = sp.GetRequiredService<AppDb>();
        var log = sp.GetRequiredService<ActivityLog>();

        var runs = await db.AutoBidRuns.ToListAsync(ct);
        if (demoTender is null && (!(await db.AutoBidSettings.FirstOrDefaultAsync(ct))?.Enabled ?? true)) return;
        if (runs.Any(r => r.IsOpen)) return;

        var company = await db.Organizations.FirstAsync(o => o.Id == orgId, ct);
        var tx = new AutoBidText(company.Language);
        var profile = await db.Profiles.FirstOrDefaultAsync(ct);
        if (profile is null) return;
        var now = clock.GetUtcNow();
        var seen = demoTender is null ? runs.Select(r => r.TenderId).ToHashSet() : [];
        var open = demoTender is null
            ? await db.Tenders.Where(t => t.Deadline > now.AddDays(2)).OrderByDescending(t => t.Featured).ThenBy(t => t.Deadline).ToListAsync(ct)
            : await db.Tenders.Where(t => t.Id == demoTender).ToListAsync(ct);

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
            Stage = AutoBidStage.Qualifying, CreatedAt = now, UpdatedAt = now, Demo = demoTender is not null,
            LotId = a.Gaps.FirstOrDefault()?.Lot.Id, Gap = a.Gaps.FirstOrDefault()?.Gap,
            Note = a.Verdict == "can-supply" ? tx["noteQualifies"] : tx["noteGap", tx.Qty(a.Gaps[0].Gap, a.Gaps[0].Lot.Unit), a.Gaps[0].Lot.Id],
        };
        db.AutoBidRuns.Add(run);
        await db.SaveChangesAsync(ct);
        created?.TrySetResult(run.Id);
        await log.WriteAsync(orgId, Agent, $"Took on {tender.SourceId}: {run.Title}",
            $"Auto-bid is on, the tender is open until {tender.Deadline:yyyy-MM-dd} and the qualification check says {a.Verdict} " +
            $"({a.Requirements.Count - a.Red - a.Amber} met, {a.Amber} via equivalent, 0 red).", tender.Id, Trace.Qualification(tender, a), ct: ct);

        try
        {
            await PrepareAsync(sp, db, log, tx, run, tender, a, ct);
        }
        catch (Exception e)
        {
            logger.LogError(e, "Auto-bid run {Run} failed", run.Id);
            await StopAsync(db, log, tx, run, tx["noteError"], ct);
        }
    }

    private async Task PrepareAsync(IServiceProvider sp, AppDb db, ActivityLog log, AutoBidText tx, AutoBidRun run, Tender tender, Assessment a, CancellationToken ct)
    {
        Deal? deal = null;
        if (a.Verdict == "partner-needed")
        {
            var gap = a.Gaps[0];
            await Beat(run, 4000, ct);
            await StageAsync(db, run, AutoBidStage.FindingPartner, tx["noteSearching", tx.Qty(gap.Gap, gap.Lot.Unit)], ct);
            var candidates = await sp.GetRequiredService<PartnerSearch>().SearchAsync(run.OrgId, tender, gap.Lot, gap.Gap!.Value);
            var best = candidates.FirstOrDefault(c => c.GatesPassed);
            if (best is null)
            {
                await StopAsync(db, log, tx, run, tx["noteNoPartner"], ct);
                return;
            }

            await Beat(run, 5000, ct);
            await StageAsync(db, run, AutoBidStage.Negotiating, tx["noteNegotiating", best.Partner.Name], ct);
            deal = await DealOps.OpenAsync(db, log, sp.GetRequiredService<DealRunner>(), clock, run.OrgId, tender, gap.Lot, gap.Gap.Value, best);
            run.DealId = deal.Id;
            deal = await WaitForDealAsync(db, deal.Id, ct);
            if (deal.Status == DealStatus.Failed)
            {
                await StopAsync(db, log, tx, run, tx["noteNoDeal"], ct);
                return;
            }

            if (deal.Status == DealStatus.Closed)
            {
                await Beat(run, 3500, ct);
                await StageAsync(db, run, AutoBidStage.Drafting, tx["noteDrafting"], ct);
                await Beat(run, 2000, ct);
                await DealOps.DraftDocumentsAsync(db, log, clock, deal, run.OrgId);
            }
        }

        var company = await db.Organizations.FirstAsync(o => o.Id == run.OrgId, ct);
        var summary = await sp.GetRequiredService<TenderSummaryService>().SummariseAsync(tender, tx.Language, ct);
        var partner = deal is null ? null : await db.Organizations.FirstAsync(o => o.Id == deal.PartnerOrgId, ct);
        await StageAsync(db, run, AutoBidStage.AwaitingSignature,
            deal?.Status == DealStatus.AwaitingApproval ? tx["noteReadyApprove"] : tx["noteReady"], ct);

        var (message, keyboard) = (Compose(tx, run, tender, a, summary, deal, partner), Keyboard(tx, run, tender));
        run.TelegramMessageId = await bot.SendAsync(message, keyboard, ct);
        await db.SaveChangesAsync(ct);
        await log.WriteAsync(run.OrgId, Agent,
            run.TelegramMessageId is null ? "Bid ready; waiting for a decision in the app" : "Bid ready; sent a sign request to Telegram",
            "Everything up to the signature is prepared. Signing and submitting stay with a person (rule 5).", tender.Id,
            Trace.Telegram(message, keyboard, run.TelegramMessageId), ct: ct);
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

    private async Task StopAsync(AppDb db, ActivityLog log, AutoBidText tx, AutoBidRun run, string why, CancellationToken ct)
    {
        await StageAsync(db, run, AutoBidStage.Stopped, why, ct);
        await log.WriteAsync(run.OrgId, Agent, $"Stopped working on {run.TenderRef}", why, run.TenderId, ct: ct);
        await bot.SendAsync($"<b>{H(tx["stopped"])}</b>: {H(run.Title)}\n{H(why)}", ct: ct);
    }

    private object[][] Keyboard(AutoBidText tx, AutoBidRun run, Tender tender)
    {
        var rows = new List<object[]> { new[] { Button(tx["sign"], $"sign:{run.Id}"), Button(tx["decline"], $"decline:{run.Id}") } };
        var links = new List<object>();
        // Telegram only accepts public http(s) links on buttons; local app links stay in the message text.
        if (!bot.AppBaseUrl.Contains("localhost") && !bot.AppBaseUrl.Contains("127.0.0.1"))
            links.Add(Link(tx["open"], $"{bot.AppBaseUrl}/tenders/{Uri.EscapeDataString(tender.Id)}"));
        if (tender.NoticeUrl is { } notice) links.Add(Link(tx["notice"], notice));
        if (links.Count > 0) rows.Add([.. links]);
        return [.. rows];
    }

    /// <summary>The sign request: short, and entirely in the company's language (titles, units, numbers, dates, summary).</summary>
    private string Compose(AutoBidText tx, AutoBidRun run, Tender tender, Assessment a, TenderSummaryText summary, Deal? deal, Organization? partner)
    {
        // Notice titles end with the procedure reference, which the second line already shows.
        var title = tender.Reference is { } r && run.Title.EndsWith(r) ? run.Title[..^r.Length].TrimEnd(' ', '–', '-') : run.Title;
        var days = tender.Deadline is { } dl ? (int)Math.Ceiling((dl - clock.GetUtcNow()).TotalDays) : (int?)null;
        var lines = new List<string>
        {
            $"🛡 <b>{H(tx["ready"])}: {H(title)}</b>",
            $"{H(tender.BuyerName)}{(tender.BuyerCity is null ? "" : ", " + H(tender.BuyerCity))} · TED {H(tender.SourceId)}",
            $"{H(tx["deadline"])}: <b>{H(tender.Deadline is { } d ? tx.DateTime(d) : "—")}</b>{(days is null ? "" : $" ({H(tx["daysLeft", days])})")}",
            "",
        };

        foreach (var l in a.Lots)
        {
            var lotNo = int.TryParse(new string(l.Lot.Id.Where(char.IsDigit).ToArray()), out var n) ? n.ToString() : l.Lot.Id;
            var split = deal is not null && deal.LotId == l.Lot.Id ? $" ({tx["youMake", tx.Qty(l.Capacity, l.Lot.Unit), tx.Qty(deal.Quantity, deal.Unit)]})" : "";
            lines.Add($"<b>{H(tx["part"])}</b>: {H(tx["lot", lotNo])} – {H(l.Product?.Name ?? l.Lot.Title)}, {H(tx.Qty(l.Lot.Quantity, l.Lot.Unit))}{H(split)}");
        }

        if (deal is not null && partner is not null)
        {
            lines.Add($"<b>{H(tx["partner"])}</b>: {H(partner.Name)} ({H(partner.Country)}) · {H(tx.Qty(deal.Quantity, deal.Unit))} × {H(tx.Eur(deal.UnitPrice))} = " +
                      $"{H(tx.Eur(deal.UnitPrice * deal.Quantity, 0))} · {H(tx["delivery", deal.Delivery is { } dd ? tx.Date(dd) : "—"])}");
            lines.Add(H(deal.Status == DealStatus.Closed ? tx["closed", deal.Documents.Count] : tx["approve"]));
        }

        lines.Add($"<b>{H(tx["requirements"])}</b>: {H(a.Amber > 0 ? tx["reqEquivalent", a.Amber] : tx["reqMet"])}");
        lines.Add("");
        lines.Add($"<b>{H(tx["summary"])}</b>");
        lines.AddRange(summary.Points.Take(3).Select(p => $"• {H(p.Text)}"));
        lines.Add("");
        var details = $"{bot.AppBaseUrl}/tenders/{Uri.EscapeDataString(tender.Id)}";
        lines.Add(deal is null
            ? $"<a href=\"{H(details)}\">{H(tx["details"])}</a>"
            : $"<a href=\"{H(details)}\">{H(tx["details"])}</a> · <a href=\"{H($"{bot.AppBaseUrl}/deals/{deal.Id}/documents")}\">{H(tx["documents"])}</a>");
        lines.Add($"<i>{H(tx["footer"])}</i>");

        var text = string.Join("\n", lines);
        return text.Length <= 4000 ? text : text[..3990] + "…";
    }
}
