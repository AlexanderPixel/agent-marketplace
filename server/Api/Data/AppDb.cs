using System.Text.Json;
using Marketplace.Tenancy;
using Microsoft.EntityFrameworkCore.ChangeTracking;
using Microsoft.EntityFrameworkCore.Metadata.Builders;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.Data;

/// <summary>
/// Tenant isolation lives here, in the data layer (non-negotiable rule 3): every organisation-owned set has a
/// query filter on the current organisation, so a query cannot return another organisation's rows even if an
/// endpoint forgets to filter. Public data (tenders) has no filter.
/// </summary>
public class AppDb(DbContextOptions<AppDb> options, OrgContext org) : DbContext(options)
{
    public Guid? CurrentOrgId => org.OrgId;

    public DbSet<Organization> Organizations => Set<Organization>();
    public DbSet<ActivityEvent> ActivityEvents => Set<ActivityEvent>();
    public DbSet<Tender> Tenders => Set<Tender>();

    protected override void OnModelCreating(ModelBuilder b)
    {
        b.Entity<Organization>(e =>
        {
            e.Property(o => o.Kind).HasConversion<string>();
        });

        b.Entity<ActivityEvent>(e =>
        {
            e.HasIndex(a => new { a.OrgId, a.CreatedAt });
            e.Property(a => a.Data).HasColumnType("jsonb");
            e.HasQueryFilter(a => a.OrgId == CurrentOrgId || a.SharedWithOrgId == CurrentOrgId);
        });

        b.Entity<Tender>(e =>
        {
            e.Property(t => t.Source).HasConversion<string>();
            e.Property(t => t.RawJson).HasColumnType("jsonb");
            Json(e.Property(t => t.Titles));
            Json(e.Property(t => t.CpvLabels));
            Json(e.Property(t => t.Lots));
            Json(e.Property(t => t.Requirements));
            Json(e.Property(t => t.ExclusionGrounds));
        });
    }

    /// <summary>Stores a small object graph as a jsonb column (demo shortcut: no owned types or extra tables).</summary>
    private static void Json<T>(PropertyBuilder<T> property) where T : class, new()
    {
        property.HasColumnType("jsonb").HasConversion(
            v => JsonSerializer.Serialize(v, JsonSerializerOptions.Web),
            v => JsonSerializer.Deserialize<T>(v, JsonSerializerOptions.Web) ?? new T(),
            new ValueComparer<T>(
                (a, b) => JsonSerializer.Serialize(a, JsonSerializerOptions.Web) == JsonSerializer.Serialize(b, JsonSerializerOptions.Web),
                v => JsonSerializer.Serialize(v, JsonSerializerOptions.Web).GetHashCode(),
                v => JsonSerializer.Deserialize<T>(JsonSerializer.Serialize(v, JsonSerializerOptions.Web), JsonSerializerOptions.Web)!));
    }
}
