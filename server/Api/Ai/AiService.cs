using System.ClientModel;
using System.Text.Json;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.Options;
using OpenAI;

namespace Marketplace.Ai;

/// <summary>
/// One place that talks to the model. Callers ask for JSON and get null back on any failure, so each feature
/// can fall back to its rule-based path instead of showing an error during the demo.
/// </summary>
public class AiService
{
    private readonly IChatClient? chat;
    private readonly ILogger<AiService> logger;

    public AiService(IOptions<AiOptions> options, ILogger<AiService> logger)
    {
        this.logger = logger;
        var o = options.Value;
        Model = o.Model;
        if (!string.IsNullOrWhiteSpace(o.ApiKey))
        {
            var client = new OpenAIClient(new ApiKeyCredential(o.ApiKey), new OpenAIClientOptions { Endpoint = new Uri(o.BaseUrl) });
            chat = client.GetChatClient(o.Model).AsIChatClient();
        }
    }

    public bool Enabled => chat is not null;
    public string Model { get; }

    public async Task<T?> AskJsonAsync<T>(string system, string user, CancellationToken ct = default) where T : class
    {
        if (chat is null)
        {
            return null;
        }

        try
        {
            List<ChatMessage> messages = [new(ChatRole.System, system), new(ChatRole.User, user)];
            var options = new ChatOptions { ResponseFormat = ChatResponseFormat.Json, Temperature = 0.2f };
            var response = await chat.GetResponseAsync(messages, options, ct);
            var text = response.Text;
            var start = text.IndexOf('{');
            var end = text.LastIndexOf('}');
            if (start < 0 || end <= start)
            {
                logger.LogWarning("The model reply had no JSON object.");
                return null;
            }

            return JsonSerializer.Deserialize<T>(text[start..(end + 1)], JsonSerializerOptions.Web);
        }
        catch (Exception e) when (e is not OperationCanceledException)
        {
            logger.LogWarning(e, "The model call failed; using the fallback.");
            return null;
        }
    }

    /// <summary>
    /// Swaps angle brackets for look-alikes so document text cannot close a &lt;source&gt; section and pose as
    /// instructions (same trick as ai-knowledge-assistant's AnswerPrompt).
    /// </summary>
    public static string Neutralize(string text) => text.Replace('<', '‹').Replace('>', '›');
}
