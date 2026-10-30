namespace Marketplace.Tenancy;

/// <summary>The organisation the current request acts for. Null means no organisation: private data is invisible.</summary>
public class OrgContext
{
    public Guid? OrgId { get; set; }

    public Guid Require() => OrgId ?? throw new BadHttpRequestException("Choose an organisation first.", 401);
}
