using Marketplace.Tenders;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Data;

public static class DemoSeeder
{
    /// <summary>
    /// Recreates the schema when <c>Db:ResetOnStart</c> is on (off by default, so bid history and award results survive
    /// restarts; turn it on for a clean demo or after a schema change, since there are no migrations), then seeds the demo
    /// organisations into an empty database.
    /// </summary>
    public static async Task PrepareAsync(IServiceProvider services, IConfiguration config)
    {
        using var scope = services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDb>();
        if (config.GetValue("Db:ResetOnStart", false))
        {
            await db.Database.EnsureDeletedAsync();
        }

        await db.Database.EnsureCreatedAsync();
        if (!await db.Organizations.AnyAsync())
        {
            await SeedCompaniesAsync(db, config);
        }

        var dataPath = Path.GetFullPath(
            config["DemoData:Path"] ?? "../../demo-data", services.GetRequiredService<IWebHostEnvironment>().ContentRootPath);
        await TenderStore.SeedAsync(db, dataPath);
        Ecertis.EcertisCatalog.Load(Path.Combine(dataPath, "ecertis"));
        Intelligence.PrimeAwards.Load(Path.Combine(dataPath, "ted/prime-awards.json"));
        Products.MarketNeeds.Load(Path.Combine(dataPath, "needs"));
    }

    /// <summary>
    /// Puts the demo back to its starting point: deals, auto-bid runs and settings, the activity log, and the companies'
    /// profiles, mandates and BOMs go back to the seed. Tenders stay (they are public data and slow to reload).
    /// </summary>
    public static async Task ResetAsync(AppDb db, IConfiguration config, CancellationToken ct = default)
    {
        await db.Deals.IgnoreQueryFilters().ExecuteDeleteAsync(ct);
        await db.AutoBidRuns.IgnoreQueryFilters().ExecuteDeleteAsync(ct);
        await db.AutoBidSettings.IgnoreQueryFilters().ExecuteDeleteAsync(ct);
        await db.ActivityEvents.IgnoreQueryFilters().ExecuteDeleteAsync(ct);
        await db.Boms.IgnoreQueryFilters().ExecuteDeleteAsync(ct);
        await db.Mandates.IgnoreQueryFilters().ExecuteDeleteAsync(ct);
        await db.Profiles.IgnoreQueryFilters().ExecuteDeleteAsync(ct);
        await db.Organizations.ExecuteDeleteAsync(ct);
        db.ChangeTracker.Clear();
        await SeedCompaniesAsync(db, config);
    }

    private static async Task SeedCompaniesAsync(AppDb db, IConfiguration config)
    {
        db.Organizations.AddRange(DemoOrgs.All());
        var profiles = DemoProfiles.All().ToList();
        if (config.GetValue("DemoData:ExtendedProfile", true))
        {
            DemoProfileExtras.Apply(profiles.First(p => p.OrgId == DemoProfileExtras.OrgId));
        }

        db.Profiles.AddRange(profiles);
        db.Mandates.AddRange(DemoProfiles.Mandates());
        db.Boms.Add(DemoProfiles.Bom());
        await db.SaveChangesAsync();
    }
}
