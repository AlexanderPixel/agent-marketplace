using System.Net.Http.Json;
using System.Text.Json;
using System.Text.RegularExpressions;
using Marketplace.Activity;
using Marketplace.Data;
using Marketplace.Qualification;
using Microsoft.EntityFrameworkCore;

namespace Marketplace.AutoBid;

/// <summary>A contract award notice for a procedure: who the buyer awarded which lots to.</summary>
public record AwardNotice(string PublicationNumber, string NoticeType, List<string> Winners, List<string> Lots, DateTimeOffset? DecidedAt);

/// <summary>
/// Works out from the buyer's award notice whether a signed bid was won or lost, so nobody has to record it by hand.
/// A win becomes a structured reference, so later tenders that ask for experience can use it.
/// </summary>
public static partial class AwardResults
{
    private const string Agent = "Auto-bid agent";
    private const string TedPrefix = "ocds-ted-";

    /// <summary>
    /// The award notices TED has published for this tender's procedure, newest first. Empty when there are none yet or the
    /// tender is not a TED procedure (e-Zamówienia result notices are not checked yet).
    /// </summary>
    public static async Task<List<AwardNotice>> FindAsync(HttpClient client, Tender tender, CancellationToken ct)
    {
        if (tender.Source != TenderSource.TED || !tender.Id.StartsWith(TedPrefix)) return [];
        var procedure = tender.Id[TedPrefix.Length..];
        var body = new
        {
            query = $"procedure-identifier={procedure} AND notice-type IN (can-standard can-social can-desg can-tran) SORT BY publication-date DESC",
            fields = new[] { "publication-number", "notice-type", "winner-name", "result-lot-identifier", "winner-decision-date" },
            limit = 20,
        };
        using var response = await client.PostAsJsonAsync("https://api.ted.europa.eu/v3/notices/search", body, ct);
        response.EnsureSuccessStatusCode();
        using var doc = JsonDocument.Parse(await response.Content.ReadAsStringAsync(ct));
        return doc.RootElement.GetProperty("notices").EnumerateArray().Select(n => new AwardNotice(
            n.GetProperty("publication-number").GetString()!,
            n.TryGetProperty("notice-type", out var type) ? type.ToString() : "",
            AllValues(n, "winner-name"),
            AllValues(n, "result-lot-identifier"),
            AllValues(n, "winner-decision-date").Select(d => Tenders.TedNotice.Date(d)).FirstOrDefault(d => d is not null))).ToList();
    }

    /// <summary>
    /// The notice that settles this bid, if any: one that covers a lot the company offered on (or the whole procedure).
    /// A notice for other lots only leaves the bid waiting.
    /// </summary>
    public static AwardNotice? Deciding(List<AwardNotice> notices, IReadOnlyCollection<string> ourLots) =>
        notices.FirstOrDefault(n => n.Lots.Count == 0 || ourLots.Count == 0 || n.Lots.Any(ourLots.Contains));

    /// <summary>
    /// Whether the company is among the winners. Legal forms and punctuation are ignored. Otherwise conservative: a short name
    /// inside a longer one ("GGU" in "GGU Gesellschaft …") is not enough, since a wrong win would add a false reference.
    /// </summary>
    public static bool Names(AwardNotice notice, string company)
    {
        var us = Normalize(company);
        return us.Length > 0 && notice.Winners.Select(Normalize).Any(w => w == us || Within(us, w) || Within(w, us));

        static bool Within(string shorter, string longer) =>
            shorter.Length >= 8 && $" {longer} ".Contains($" {shorter} ");
    }

    /// <summary>Records the result on the run, logs it, and turns a win into a reference.</summary>
    public static async Task RecordAsync(AppDb db, ActivityLog log, TimeProvider clock, AutoBidRun run, bool won, AwardNotice notice, CancellationToken ct)
    {
        var now = clock.GetUtcNow();
        var winners = notice.Winners.Distinct().ToList();
        (run.Result, run.ResultNotice, run.Winner, run.ResultAt, run.UpdatedAt) =
            (won ? "won" : "lost", notice.PublicationNumber, winners.Count == 0 ? null : string.Join(", ", winners), now, now);
        var why = $"The award notice TED {notice.PublicationNumber} " +
                  (winners.Count == 0 ? "names no winner for the lots the company bid on." : $"names {string.Join(", ", winners)} as the winner.");
        if (!won)
        {
            await db.SaveChangesAsync(ct);
            await log.WriteAsync(run.OrgId, Agent, $"Recorded {run.TenderRef} as lost", why + " Nothing is added to the references.", run.TenderId, ct: ct);
            return;
        }

        var tender = await db.Tenders.FirstAsync(t => t.Id == run.TenderId, ct);
        var profile = await db.Profiles.FirstAsync(ct);
        var deal = run.DealId is { } dealId ? await db.Deals.FirstOrDefaultAsync(d => d.Id == dealId, ct) : null;
        var lot = OfferedLots(tender, profile).FirstOrDefault();
        // Only the company's own share counts as its experience; the partner's quantity is the partner's reference.
        var own = lot is null ? (int?)null : (int)(lot.Quantity ?? 0) - (deal?.Quantity ?? 0);
        var completed = deal?.Delivery ?? DateOnly.FromDateTime((tender.Deadline ?? now).UtcDateTime).AddMonths(6);
        var reference = new CompanyDocument(
            $"won-{run.TenderRef}", "reference", $"Won: {run.Title}", tender.BuyerName, tender.BuyerCountry,
            DateOnly.FromDateTime((notice.DecidedAt ?? now).UtcDateTime), null,
            $"Created from the won tender {tender.Source} {tender.SourceId} (award notice {notice.PublicationNumber}). Ask the buyer for a reference letter after delivery.",
            new DeliveryRecord(tender.BuyerName, lot?.Title ?? run.Title, own, lot?.Unit, null, completed, tender.SourceId,
                $"Award notice TED {notice.PublicationNumber}"));
        profile.Documents = [.. profile.Documents.Where(d => d.Id != reference.Id), reference];
        await db.SaveChangesAsync(ct);
        await log.WriteAsync(run.OrgId, Agent, $"Recorded {run.TenderRef} as won and added it to the references",
            $"{why} {own} {lot?.Unit} of {lot?.Title} for {tender.BuyerName}, delivery due {completed:yyyy-MM-dd}. It counts as experience once delivered.",
            run.TenderId, ct: ct);
    }

    public static IEnumerable<TenderLot> OfferedLots(Tender tender, CompanyProfile profile) =>
        tender.Lots.Where(l => QualificationCheck.CheckLot(l, profile).Fit != LotFit.NotOffered);

    private static List<string> AllValues(JsonElement n, string key) =>
        !n.TryGetProperty(key, out var v) ? []
        : v.ValueKind == JsonValueKind.Array ? v.EnumerateArray().Select(x => x.ToString()).ToList()
        // Multilingual fields look like {"deu": ["..."], "eng": ["..."]}; a name may be spelled differently per language.
        : v.ValueKind == JsonValueKind.Object ? v.EnumerateObject().SelectMany(p => p.Value.ValueKind == JsonValueKind.Array
            ? p.Value.EnumerateArray().Select(x => x.ToString()) : [p.Value.ToString()]).Distinct().ToList()
        : [v.ToString()];

    [GeneratedRegex(@"\b(sp\.?\s*z\s*o\.?\s*o|s\.?\s*a|sp\.?\s*k|sp\.?\s*j|gmbh(\s*&\s*co\.?\s*kg)?|ag|kg|se|a\.?\s*s|s\.?\s*r\.?\s*o|spol|ltd|limited|plc|inc|llc|bv|nv|sas|sarl|srl|spa|oy|ab)\b\.?", RegexOptions.IgnoreCase)]
    private static partial Regex LegalForm();

    [GeneratedRegex(@"[^\p{L}\p{N}]+")]
    private static partial Regex NonWord();

    private static string Normalize(string name) =>
        NonWord().Replace(LegalForm().Replace(name.ToLowerInvariant(), " "), " ").Trim();
}
