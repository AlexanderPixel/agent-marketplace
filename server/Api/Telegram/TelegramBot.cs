using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;
using Marketplace.AutoBid;
using Microsoft.Extensions.Options;

namespace Marketplace.Telegram;

public class TelegramOptions
{
    /// <summary>From @BotFather. Without it the bot stays off and decisions are made in the app.</summary>
    public string BotToken { get; set; } = "";
    /// <summary>The one chat allowed to receive requests and press Sign. Empty: the first chat that sends /start.</summary>
    public long? ChatId { get; set; }
    /// <summary>Base for the "details" links in messages.</summary>
    public string AppBaseUrl { get; set; } = "http://localhost:5080";
}

/// <summary>
/// The company's Telegram bot. It long-polls Telegram (no public URL needed), sends sign requests with Sign / Don't sign
/// buttons and passes a press on to <see cref="AutoBidDecisions"/>. It only listens to its one chat.
/// </summary>
public class TelegramBot(IOptions<TelegramOptions> options, IHttpClientFactory http, IServiceScopeFactory scopes, ILogger<TelegramBot> log)
    : BackgroundService
{
    private readonly TelegramOptions o = options.Value;
    private long? chatId = options.Value.ChatId;

    public bool Configured => !string.IsNullOrWhiteSpace(o.BotToken);
    public bool Connected => Configured && chatId is not null;
    public string AppBaseUrl => o.AppBaseUrl.TrimEnd('/');

    private HttpClient Client()
    {
        var c = http.CreateClient("telegram");
        c.BaseAddress = new Uri($"https://api.telegram.org/bot{o.BotToken}/");
        c.Timeout = TimeSpan.FromSeconds(40);
        return c;
    }

    private async Task<JsonNode?> CallAsync(string method, object body, CancellationToken ct = default)
    {
        using var response = await Client().PostAsJsonAsync(method, body, ct);
        var json = JsonNode.Parse(await response.Content.ReadAsStringAsync(ct));
        if (json?["ok"]?.GetValue<bool>() != true) log.LogWarning("Telegram {Method} failed: {Body}", method, json?.ToJsonString());
        return json?["result"];
    }

    /// <summary>Sends an HTML message with inline buttons; returns its id, or null when the bot is not connected.</summary>
    public async Task<long?> SendAsync(string html, IEnumerable<IEnumerable<object>>? keyboard = null, CancellationToken ct = default)
    {
        if (!Connected) return null;
        var result = await CallAsync("sendMessage", new
        {
            chat_id = chatId,
            text = html,
            parse_mode = "HTML",
            link_preview_options = new { is_disabled = true },
            reply_markup = keyboard is null ? null : new { inline_keyboard = keyboard },
        }, ct);
        return result?["message_id"]?.GetValue<long>();
    }

    /// <summary>Replaces the buttons under a sign request with the decision, so it cannot be pressed twice.</summary>
    public Task MarkDecidedAsync(long messageId, string note, CancellationToken ct = default) =>
        Connected
            ? CallAsync("editMessageReplyMarkup", new { chat_id = chatId, message_id = messageId, reply_markup = new { inline_keyboard = new[] { new[] { new { text = note, callback_data = "noop" } } } } }, ct)
            : Task.CompletedTask;

    public static object Button(string text, string data) => new { text, callback_data = data };
    public static object Link(string text, string url) => new { text, url };
    public static string H(string? s) => WebUtility.HtmlEncode(s ?? "");

    protected override async Task ExecuteAsync(CancellationToken stop)
    {
        if (!Configured)
        {
            log.LogInformation("Telegram bot is off: set Telegram:BotToken to turn it on.");
            return;
        }

        long offset = 0;
        while (!stop.IsCancellationRequested)
        {
            try
            {
                var updates = await CallAsync("getUpdates", new { offset, timeout = 25, allowed_updates = new[] { "message", "callback_query" } }, stop);
                foreach (var u in updates?.AsArray() ?? [])
                {
                    offset = u!["update_id"]!.GetValue<long>() + 1;
                    await HandleAsync(u, stop);
                }
            }
            catch (OperationCanceledException) when (stop.IsCancellationRequested)
            {
                return;
            }
            catch (Exception e)
            {
                log.LogWarning(e, "Telegram polling failed; retrying");
                await Task.Delay(TimeSpan.FromSeconds(5), stop);
            }
        }
    }

    private async Task HandleAsync(JsonNode u, CancellationToken ct)
    {
        if (u["message"] is { } message)
        {
            var from = message["chat"]!["id"]!.GetValue<long>();
            var text = message["text"]?.GetValue<string>() ?? "";
            if (!text.StartsWith("/start")) return;
            if (chatId is null)
            {
                chatId = from;
                log.LogInformation("Telegram bot linked to chat {Chat}. Put it in Telegram:ChatId to keep it after a restart.", from);
            }
            var reply = chatId == from
                ? $"Connected. Sign requests from EU Patriot will arrive here.\nChat id: <code>{from}</code>"
                : "This bot is linked to another chat.";
            await CallAsync("sendMessage", new { chat_id = from, text = reply, parse_mode = "HTML" }, ct);
            return;
        }

        if (u["callback_query"] is not { } q) return;
        var queryId = q["id"]!.GetValue<string>();
        var fromChat = q["message"]?["chat"]?["id"]?.GetValue<long>();
        var data = q["data"]?.GetValue<string>() ?? "";
        var who = q["from"]?["first_name"]?.GetValue<string>() ?? "Telegram user";
        string answer;
        if (fromChat != chatId) answer = "Not allowed from this chat.";
        else if (data.Split(':') is [var verb and ("sign" or "decline"), var id] && Guid.TryParse(id, out var runId))
        {
            using var scope = scopes.CreateScope();
            var decisions = scope.ServiceProvider.GetRequiredService<AutoBidDecisions>();
            answer = await decisions.DecideAsync(runId, verb == "sign", $"{who} via Telegram", ct);
        }
        else answer = "Already decided.";
        await CallAsync("answerCallbackQuery", new { callback_query_id = queryId, text = answer }, ct);
    }
}
