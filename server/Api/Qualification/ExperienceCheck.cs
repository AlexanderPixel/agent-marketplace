using System.Globalization;
using System.Text.RegularExpressions;
using Marketplace.Data;

namespace Marketplace.Qualification;

/// <summary>What an experience requirement asks for, read from its text. Null means the text does not say.</summary>
public record ExperienceAsk(int Count, decimal? MinValueEur, int? MinQuantity, int Years, bool YearsStated);

/// <summary>
/// Checks "at least N deliveries worth X in the last Y years" against the company's references. Conservative (rule 7):
/// a reference only counts once its delivery is complete, and a threshold that needs a value we do not know stays unclear.
/// </summary>
public static partial class ExperienceCheck
{
    /// <summary>Fixed demo rate for PLN thresholds; shown in the reason so a person can check it.</summary>
    public const decimal PlnPerEur = 4.25m;

    /// <summary>Default look-back for supplies when the text gives none (Polish PZP art. 116: three years).</summary>
    private const int DefaultYears = 3;

    private static readonly Dictionary<string, int> Words = new(StringComparer.OrdinalIgnoreCase)
    {
        ["jedn"] = 1, ["one"] = 1, ["ein"] = 1,
        ["dw"] = 2, ["two"] = 2, ["zwei"] = 2,
        ["trz"] = 3, ["three"] = 3, ["drei"] = 3,
        ["czter"] = 4, ["four"] = 4, ["vier"] = 4,
        ["pię"] = 5, ["five"] = 5, ["fünf"] = 5,
    };

    [GeneratedRegex(@"(?:co najmniej|minimum|min\.?|at least|mindestens|nie mniej niż)\s+(\d+|\p{L}+)\s+(?:\p{L}+\s+)?(?:dostaw|zamówie|umów|deliver|contract|reference|referenc|Liefer|Auftr)", RegexOptions.IgnoreCase)]
    private static partial Regex CountPattern();

    [GeneratedRegex(@"(\d{1,3}(?:[ . ]\d{3})+|\d+)(?:[,.]\d{1,2})?\s*(?:(tys\.?|mln)\s*)?(zł|PLN|EUR|€|euro)", RegexOptions.IgnoreCase)]
    private static partial Regex ValuePattern();

    [GeneratedRegex(@"(\d{1,3}(?:[ . ]\d{3})+|\d+)\s*(szt|kpl|kompl|sets|pcs|pieces|units|Stück|Sätze|Satz)", RegexOptions.IgnoreCase)]
    private static partial Regex QuantityPattern();

    [GeneratedRegex(@"(?:ostatnich|last|letzten)\s+(\d+|\p{L}+)\s+(?:lat|years|Jahre)", RegexOptions.IgnoreCase)]
    private static partial Regex YearsPattern();

    private static int? Number(string s)
    {
        var digits = new string(s.Where(char.IsDigit).ToArray());
        if (digits.Length > 0 && int.TryParse(digits, out var n)) return n;
        return Words.FirstOrDefault(w => s.StartsWith(w.Key, StringComparison.OrdinalIgnoreCase)).Value is var v and > 0 ? v : null;
    }

    public static ExperienceAsk Read(string text)
    {
        var count = CountPattern().Match(text) is { Success: true } c ? Number(c.Groups[1].Value) ?? 1 : 1;
        decimal? value = null;
        if (ValuePattern().Match(text) is { Success: true } v && Number(v.Groups[1].Value) is { } amount)
        {
            var scaled = (decimal)amount * (v.Groups[2].Value.ToLowerInvariant() switch { "mln" => 1_000_000m, "tys" or "tys." => 1_000m, _ => 1m });
            value = v.Groups[3].Value.ToUpperInvariant() is "ZŁ" or "PLN" ? Math.Round(scaled / PlnPerEur) : scaled;
        }
        var quantity = QuantityPattern().Match(text) is { Success: true } q ? Number(q.Groups[1].Value) : null;
        var years = YearsPattern().Match(text) is { Success: true } y ? Number(y.Groups[1].Value) : null;
        return new ExperienceAsk(count, value, quantity, years ?? DefaultYears, years is not null);
    }

    public static EvidenceCheck Evaluate(string need, string requirementText, DateTimeOffset? deadline, CompanyProfile profile)
    {
        var ask = Read(requirementText);
        var at = DateOnly.FromDateTime((deadline ?? DateTimeOffset.UtcNow).UtcDateTime);
        var from = at.AddYears(-ask.Years);
        var en = CultureInfo.GetCultureInfo("en-GB");
        var asked = $"{ask.Count} deliver{(ask.Count == 1 ? "y" : "ies")}" +
                    (ask.MinValueEur is { } mv ? $" worth at least €{mv.ToString("N0", en)}" : "") +
                    (ask.MinQuantity is { } mq ? $" of at least {mq} units" : "") +
                    $" in the {ask.Years} years before the deadline{(ask.YearsStated ? "" : " (the usual window for supplies)")}";

        var refs = profile.Documents.Where(d => d.Kind == "reference").ToList();
        if (refs.Count == 0) return new EvidenceCheck(need, Outcome.Missing, $"The tender asks for {asked}. No references in your profile.", null);

        // A reference counts only for a delivery finished inside the window; a won contract still being delivered does not yet.
        var inWindow = refs.Where(r => r.Delivery is null ? r.IssuedOn is { } i && i >= from && i <= at : r.Delivery.Completed >= from && r.Delivery.Completed <= at).ToList();
        var pending = refs.Where(r => r.Delivery?.Completed > at).ToList();
        var confirmed = inWindow.Where(r => Meets(r.Delivery?.ValueEur, ask.MinValueEur) == true && Meets(r.Delivery?.Quantity, ask.MinQuantity) == true).ToList();
        var unknown = inWindow.Except(confirmed).Where(r => Meets(r.Delivery?.ValueEur, ask.MinValueEur) != false && Meets(r.Delivery?.Quantity, ask.MinQuantity) != false).ToList();

        if (confirmed.Count >= ask.Count)
            return new EvidenceCheck(need, Outcome.Met,
                $"The tender asks for {asked}. {Describe(confirmed.Take(ask.Count), en)} meet{(ask.Count == 1 ? "s" : "")} it.",
                confirmed[0], AlsoSubmit: confirmed.Skip(1).Take(ask.Count - 1).ToList());

        if (confirmed.Count + unknown.Count >= ask.Count)
            return new EvidenceCheck(need, Outcome.Unclear,
                $"The tender asks for {asked}. {Describe(unknown, en)} could count, but the profile does not record " +
                $"{(ask.MinValueEur is not null ? "the contract value" : "the quantity")}, so it is not counted as met. Add it to the reference.",
                unknown[0]);

        var why = inWindow.Count == 0
            ? $"None of your {refs.Count} references is a delivery completed between {from:yyyy-MM-dd} and {at:yyyy-MM-dd}."
            : $"Only {confirmed.Count} of your references meet{(confirmed.Count == 1 ? "s" : "")} it.";
        if (pending.Count > 0) why += $" {Describe(pending, en)} will count once delivered.";
        return new EvidenceCheck(need, Outcome.Missing, $"The tender asks for {asked}. {why}", inWindow.FirstOrDefault() ?? refs[0]);
    }

    /// <summary>True: meets the threshold. False: below it. Null: no threshold to meet is fine; an unknown value is not.</summary>
    private static bool? Meets(decimal? have, decimal? need) => need is null ? true : have is null ? null : have >= need;
    private static bool? Meets(int? have, int? need) => need is null ? true : have is null ? null : have >= need;

    private static string Describe(IEnumerable<CompanyDocument> docs, CultureInfo en) =>
        string.Join(" and ", docs.Select(d => d.Delivery is { } x
            ? $"{x.Subject} for {x.Buyer} ({(x.Quantity is null ? "" : $"{x.Quantity} {x.Unit}, ")}{(x.ValueEur is null ? "" : $"€{x.ValueEur.Value.ToString("N0", en)}, ")}{x.Completed:MMM yyyy})"
            : d.Title));
}
