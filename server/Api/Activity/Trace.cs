using Marketplace.Data;
using Marketplace.Qualification;

namespace Marketplace.Activity;

/// <summary>
/// Structured details stored with an activity event, so the "Under the hood" page can replay a run step by step:
/// what the agent looked at, what it concluded, and the exact Telegram message it sent.
/// </summary>
public static class Trace
{
    public record TelegramMessage(string Html, object Buttons, bool Sent, long? MessageId);

    public record WithTelegram(TelegramMessage Telegram);

    /// <summary>Subject that groups the events of one supplier change.</summary>
    public static string Change(Guid changeId) => $"change:{changeId}";

    /// <param name="messageId">Null when the bot is not connected: the request then waits in the app, but the text is kept.</param>
    public static WithTelegram Telegram(string html, object buttons, long? messageId) =>
        new(new TelegramMessage(html, buttons, messageId is not null, messageId));

    public static object Qualification(Tender tender, Assessment a) => new
    {
        verdict = a.Verdict,
        requirements = a.Requirements.Select(r => new
        {
            text = r.Requirement.Text,
            outcome = r.Outcome.ToString(),
            evidence = r.Checks.Select(c => new { need = c.Need, outcome = c.Outcome.ToString(), c.Reason, document = c.Evidence?.Title }),
        }),
        lots = a.Lots.Select(l => new { id = l.Lot.Id, title = l.Lot.Title, quantity = l.Lot.Quantity, unit = l.Lot.Unit, capacity = l.Capacity, gap = l.Gap, fit = l.Fit.ToString() }),
        notOffered = a.NotOffered,
        deadline = tender.Deadline,
    };
}
