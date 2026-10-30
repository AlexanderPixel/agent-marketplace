using Marketplace.Data;
using Marketplace.Ecertis;
using Marketplace.Tenancy;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Endpoints;

public static class EcertisEndpoints
{
    public static void MapEcertisEndpoints(this WebApplication app)
    {
        var api = app.MapGroup("/api/ecertis");

        api.MapGet("/criteria", () => EcertisCatalog.Criteria.Values
            .Select(c => new { c.Id, c.Name, c.Type, countries = c.EvidenceByCountry.Keys.Order(), url = EcertisCatalog.Link(c.Id) }));

        api.MapGet("/criteria/{id}", (string id) =>
            EcertisCatalog.Criteria.TryGetValue(id, out var c)
                ? Results.Ok(new { c.Id, c.Name, c.Type, c.EvidenceByCountry, url = EcertisCatalog.Link(c.Id) })
                : Results.NotFound());

        // Your documents, each with what a buyer in another country would accept for the same eCertis criterion.
        app.MapGet("/api/evidence", async (AppDb db, OrgContext org, string? buyerCountry) =>
        {
            org.Require();
            var profile = await db.Profiles.FirstOrDefaultAsync();
            var country = buyerCountry ?? "DE";
            return (profile?.Documents ?? []).Select(d => new { document = d, ecertis = EcertisCatalog.Match(d, country) });
        });
    }
}
