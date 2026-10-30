using Marketplace.Tenders;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Data;

public static class DemoSeeder
{
    /// <summary>
    /// Recreates the schema when <c>Db:ResetOnStart</c> is on (the default for the demo: no migrations to run,
    /// every start is a clean demo), then seeds the demo organisations.
    /// </summary>
    public static async Task PrepareAsync(IServiceProvider services, IConfiguration config)
    {
        using var scope = services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDb>();
        if (config.GetValue("Db:ResetOnStart", true))
        {
            await db.Database.EnsureDeletedAsync();
        }

        await db.Database.EnsureCreatedAsync();
        if (!await db.Organizations.AnyAsync())
        {
            db.Organizations.AddRange(DemoOrgs.All());
            db.Profiles.AddRange(DemoProfiles.All());
            db.Mandates.AddRange(DemoProfiles.Mandates());
            db.Boms.Add(DemoProfiles.Bom());
            await db.SaveChangesAsync();
        }

        var dataPath = Path.GetFullPath(
            config["DemoData:Path"] ?? "../../demo-data", services.GetRequiredService<IWebHostEnvironment>().ContentRootPath);
        await TenderStore.SeedAsync(db, dataPath);
        Ecertis.EcertisCatalog.Load(Path.Combine(dataPath, "ecertis"));
    }
}
