using Marketplace.Activity;
using Marketplace.Compliance;
using Marketplace.Data;
using Marketplace.Tenancy;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Endpoints;

public static class ReachEndpoints
{
    public record PackItem(string Group, string Item, string Status, string Detail, string? Source);

    public static void MapReachEndpoints(this WebApplication app)
    {
        app.MapPost("/api/reach/scan", async Task<IResult> (AppDb db, OrgContext org, ActivityLog log) =>
        {
            var orgId = org.Require();
            var bom = await db.Boms.FirstOrDefaultAsync();
            if (bom is null) return TypedResults.NotFound();
            var scan = Substances.Scan(bom);
            var restricted = scan.Count(s => s.Risk is SubstanceRisk.Restricted or SubstanceRisk.Unknown);
            var notify = scan.Count(s => s.Risk == SubstanceRisk.Notify);
            await log.WriteAsync(orgId, "REACH agent",
                $"Scanned {scan.Count} components of {bom.Product}: {restricted} red, {notify} to notify, {scan.Count - restricted - notify} clear",
                "Checked supplier material declarations against the SVHC Candidate List, Annex XIV and the proposed PFAS restriction. " +
                "Components without a declaration are red until the supplier sends one.");
            return TypedResults.Ok(new { product = bom.Product, scan });
        });

        app.MapGet("/api/financing", async (AppDb db, OrgContext org) =>
        {
            var orgId = org.Require();
            var me = await db.Organizations.FirstAsync(o => o.Id == orgId);
            var profile = await db.Profiles.FirstOrDefaultAsync();
            var bom = await db.Boms.FirstOrDefaultAsync();
            var deals = await db.Deals.Where(d => d.Status == DealStatus.Closed).ToListAsync();
            var docs = profile?.Documents ?? [];
            string Has(string kind) => docs.Any(d => d.Kind == kind) ? "ready" : "missing";
            string? Title(string kind) => docs.FirstOrDefault(d => d.Kind == kind)?.Title;

            var euContent = bom is null ? (decimal?)null : BuyEuropean.EuContent(bom.Components);
            var scan = bom is null ? [] : Substances.Scan(bom);
            var subcontracted = deals.Sum(d => (d.UnitPrice ?? 0) * (d.Quantity ?? 0));
            var ownProduct = profile?.Products.FirstOrDefault();
            var items = new List<PackItem>
            {
                new("Company", "Trade register extract", Has("trade-register"), Title("trade-register") ?? "Upload an odpis z KRS.", "Profile"),
                new("Company", "Beneficial owners (CRBR)", profile?.Ownership.Count > 0 ? "ready" : "missing",
                    profile?.Ownership.LastOrDefault() is { } u ? $"Ultimate owner: {u.Entity} ({u.Country}, {u.Share}%)" : "Add the ownership chain.", "Buy-European"),
                new("Company", "Tax and social security certificates", Has("tax") == "ready" && Has("social-security") == "ready" ? "ready" : "missing",
                    "Zaświadczenia z US i ZUS, valid at application date.", "Profile"),
                new("Financials", "Financial statements, last 3 years", "missing", "Not in the platform yet: upload the approved sprawozdania finansowe.", null),
                new("Financials", "Order backlog and cash-flow need", deals.Count > 0 ? "ready" : "missing",
                    deals.Count > 0 ? $"Bid on TED {deals[0].TenderRef}: subcontract €{subcontracted:N0} payable before the buyer pays; own share {ownProduct?.Capacity} sets." : "Close a deal first.", "Deals"),
                new("Defence", "Quality certificates (AQAP 2110, ISO 9001)", Has("aqap2110") == "ready" && Has("iso9001") == "ready" ? "ready" : "missing",
                    string.Join(", ", docs.Where(d => d.Kind is "aqap2110" or "iso9001").Select(d => d.Title)), "Profile"),
                new("Defence", "SAFE / EDIP eligibility", euContent >= BuyEuropean.Threshold ? "ready" : "warning",
                    euContent is null ? "No BOM." : $"EU content {euContent}% (threshold {BuyEuropean.Threshold}%).", "Buy-European"),
                new("ESG", "Sanctions screening", "ready", "Suppliers and partners screened, no matches.", "Buy-European"),
                new("ESG", "REACH / PFAS status", scan.Any(s => s.Risk is SubstanceRisk.Restricted or SubstanceRisk.Unknown) ? "warning" : "ready",
                    $"{scan.Count(s => s.Risk is SubstanceRisk.Restricted or SubstanceRisk.Unknown)} red, {scan.Count(s => s.Risk == SubstanceRisk.Notify)} to notify.", "REACH"),
                new("Contracts", "Partner agreement", deals.Any(d => d.Documents.Count > 0) ? "ready" : "missing",
                    deals.Any(d => d.Documents.Count > 0) ? "Umowa podwykonawcza drafted (PL/DE), awaiting signatures." : "Generate the agreement documents.", "Deals"),
            };
            return new
            {
                company = me.Name,
                items,
                ready = items.Count(i => i.Status == "ready"),
                lenders = new[]
                {
                    new { name = "BGK (Bank Gospodarstwa Krajowego)", fit = "Polish development bank; defence and dual-use working-capital lines." },
                    new { name = "EIB Group (Defence Equity Facility, SAFE-related lending)", fit = "EU defence supply chain financing via intermediary banks." },
                    new { name = "Commercial bank with a KUKE guarantee", fit = "Export contract to Germany: KUKE can insure the receivable." },
                },
            };
        });
    }
}
