using Marketplace.Activity;
using Marketplace.Compliance;
using Marketplace.Data;
using Marketplace.Tenancy;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Endpoints;

public static class ComplianceEndpoints
{
    public record SwapRequest(string ComponentId, string Supplier);

    public static void MapComplianceEndpoints(this WebApplication app)
    {
        var api = app.MapGroup("/api/compliance");

        api.MapGet("/", async (AppDb db, OrgContext org) =>
        {
            var orgId = org.Require();
            var me = await db.Organizations.FirstAsync(o => o.Id == orgId);
            var bom = await db.Boms.FirstOrDefaultAsync();
            var profile = await db.Profiles.FirstOrDefaultAsync();

            // Partners in my closed deals: their ownership chain is part of SAFE eligibility, so it is shared for deals only.
            var deals = await db.Deals.Where(d => d.Status == DealStatus.Closed).ToListAsync();
            var partnerIds = deals.Select(d => d.BidderOrgId == orgId ? d.PartnerOrgId : d.BidderOrgId).Distinct().ToList();
            var partners = await db.Organizations.Where(o => partnerIds.Contains(o.Id)).ToListAsync();
            var partnerProfiles = await db.Profiles.IgnoreQueryFilters().Where(p => partnerIds.Contains(p.OrgId))
                .Select(p => new { p.OrgId, p.Ownership }).ToListAsync();

            var control = new List<ControlCheck> { BuyEuropean.Control(me, profile, "you") };
            control.AddRange(partners.Select(p => BuyEuropean.Control(p,
                new CompanyProfile { OrgId = p.Id, Ownership = partnerProfiles.FirstOrDefault(x => x.OrgId == p.Id)?.Ownership ?? [] }, "partner")));

            var screened = (bom?.Components.Select(c => (c.Supplier, c.Country)) ?? [])
                .Concat(partners.Select(p => (p.Name, p.Country)));
            var euContent = bom is null ? 0 : BuyEuropean.EuContent(bom.Components);
            return new
            {
                threshold = BuyEuropean.Threshold,
                product = bom?.Product,
                euContent,
                components = bom is null ? [] : BuyEuropean.Components(bom),
                swaps = bom is null ? [] : BuyEuropean.Swaps(bom),
                control,
                sanctions = BuyEuropean.Screen(screened),
                safeEligible = euContent >= BuyEuropean.Threshold && control.All(c => c.EuControlled),
                contentOk = euContent >= BuyEuropean.Threshold,
                controlOk = control.All(c => c.EuControlled),
            };
        });

        api.MapPost("/swap", async Task<IResult> (SwapRequest body, AppDb db, OrgContext org, ActivityLog log) =>
        {
            var orgId = org.Require();
            var bom = await db.Boms.FirstOrDefaultAsync();
            var component = bom?.Components.FirstOrDefault(c => c.Id == body.ComponentId);
            var alt = component?.Alternatives.FirstOrDefault(a => a.Supplier == body.Supplier);
            if (bom is null || component is null || alt is null) return TypedResults.NotFound();

            var before = BuyEuropean.EuContent(bom.Components);
            var old = new BomAlternative(component.Supplier, component.Country, component.CostEur, "Previous supplier");
            bom.Components = bom.Components
                .Select(c => c.Id == component.Id
                    ? c with { Supplier = alt.Supplier, Country = alt.Country, CostEur = alt.CostEur, Alternatives = [.. c.Alternatives.Where(a => a != alt), old] }
                    : c)
                .ToList();
            await db.SaveChangesAsync();
            var after = BuyEuropean.EuContent(bom.Components);
            await log.WriteAsync(orgId, "Compliance agent",
                $"Switched {component.Name} from {old.Supplier} ({old.Country}) to {alt.Supplier} ({alt.Country}): EU content {before}% → {after}%",
                $"A person accepted the suggestion. Cost change {alt.CostEur - old.CostEur:+0.00;-0.00} € per set; SAFE/EDIP need at least {BuyEuropean.Threshold}% of component cost from the EU, EEA or Ukraine.");
            return TypedResults.Ok(new { before, after });
        });
    }
}
