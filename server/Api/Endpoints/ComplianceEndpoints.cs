using Marketplace.Activity;
using Marketplace.Compliance;
using Marketplace.Data;
using Marketplace.Tenancy;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Endpoints;

public static class ComplianceEndpoints
{
    public record SwapRequest(string ComponentId, string Supplier);

    public record ProductRequest(string Product);

    /// <param name="Country">ISO 3166 alpha-2 code of where the part comes from; this decides whether it counts as EU content.</param>
    public record ComponentRequest(string Name, string Supplier, string Country, decimal CostEur);

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
                eligibleCountries = BuyEuropean.Eligible.Order(),
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

        // The company maintains its own BOM: product name, parts, suppliers and where each part comes from.
        api.MapPut("/product", async Task<IResult> (ProductRequest body, AppDb db, OrgContext org) =>
        {
            var orgId = org.Require();
            if (string.IsNullOrWhiteSpace(body.Product)) return TypedResults.BadRequest("Product name is required.");
            var bom = await db.Boms.FirstOrDefaultAsync();
            if (bom is null) db.Boms.Add(new BillOfMaterials { OrgId = orgId, Product = body.Product.Trim() });
            else bom.Product = body.Product.Trim();
            await db.SaveChangesAsync();
            return TypedResults.NoContent();
        });

        api.MapPost("/components", async Task<IResult> (ComponentRequest body, AppDb db, OrgContext org) =>
        {
            org.Require();
            if (Invalid(body) is { } error) return TypedResults.BadRequest(error);
            var bom = await db.Boms.FirstOrDefaultAsync();
            if (bom is null) return TypedResults.NotFound();
            var component = new BomComponent($"c-{Guid.NewGuid():N}"[..10], body.Name.Trim(), body.Supplier.Trim(), body.Country.ToUpperInvariant(), body.CostEur, []);
            bom.Components = [.. bom.Components, component];
            await db.SaveChangesAsync();
            return TypedResults.Ok(component);
        });

        api.MapPut("/components/{id}", async Task<IResult> (string id, ComponentRequest body, AppDb db, OrgContext org) =>
        {
            org.Require();
            if (Invalid(body) is { } error) return TypedResults.BadRequest(error);
            var bom = await db.Boms.FirstOrDefaultAsync();
            if (bom is null || bom.Components.All(c => c.Id != id)) return TypedResults.NotFound();
            bom.Components = bom.Components
                .Select(c => c.Id == id
                    ? c with { Name = body.Name.Trim(), Supplier = body.Supplier.Trim(), Country = body.Country.ToUpperInvariant(), CostEur = body.CostEur }
                    : c)
                .ToList();
            await db.SaveChangesAsync();
            return TypedResults.NoContent();
        });

        api.MapDelete("/components/{id}", async Task<IResult> (string id, AppDb db, OrgContext org) =>
        {
            org.Require();
            var bom = await db.Boms.FirstOrDefaultAsync();
            if (bom is null || bom.Components.All(c => c.Id != id)) return TypedResults.NotFound();
            bom.Components = bom.Components.Where(c => c.Id != id).ToList();
            await db.SaveChangesAsync();
            return TypedResults.NoContent();
        });
    }

    private static string? Invalid(ComponentRequest c) =>
        string.IsNullOrWhiteSpace(c.Name) ? "Component name is required."
        : string.IsNullOrWhiteSpace(c.Supplier) ? "Supplier is required."
        : c.Country is null || c.Country.Length != 2 || !c.Country.All(char.IsAsciiLetter) ? "Country must be a two-letter ISO code."
        : c.CostEur <= 0 ? "Cost must be above zero."
        : null;
}
