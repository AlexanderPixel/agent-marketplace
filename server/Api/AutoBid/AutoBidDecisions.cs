using Marketplace.Activity;
using Marketplace.Data;
using Marketplace.Deals;
using Marketplace.Tenancy;
using Marketplace.Telegram;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.AutoBid;

/// <summary>
/// A person's Sign / Don't sign on a prepared bid, from Telegram or the app. "Sign" is the human go-ahead (rule 5):
/// it approves the partner deal if it was waiting and is recorded in the activity log. The qualified e-signatures and
/// the submission on the buyer's portal are still done by that person.
/// </summary>
public class AutoBidDecisions(AppDb db, OrgContext org, ActivityLog log, TimeProvider clock, TelegramBot bot)
{
    public async Task<string> DecideAsync(Guid runId, bool sign, string who, CancellationToken ct = default)
    {
        var run = await db.AutoBidRuns.IgnoreQueryFilters().FirstOrDefaultAsync(r => r.Id == runId, ct);
        if (run is null) return "Not found.";
        if (run.Stage != AutoBidStage.AwaitingSignature) return "Already decided.";
        org.OrgId = run.OrgId;

        var tender = await db.Tenders.FirstAsync(t => t.Id == run.TenderId, ct);
        var deal = run.DealId is { } dealId ? await db.Deals.FirstOrDefaultAsync(d => d.Id == dealId, ct) : null;
        var now = clock.GetUtcNow();
        var portal = tender.DocumentsUrl is { } u && Uri.TryCreate(u, UriKind.Absolute, out var uri) ? uri.Host : "the buyer's portal";

        if (sign)
        {
            if (deal?.Status == DealStatus.AwaitingApproval)
            {
                await DealOps.ApproveAsync(db, log, clock, deal, run.OrgId, who);
                await DealOps.DraftDocumentsAsync(db, log, clock, deal, run.OrgId);
            }
            run.Stage = AutoBidStage.Signed;
            run.Note = $"Approved for signing by {who}. Next: qualified e-signatures and submission on {portal}, done by a person.";
            await log.WriteAsync(run.OrgId, "Auto-bid agent", $"{who} approved the bid for {tender.SourceId}",
                "A person decided; the agents only prepared the bid. Signing with qualified e-signatures and submitting stay with people.",
                tender.Id, ct: ct);
        }
        else
        {
            run.Stage = AutoBidStage.Declined;
            run.Note = $"Declined by {who}. Nothing will be signed or submitted.";
            await log.WriteAsync(run.OrgId, "Auto-bid agent", $"{who} declined the bid for {tender.SourceId}",
                "A person decided not to go ahead. The prepared drafts stay in the app for reference.", tender.Id, ct: ct);
        }

        run.DecidedAt = run.UpdatedAt = now;
        run.DecidedBy = who;
        await db.SaveChangesAsync(ct);
        if (run.TelegramMessageId is { } messageId)
            await bot.MarkDecidedAsync(messageId, sign ? $"✅ Signed off by {who}" : $"❌ Declined by {who}", ct);
        return sign ? "Recorded: go ahead and sign." : "Recorded: not signing.";
    }
}
