using Marketplace.Tenancy;
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
    }
}
