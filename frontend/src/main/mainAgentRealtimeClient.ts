/**
 * Electron Main 的生产文本实时客户端：获取短期 capability、持有唯一 Main Agent 会话、
 * 提交文本命令并在 clause 已被桌面投影持久化后确认。它不处理音频，也不依赖旧 Chat/
 * Memory 服务；调用方注入可设置 Origin 的 WebSocket 工厂以满足 Backend 握手边界。
 */

const MAIN_AGENT_PROTOCOL = "aurabot.main-agent.v1";
const DEFAULT_DESKTOP_ORIGIN = "http://127.0.0.1:6400";
const DEFAULT_COMMAND_TIMEOUT_MS = 5_000;

export interface RealtimeSocket {
  readyState: number;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  send(data: string): void;
  close(): void;
}

export interface MainAgentClauseOffer {
  clauseId: string;
  inputId: string;
  invokeId: string;
  revision: number;
  text: string;
}

export interface MainAgentRealtimeClientOptions {
  baseUrl: string;
  createWebSocket(
    url: string,
    protocols: readonly string[],
    options: { origin: string }
  ): RealtimeSocket;
  onClauseOffer(offer: MainAgentClauseOffer): Promise<void> | void;
  fetchImpl?: (input: string, init?: RequestInit) => Promise<Response>;
  uuidFactory?: () => string;
  desktopOrigin?: string;
  commandTimeoutMs?: number;
}

interface PendingCommand {
  resolve(): void;
  reject(error: Error): void;
  timer: ReturnType<typeof setTimeout>;
}

interface TokenResponse {
  token: string;
  subprotocol: string;
}

export class MainAgentRealtimeClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: (input: string, init?: RequestInit) => Promise<Response>;
  private readonly uuidFactory: () => string;
  private readonly desktopOrigin: string;
  private readonly commandTimeoutMs: number;
  private socket: RealtimeSocket | undefined;
  private connection: Promise<void> | undefined;
  private connectionResolve: (() => void) | undefined;
  private connectionReject: ((error: Error) => void) | undefined;
  private sequence = 0;
  private rendererEpoch: number | undefined;
  private sessionReady = false;
  private closing = false;
  private readonly pending = new Map<string, PendingCommand>();
  private readonly handledClauseIds = new Set<string>();

  constructor(private readonly options: MainAgentRealtimeClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.uuidFactory = options.uuidFactory ?? (() => crypto.randomUUID());
    this.desktopOrigin = options.desktopOrigin ?? DEFAULT_DESKTOP_ORIGIN;
    this.commandTimeoutMs = options.commandTimeoutMs ?? DEFAULT_COMMAND_TIMEOUT_MS;
  }

  async submitText(text: string): Promise<void> {
    if (!text.trim()) {
      throw new Error("text_required");
    }
    await this.ensureConnected();
    await this.sendConfirmedCommand("text.submit", { text });
  }

  close(): void {
    this.closing = true;
    const socket = this.socket;
    this.resetConnection(new Error("main_agent_client_closed"));
    if (socket && socket.readyState < 2) {
      socket.close();
    }
    this.closing = false;
  }

  private async ensureConnected(): Promise<void> {
    if (this.socket?.readyState === 1 && this.sessionReady) {
      return;
    }
    if (!this.connection) {
      this.connection = this.connect().catch((error: unknown) => {
        const normalized = asError(error, "main_agent_connection_failed");
        this.resetConnection(normalized);
        throw normalized;
      });
    }
    await this.connection;
  }

  private async connect(): Promise<void> {
    const response = await this.fetchImpl(`${this.baseUrl}/api/v1/main-agent/realtime-token`, {
      method: "POST",
      headers: { Origin: this.desktopOrigin }
    });
    if (!response.ok) {
      throw new Error(`main_agent_token_${response.status}`);
    }
    const token = await response.json() as TokenResponse;
    if (!token.token || token.subprotocol !== MAIN_AGENT_PROTOCOL) {
      throw new Error("main_agent_token_invalid");
    }

    this.sequence = 0;
    this.rendererEpoch = undefined;
    this.sessionReady = false;
    this.handledClauseIds.clear();
    const socket = this.options.createWebSocket(
      realtimeUrl(this.baseUrl),
      [MAIN_AGENT_PROTOCOL, `aurabot.capability.${token.token}`],
      { origin: this.desktopOrigin }
    );
    this.socket = socket;

    const opened = new Promise<void>((resolve, reject) => {
      this.connectionResolve = resolve;
      this.connectionReject = reject;
    });
    const timeout = setTimeout(() => {
      this.connectionReject?.(new Error("main_agent_session_timeout"));
    }, this.commandTimeoutMs);

    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.sendEvent("session.open", {
        command_id: this.uuidFactory(),
        client_id: this.uuidFactory(),
        mode: "text"
      });
    };
    socket.onmessage = (event) => {
      if (this.socket !== socket || typeof event.data !== "string") return;
      this.handleEvent(JSON.parse(event.data) as Record<string, unknown>);
    };
    socket.onerror = () => {
      if (this.socket === socket && !this.sessionReady) {
        this.connectionReject?.(new Error("main_agent_transport_error"));
      }
    };
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.resetConnection(new Error(
        this.closing ? "main_agent_client_closed" : `main_agent_transport_closed_${event.code}`
      ));
    };

    try {
      await opened;
    } finally {
      clearTimeout(timeout);
      this.connectionResolve = undefined;
      this.connectionReject = undefined;
    }
  }

  private handleEvent(event: Record<string, unknown>): void {
    if (event.type === "session.opened") {
      this.rendererEpoch = positiveInteger(event.renderer_epoch, "renderer_epoch");
      this.sessionReady = true;
      this.connectionResolve?.();
      return;
    }
    if (event.type === "state.snapshot") {
      this.rendererEpoch = positiveInteger(event.renderer_epoch, "renderer_epoch");
      return;
    }
    if (event.type === "command.ack") {
      this.settleCommand(event);
      return;
    }
    if (event.type === "clause.offer") {
      void this.deliverClause(event);
      return;
    }
    if (event.type === "session.replaced") {
      this.resetConnection(new Error("main_agent_session_replaced"));
      return;
    }
    if (event.type === "session.error" && event.fatal === true) {
      this.resetConnection(new Error(String(event.code ?? "main_agent_session_error")));
    }
  }

  private settleCommand(event: Record<string, unknown>): void {
    const commandId = String(event.command_id ?? "");
    const command = this.pending.get(commandId);
    if (!command) return;
    clearTimeout(command.timer);
    this.pending.delete(commandId);
    if (event.status === "accepted") {
      command.resolve();
    } else {
      command.reject(new Error(String(event.code ?? event.error ?? "main_agent_command_rejected")));
    }
  }

  private async deliverClause(event: Record<string, unknown>): Promise<void> {
    const clauseId = String(event.clause_id ?? "");
    if (!clauseId || this.handledClauseIds.has(clauseId)) return;
    const epoch = positiveInteger(event.renderer_epoch, "renderer_epoch");
    const offer: MainAgentClauseOffer = {
      clauseId,
      inputId: String(event.input_id ?? ""),
      invokeId: String(event.invoke_id ?? ""),
      revision: positiveInteger(event.revision, "revision"),
      text: String(event.text ?? "")
    };
    if (!offer.inputId || !offer.text) return;
    this.handledClauseIds.add(clauseId);
    try {
      await this.options.onClauseOffer(offer);
      if (this.socket?.readyState !== 1 || this.rendererEpoch !== epoch) return;
      this.sendEvent("renderer.ack", {
        command_id: this.uuidFactory(),
        renderer_epoch: epoch,
        clause_id: clauseId,
        complete: true
      });
    } catch {
      this.handledClauseIds.delete(clauseId);
    }
  }

  private sendConfirmedCommand(type: string, fields: Record<string, unknown>): Promise<void> {
    if (!this.sessionReady || this.socket?.readyState !== 1) {
      return Promise.reject(new Error("main_agent_not_connected"));
    }
    const commandId = this.uuidFactory();
    const result = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(commandId);
        reject(new Error("main_agent_command_timeout"));
      }, this.commandTimeoutMs);
      this.pending.set(commandId, { resolve, reject, timer });
    });
    this.sendEvent(type, { command_id: commandId, ...fields });
    return result;
  }

  private sendEvent(type: string, fields: Record<string, unknown>): void {
    const socket = this.socket;
    if (!socket || socket.readyState !== 1) {
      throw new Error("main_agent_not_connected");
    }
    socket.send(JSON.stringify({ type, seq: ++this.sequence, ...fields }));
  }

  private resetConnection(error: Error): void {
    this.connectionReject?.(error);
    this.connection = undefined;
    this.connectionResolve = undefined;
    this.connectionReject = undefined;
    this.sessionReady = false;
    this.rendererEpoch = undefined;
    this.socket = undefined;
    for (const command of this.pending.values()) {
      clearTimeout(command.timer);
      command.reject(error);
    }
    this.pending.clear();
  }
}

function realtimeUrl(baseUrl: string): string {
  const url = new URL(baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/api/v1/main-agent/realtime";
  url.search = "";
  url.hash = "";
  return url.toString().replace(/\/$/, "");
}

function positiveInteger(value: unknown, field: string): number {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error(`invalid_${field}`);
  }
  return parsed;
}

function asError(error: unknown, fallback: string): Error {
  return error instanceof Error ? error : new Error(fallback);
}
