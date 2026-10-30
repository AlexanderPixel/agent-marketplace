namespace Marketplace.Ai;

/// <summary>Bound from the <c>AI</c> section. OpenRouter by default; any OpenAI-compatible endpoint works.</summary>
public class AiOptions
{
    public string BaseUrl { get; set; } = "https://openrouter.ai/api/v1";

    /// <summary>Empty means AI is off and every feature uses its rule-based fallback, so the demo still runs.</summary>
    public string ApiKey { get; set; } = "";

    public string Model { get; set; } = "openai/gpt-4.1-mini";
}
