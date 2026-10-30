using Marketplace.Activity;
using Marketplace.Data;
using Marketplace.Qualification;
using Marketplace.Tenancy;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Endpoints;

public static class QualificationEndpoints
{
    public static void MapQualificationEndpoints(this WebApplication app)
    {
        app.MapGet("/api/profile", async (AppDb db, OrgContext org) =>
        {
            org.Require();
            return await db.Profiles.FirstOrDefaultAsync();
        });

        app.MapPost("/api/tenders/{id}/analysis", async Task<IResult> (
            string id, HttpRequest request, AppDb db, OrgContext org, ActivityLog log,
            TenderSummaryService summaries, CancellationToken ct) =>
        {
            var orgId = org.Require();
            var tender = await db.Tenders.FindAsync([id], ct);
            if (tender is null) return TypedResults.NotFound();
            var company = await db.Organizations.FirstAsync(o => o.Id == orgId, ct);
            var profile = await db.Profiles.FirstOrDefaultAsync(ct) ?? new CompanyProfile { OrgId = orgId };

            var language = request.Headers.AcceptLanguage.ToString() is { Length: >= 2 } l ? l[..2] : company.Language;
            var summary = await summaries.SummariseAsync(tender, language, ct);
            await log.WriteAsync(orgId, "Tender reader",
                $"Summarised {tender.SourceId} in {language} ({summary.Points.Count} cited points)",
                tender.Language == language
                    ? "You opened the tender; each point cites the notice field it comes from."
                    : $"The notice is in '{tender.Language}' and you read '{language}'; each point cites the original field.",
                subject: tender.Id, ct: ct);

            var a = QualificationCheck.Assess(tender, profile, company.Country);
            await log.WriteAsync(orgId, "Qualification agent",
                $"Checked {a.Requirements.Count} requirements and {tender.Lots.Count} lots for {tender.SourceId}: " +
                $"{a.Requirements.Count - a.Red - a.Amber} met, {a.Amber} via equivalent, {a.Red} red" +
                (a.Gaps.Count > 0 ? $"; {string.Join(", ", a.Gaps.Select(g => $"{g.Lot.Id} short by {g.Gap} {g.Lot.Unit}"))}" : "") +
                $". Verdict: {a.Verdict}",
                "Compared every requirement with the documents in your profile and every lot with your product capacity. " +
                "Anything that could not be matched to a document is marked red, never green.",
                subject: tender.Id, data: new { a.Verdict, a.Red, a.Amber }, ct: ct);

            return TypedResults.Ok(new { summary, a.Requirements, a.Lots, a.NotOffered, a.Verdict });
        });
    }
}
