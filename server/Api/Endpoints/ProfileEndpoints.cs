using Marketplace.Data;
using Marketplace.Tenancy;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Endpoints;

public static class ProfileEndpoints
{
    public record NewDocument(string Kind, string Title, string Issuer, string Country, DateOnly? IssuedOn, DateOnly? ValidUntil);

    public static void MapProfileEndpoints(this WebApplication app)
    {
        app.MapGet("/api/profile/full", async (AppDb db, OrgContext org) =>
        {
            var orgId = org.Require();
            var me = await db.Organizations.FirstAsync(o => o.Id == orgId);
            var profile = await db.Profiles.FirstOrDefaultAsync() ?? new CompanyProfile { OrgId = orgId };
            return new { organization = me, profile };
        });

        app.MapPost("/api/profile/documents", async Task<IResult> (NewDocument d, AppDb db, OrgContext org) =>
        {
            var orgId = org.Require();
            var profile = await db.Profiles.FirstOrDefaultAsync();
            if (profile is null) return TypedResults.NotFound();
            var doc = new CompanyDocument($"doc-{Guid.NewGuid():N}"[..12], d.Kind, d.Title, d.Issuer, d.Country, d.IssuedOn, d.ValidUntil);
            profile.Documents = [.. profile.Documents, doc];
            await db.SaveChangesAsync();
            return TypedResults.Ok(doc);
        });

        app.MapDelete("/api/profile/documents/{id}", async Task<IResult> (string id, AppDb db, OrgContext org) =>
        {
            org.Require();
            var profile = await db.Profiles.FirstOrDefaultAsync();
            if (profile is null) return TypedResults.NotFound();
            profile.Documents = profile.Documents.Where(d => d.Id != id).ToList();
            await db.SaveChangesAsync();
            return TypedResults.NoContent();
        });
    }
}
