using Marketplace.Telegram;
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

    public record DecisionRequest(bool Sign);

    public record SpecRequest(string Document, string Text);

    public record SpecApplyRequest(string Document, string? Product, List<ImportedComponent> Components);

    /// <param name="Country">ISO 3166 alpha-2 code of where the part comes from; this decides whether it counts as EU content.</param>
    public record ComponentRequest(string Name, string Supplier, string Country, decimal CostEur);

    public static void MapComplianceEndpoints(this WebApplication app)
    {
        var api = app.MapGroup("/api/compliance");

        api.MapGet("/", async (AppDb db, OrgContext org, TelegramBot bot, SupplierChanges changes) =>
        {
            var orgId = org.Require();
            // A failing check sends its sign requests to Telegram by itself; nothing in the BOM changes until a person signs.
            await changes.AutoPrepareAsync();
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
            var sanctions = BuyEuropean.Screen(screened);
            return new
            {
                threshold = BuyEuropean.Threshold,
                eligibleCountries = BuyEuropean.Eligible.Order(),
                product = bom?.Product,
                euContent,
                components = bom is null ? [] : BuyEuropean.Components(bom),
                swaps = bom is null ? [] : BuyEuropean.Swaps(bom),
                control,
                sanctions,
                safeEligible = euContent >= BuyEuropean.Threshold && control.All(c => c.EuControlled) && !sanctions.Any(s => s.Hit),
                sanctionsOk = !sanctions.Any(s => s.Hit),
                contentOk = euContent >= BuyEuropean.Threshold,
                controlOk = control.All(c => c.EuControlled),
                telegram = bot.Connected,
            };
        });

        api.MapPost("/swap", async Task<IResult> (SwapRequest body, AppDb db, OrgContext org, ActivityLog log) =>
        {
            var orgId = org.Require();
            var bom = await db.Boms.FirstOrDefaultAsync();
            var component = bom?.Components.FirstOrDefault(c => c.Id == body.ComponentId);
            var alt = component?.Alternatives.FirstOrDefault(a => a.Supplier == body.Supplier);
            if (bom is null || component is null || alt is null) return TypedResults.NotFound();
            if (BuyEuropean.Sanctioned(alt.Supplier)) return TypedResults.BadRequest($"{alt.Supplier} is on the EU sanctions list: you cannot buy from them.");

            var before = BuyEuropean.EuContent(bom.Components);
            var old = new BomAlternative(component.Supplier, component.Country, component.CostEur, BuyEuropean.PreviousSupplier);
            bom.Components = bom.Components
                .Select(c => c.Id == component.Id
                    // Alternatives keep their original notes, so swapping back (undo) loses nothing.
                    // An undo keeps the change on record as "undone", so the agent does not ask to switch again on its own.
                    ? c with { Supplier = alt.Supplier, Country = alt.Country, CostEur = alt.CostEur,
                        Change = alt.Note == BuyEuropean.PreviousSupplier && c.Change is { } done ? done with { Stage = "undone", DecidedAt = DateTimeOffset.UtcNow } : null,
                        Alternatives = c.Alternatives.Any(a => a.Supplier == old.Supplier) ? c.Alternatives : [.. c.Alternatives, old] }
                    : c)
                .ToList();
            await db.SaveChangesAsync();
            var after = BuyEuropean.EuContent(bom.Components);
            await log.WriteAsync(orgId, "Compliance agent",
                $"Switched {component.Name} from {old.Supplier} ({old.Country}) to {alt.Supplier} ({alt.Country}): EU content {before}% → {after}%",
                $"{(alt.Note == BuyEuropean.PreviousSupplier ? "A person undid an earlier swap." : "A person accepted the suggestion.")} Cost change {alt.CostEur - old.CostEur:+0.00;-0.00} € per set; SAFE/EDIP need at least {BuyEuropean.Threshold}% of component cost from the EU, EEA or Ukraine.");
            return TypedResults.Ok(new { before, after });
        });

        // A supplier change goes through a person's signature: drafts first, the BOM changes only on "Sign".
        api.MapPost("/changes", async Task<IResult> (SwapRequest body, SupplierChanges changes) =>
            await changes.PrepareAsync(body.ComponentId, body.Supplier) is { } change ? TypedResults.Ok(change) : TypedResults.NotFound());

        api.MapPost("/changes/{id:guid}/decide", async (Guid id, DecisionRequest body, SupplierChanges changes, OrgContext org) =>
        {
            org.Require();
            return TypedResults.Ok(new { message = await changes.DecideAsync(id, body.Sign, "You in the app") });
        });

        // The spec agent reads an uploaded specification; a person reviews the result before it replaces the BOM.
        api.MapPost("/import/preview", async Task<IResult> (SpecRequest body, SpecImporter importer, AppDb db, OrgContext org, ActivityLog log) =>
        {
            var orgId = org.Require();
            if (string.IsNullOrWhiteSpace(body.Text)) return TypedResults.BadRequest("The document is empty.");
            var language = (await db.Organizations.FirstAsync(o => o.Id == orgId)).Language;
            var result = await importer.ReadAsync(body.Document, body.Text, language);
            await log.WriteAsync(orgId, "Spec agent",
                $"Read {body.Document}: found {result.Components.Count} components, {result.Components.Count(c => c.Complete)} complete",
                $"Read only the uploaded specification ({(result.Method == "ai" ? "AI model" : "table reader")}); every value cites its row in the document. " +
                "Nothing is changed until a person confirms the import.");
            return TypedResults.Ok(result);
        });

        api.MapPost("/import/apply", async Task<IResult> (SpecApplyRequest body, AppDb db, OrgContext org, ActivityLog log) =>
        {
            var orgId = org.Require();
            var rows = body.Components.Where(c => !string.IsNullOrWhiteSpace(c.Supplier) && c.Country is { Length: 2 } && c.CostEur > 0).ToList();
            if (rows.Count == 0) return TypedResults.BadRequest("No complete components to import.");
            var components = rows.Select(c => new BomComponent($"c-{Guid.NewGuid():N}"[..10], c.Name.Trim(), c.Supplier!.Trim(), c.Country!.ToUpperInvariant(), c.CostEur!.Value,
                c.Alternatives.Where(a => a.Country is { Length: 2 } && a.CostEur > 0 && !BuyEuropean.Sanctioned(a.Supplier))
                    .Select(a => new BomAlternative(a.Supplier.Trim(), a.Country!.ToUpperInvariant(), a.CostEur!.Value, a.Note)).ToList(),
                Source: new ComponentSource(body.Document, c.Location, c.Quote))).ToList();

            var bom = await db.Boms.FirstOrDefaultAsync();
            if (bom is null) db.Boms.Add(bom = new BillOfMaterials { OrgId = orgId });
            if (!string.IsNullOrWhiteSpace(body.Product)) bom.Product = body.Product.Trim();
            bom.Components = components;
            await db.SaveChangesAsync();
            await log.WriteAsync(orgId, "Spec agent",
                $"Replaced the bill of materials of {bom.Product} with {components.Count} components from {body.Document}: EU content {BuyEuropean.EuContent(components)}%",
                "A person reviewed the extracted components and confirmed the import. Each component keeps its source row in the specification.");
            return TypedResults.Ok(new { imported = components.Count });
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
        : BuyEuropean.Sanctioned(c.Supplier) ? $"{c.Supplier.Trim()} is on the EU sanctions list: you cannot buy from them."
        : null;
}
