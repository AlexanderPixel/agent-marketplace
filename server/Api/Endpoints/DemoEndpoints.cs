using Marketplace.Data;

namespace Marketplace.Endpoints;

public static class DemoEndpoints
{
    public static void MapDemoEndpoints(this WebApplication app)
    {
        // Demo only: back to the starting point, so the walkthrough can be run again from the top.
        app.MapPost("/api/demo/reset", async (AppDb db, IConfiguration config, ILoggerFactory logs) =>
        {
            await DemoSeeder.ResetAsync(db, config);
            logs.CreateLogger("Demo").LogInformation("Demo data reset");
            return TypedResults.NoContent();
        });
    }
}
