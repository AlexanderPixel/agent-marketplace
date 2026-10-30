namespace Marketplace.Tenancy;

/// <summary>
/// Demo sign-in: the UI's company switcher sends the organisation id in the <c>X-Org-Id</c> header (or <c>?org=</c>
/// for server-sent events, which cannot set headers). A real deployment would take it from the session instead.
/// </summary>
public class OrgMiddleware(RequestDelegate next)
{
    public async Task InvokeAsync(HttpContext http, OrgContext org)
    {
        var raw = http.Request.Headers["X-Org-Id"].FirstOrDefault() ?? http.Request.Query["org"].FirstOrDefault();
        if (Guid.TryParse(raw, out var id))
        {
            org.OrgId = id;
        }

        await next(http);
    }
}
