using Marketplace.Data;

namespace Marketplace.Tenders;

/// <summary>
/// Builds an OCDS 1.1 release for a tender, so partners (e.g. SalesPatriot) get one standard shape whatever the source:
/// TED eForms or e-Zamówienia. Only the fields we hold are mapped; the raw notice stays available next to it.
/// </summary>
public static class Ocds
{
    public static object Release(Tender t)
    {
        var buyerId = $"{t.BuyerCountry}-{Slug(t.BuyerName)}";
        return new
        {
            ocid = t.Id,
            id = $"{t.Source}-{t.SourceId}",
            date = t.PublishedAt,
            tag = new[] { "tender" },
            initiationType = "tender",
            language = t.Language,
            parties = new[]
            {
                new
                {
                    id = buyerId,
                    name = t.BuyerName,
                    address = new { locality = t.BuyerCity, countryName = t.BuyerCountry },
                    roles = new[] { "buyer", "procuringEntity" },
                },
            },
            buyer = new { id = buyerId, name = t.BuyerName },
            tender = new
            {
                id = t.Reference ?? t.SourceId,
                title = t.Title,
                status = t.Deadline is { } d && d < DateTimeOffset.UtcNow ? "complete" : "active",
                mainProcurementCategory = t.ContractNature switch
                {
                    "supplies" => "goods", "services" => "services", "works" => "works", _ => null,
                },
                procurementMethod = t.ProcedureType switch
                {
                    "open" or "basic" => "open", "restricted" => "selective", _ => t.ProcedureType,
                },
                procurementMethodDetails = t.ProcedureType,
                classification = new { scheme = "CPV", id = t.Cpv, description = t.CpvLabels.GetValueOrDefault(t.Language) },
                tenderPeriod = new { endDate = t.Deadline },
                lots = t.Lots.Select(l => new { id = l.Id, title = l.Title }),
                items = t.Lots.Select(l => new
                {
                    id = l.Id,
                    description = l.Title,
                    classification = new { scheme = "CPV", id = l.Cpv ?? t.Cpv },
                    quantity = l.Quantity,
                    unit = l.Unit is null ? null : new { scheme = "UNCEFACT", id = l.Unit, name = Codes.Units.GetValueOrDefault(l.Unit) },
                    relatedLot = l.Id,
                    deliveryAddress = new { locality = l.Place },
                }),
                selectionCriteria = new
                {
                    criteria = t.Requirements.Select(r => new
                    {
                        type = r.Code is null ? "technical" : r.Code.StartsWith("slc-stand") ? "economic" : r.Code.StartsWith("slc-abil") ? "technical" : "suitability",
                        description = r.Text,
                        relatedLots = r.Lots,
                    }),
                },
                exclusionGrounds = t.ExclusionGrounds.Count == 0 ? null : new { description = string.Join(", ", t.ExclusionGrounds) },
                documents = new[]
                {
                    new { id = "notice", documentType = "tenderNotice", url = t.NoticeUrl, language = t.Language },
                    new { id = "documents", documentType = "biddingDocuments", url = t.DocumentsUrl, language = t.Language },
                }.Where(d => d.url is not null),
            },
        };
    }

    private static string Slug(string s) =>
        new string(s.ToLowerInvariant().Select(c => char.IsLetterOrDigit(c) ? c : '-').ToArray()).Trim('-');
}
