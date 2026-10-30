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
            var company = await db.Organizations.FirstAsync(o => o.Id == org.OrgId);
            // How to get each document again: only where we know the national source (Polish companies for now).
            List<ObtainableDocument>? Obtain(string kind) => company.Country == "PL" ? PolishSources.ByKind.GetValueOrDefault(kind) : null;
            return new
            {
                translationNote = company.Country == "PL" && country == "DE" ? PolishSources.TranslationNote : null,
                // The German competition register has no foreign equivalent; these are what a Polish bidder submits instead.
                instead = country == "DE" ? new[] { "criminal-record", "trade-register" }.Select(k => new { kind = k, obtain = Obtain(k)?.FirstOrDefault() }) : null,
                rows = (profile?.Documents ?? []).Select(d => new { document = d, ecertis = EcertisCatalog.Match(d, country), obtain = Obtain(d.Kind) }),
            };
        });
    }
}
