export interface ChatCompletionMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface ChatCompletionRequest {
  baseUrl: string;
  apiKey: string;
  model: string;
  messages: ChatCompletionMessage[];
  temperature?: number;
  timeoutMs?: number;
}

export interface OpenAICompatibleClientOptions {
  fetchImplementation?: typeof fetch;
}

export class OpenAICompatibleChatCompletionClient {
  private readonly fetchImplementation: typeof fetch;

  constructor(options: OpenAICompatibleClientOptions = {}) {
    this.fetchImplementation = options.fetchImplementation ?? fetch;
  }

  async complete(input: ChatCompletionRequest): Promise<string> {
    const abortController = input.timeoutMs ? new AbortController() : undefined;
    const timeout = abortController
      ? setTimeout(() => abortController.abort(), input.timeoutMs)
      : undefined;

    const response = await this.fetchImplementation(buildEndpoint(input.baseUrl), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${input.apiKey}`,
        "content-type": "application/json"
      },
      signal: abortController?.signal,
      body: JSON.stringify({
        model: input.model,
        messages: input.messages,
        temperature: input.temperature ?? 0.2
      })
    }).finally(() => {
      if (timeout) {
        clearTimeout(timeout);
      }
    });

    if (!response.ok) {
      throw new Error(`OpenAI-compatible request failed with status ${response.status}`);
    }

    const payload = (await response.json()) as {
      choices?: Array<{
        message?: {
          content?: string | null;
        };
        text?: string | null;
      }>;
    };
    const choice = payload.choices?.[0];
    const content = choice?.message?.content ?? choice?.text ?? "";

    if (!content.trim()) {
      throw new Error("OpenAI-compatible response did not include content");
    }

    return content;
  }
}

function buildEndpoint(baseUrl: string): string {
  return new URL("chat/completions", baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`).toString();
}
