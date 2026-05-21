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

  async streamComplete(
    input: ChatCompletionRequest,
    onDelta: (delta: string) => void | Promise<void>
  ): Promise<string> {
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
        temperature: input.temperature ?? 0.2,
        stream: true
      })
    }).finally(() => {
      if (timeout) {
        clearTimeout(timeout);
      }
    });

    if (!response.ok) {
      throw new Error(`OpenAI-compatible request failed with status ${response.status}`);
    }

    if (!response.body) {
      throw new Error("OpenAI-compatible streaming response did not include a body");
    }

    const decoder = new TextDecoder();
    const reader = response.body.getReader();
    let buffer = "";
    let content = "";

    while (true) {
      const { done, value } = await reader.read();

      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const parsed = await consumeSseBuffer(buffer, onDelta);
      buffer = parsed.remaining;
      content += parsed.content;
    }

    if (buffer.trim()) {
      const parsed = await consumeSseBuffer(`${buffer}\n\n`, onDelta);
      content += parsed.content;
    }

    if (!content.trim()) {
      throw new Error("OpenAI-compatible streaming response did not include content");
    }

    return content;
  }
}

function buildEndpoint(baseUrl: string): string {
  return new URL("chat/completions", baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`).toString();
}

async function consumeSseBuffer(
  buffer: string,
  onDelta: (delta: string) => void | Promise<void>
): Promise<{ content: string; remaining: string }> {
  const parts = buffer.split(/\r?\n\r?\n/);
  const remaining = parts.pop() ?? "";
  let content = "";

  for (const part of parts) {
    const delta = parseSseDelta(part);

    if (!delta) {
      continue;
    }

    content += delta;
    await onDelta(delta);
  }

  return { content, remaining };
}

function parseSseDelta(part: string): string | undefined {
  const dataLines = part
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice("data:".length).trim())
    .filter(Boolean);

  let content = "";

  for (const dataLine of dataLines) {
    if (dataLine === "[DONE]") {
      continue;
    }

    try {
      const payload = JSON.parse(dataLine) as {
        choices?: Array<{
          delta?: {
            content?: string | null;
          };
          message?: {
            content?: string | null;
          };
          text?: string | null;
        }>;
      };
      const choice = payload.choices?.[0];
      const delta = choice?.delta?.content ?? choice?.message?.content ?? choice?.text ?? "";

      content += delta;
    } catch {
      continue;
    }
  }

  return content || undefined;
}
