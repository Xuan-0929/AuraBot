import { createServer, type Server } from "node:http";
import { afterEach, describe, expect, it } from "vitest";
import {
  getBackendEventLogs,
  getBackendMainSessionMessages,
  getBackendMemoryDebugSnapshot,
  getBackendStatus,
  getBackendTasks,
  getBackendTaskDebugSnapshot
} from "../../../src/main/backendGatewayClient";

let server: Server | undefined;

afterEach(async () => {
  if (!server) {
    return;
  }

  await new Promise<void>((resolve, reject) => {
    server?.close((error) => (error ? reject(error) : resolve()));
  });
  server = undefined;
});

describe("getBackendStatus", () => {
  it("returns gateway health and registered services", async () => {
    const baseUrl = await startJsonServer({
      "/health": {
        ok: true,
        service: "gateway",
        host: "127.0.0.1",
        port: 6401,
        frontend: "http://127.0.0.1:6400"
      },
      "/services": {
        services: [
          {
            name: "memory",
            host: "127.0.0.1",
            port: 6402
          }
        ]
      }
    });

    const status = await getBackendStatus({ baseUrl, timeoutMs: 500 });

    expect(status).toMatchObject({
      online: true,
      gateway: {
        ok: true,
        service: "gateway",
        host: "127.0.0.1",
        port: 6401,
        frontend: "http://127.0.0.1:6400"
      },
      services: [
        {
          name: "memory",
          host: "127.0.0.1",
          port: 6402,
          url: "http://127.0.0.1:6402"
        }
      ]
    });
    expect(status.checkedAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect(status.error).toBeUndefined();
  });

  it("reports offline status when the gateway cannot be reached", async () => {
    const status = await getBackendStatus({
      baseUrl: "http://127.0.0.1:9",
      timeoutMs: 50
    });

    expect(status.online).toBe(false);
    expect(status.services).toEqual([]);
    expect(status.error).toMatch(/Backend gateway is unavailable/);
    expect(status.checkedAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });

  it("accepts an empty service registry", async () => {
    const baseUrl = await startJsonServer({
      "/health": {
        ok: true,
        service: "gateway",
        host: "127.0.0.1",
        port: 6401,
        frontend: "http://127.0.0.1:6400"
      },
      "/services": {
        services: []
      }
    });

    const status = await getBackendStatus({ baseUrl, timeoutMs: 500 });

    expect(status.online).toBe(true);
    expect(status.services).toEqual([]);
  });
});

describe("getBackendEventLogs", () => {
  it("returns event logs from the backend gateway", async () => {
    const baseUrl = await startJsonServer({
      "/debug/events?storage_path=%2Ftmp%2Faurabot.sqlite&limit=2": {
        events: [
          {
            id: "event-1",
            eventType: "memory.write",
            payload: { ok: true },
            createdAt: "2026-05-21T00:00:00.000Z"
          }
        ]
      }
    });

    const events = await getBackendEventLogs({
      baseUrl,
      storagePath: "/tmp/aurabot.sqlite",
      limit: 2,
      timeoutMs: 500
    });

    expect(events).toEqual([
      {
        id: "event-1",
        eventType: "memory.write",
        payload: { ok: true },
        createdAt: "2026-05-21T00:00:00.000Z"
      }
    ]);
  });

  it("throws a serializable error when the backend event log request fails", async () => {
    await expect(
      getBackendEventLogs({
        baseUrl: "http://127.0.0.1:9",
        storagePath: "/tmp/aurabot.sqlite",
        timeoutMs: 50
      })
    ).rejects.toThrow(/Backend event logs are unavailable/);
  });
});

describe("getBackendMainSessionMessages", () => {
  it("returns main session messages from the backend gateway", async () => {
    const baseUrl = await startJsonServer({
      "/chat/main-session?storage_path=%2Ftmp%2Faurabot.sqlite&profile_id=profile-1": {
        sessionId: "session-main",
        messages: [
          {
            id: "message-1",
            sessionId: "session-main",
            profileId: "profile-1",
            role: "user",
            content: "你好",
            attachments: [],
            createdAt: "2026-05-21T00:00:00.000Z"
          }
        ]
      }
    });

    const snapshot = await getBackendMainSessionMessages({
      baseUrl,
      storagePath: "/tmp/aurabot.sqlite",
      profileId: "profile-1",
      timeoutMs: 500
    });

    expect(snapshot).toEqual({
      sessionId: "session-main",
      messages: [
        {
          id: "message-1",
          sessionId: "session-main",
          profileId: "profile-1",
          role: "user",
          content: "你好",
          attachments: [],
          createdAt: "2026-05-21T00:00:00.000Z"
        }
      ]
    });
  });

  it("throws a serializable error when the backend main session request fails", async () => {
    await expect(
      getBackendMainSessionMessages({
        baseUrl: "http://127.0.0.1:9",
        storagePath: "/tmp/aurabot.sqlite",
        profileId: "profile-1",
        timeoutMs: 50
      })
    ).rejects.toThrow(/Backend main session messages are unavailable/);
  });
});

describe("getBackendTasks", () => {
  it("returns tasks from the backend gateway", async () => {
    const baseUrl = await startJsonServer({
      "/tasks?storage_path=%2Ftmp%2Faurabot.sqlite&profile_id=profile-1": {
        tasks: [
          {
            id: "task-1",
            profileId: "profile-1",
            parentSessionId: "session-main",
            sessionId: "session-task",
            kind: "result_output",
            title: "整理周报",
            status: "queued",
            latestProgress: "Task queued from main chat.",
            createdAt: "2026-05-21T00:00:00.000Z",
            updatedAt: "2026-05-21T00:00:01.000Z"
          }
        ]
      }
    });

    const tasks = await getBackendTasks({
      baseUrl,
      storagePath: "/tmp/aurabot.sqlite",
      profileId: "profile-1",
      timeoutMs: 500
    });

    expect(tasks).toEqual([
      {
        id: "task-1",
        profileId: "profile-1",
        parentSessionId: "session-main",
        sessionId: "session-task",
        kind: "result_output",
        title: "整理周报",
        status: "queued",
        latestProgress: "Task queued from main chat.",
        createdAt: "2026-05-21T00:00:00.000Z",
        updatedAt: "2026-05-21T00:00:01.000Z"
      }
    ]);
  });

  it("throws a serializable error when the backend tasks request fails", async () => {
    await expect(
      getBackendTasks({
        baseUrl: "http://127.0.0.1:9",
        storagePath: "/tmp/aurabot.sqlite",
        profileId: "profile-1",
        timeoutMs: 50
      })
    ).rejects.toThrow(/Backend tasks are unavailable/);
  });
});

describe("getBackendMemoryDebugSnapshot", () => {
  it("returns memory debug data from the backend gateway", async () => {
    const baseUrl = await startJsonServer({
      "/debug/memory?storage_path=%2Ftmp%2Faurabot.sqlite&profile_id=profile-1&event_limit=80": {
        generatedAt: "2026-05-21T00:00:00.000Z",
        nodes: [
          {
            id: "node-1",
            profileId: "profile-1",
            type: "preference",
            state: "active",
            content: "喜欢火锅",
            importance: 0.8,
            createdAt: "2026-05-21T00:00:00.000Z",
            updatedAt: "2026-05-21T00:00:01.000Z"
          }
        ],
        edges: [],
        summaries: [],
        recentEvents: []
      }
    });

    const snapshot = await getBackendMemoryDebugSnapshot({
      baseUrl,
      storagePath: "/tmp/aurabot.sqlite",
      profileId: "profile-1",
      eventLimit: 80,
      timeoutMs: 500
    });

    expect(snapshot.nodes[0]).toMatchObject({
      id: "node-1",
      profileId: "profile-1",
      type: "preference"
    });
    expect(snapshot.generatedAt).toBe("2026-05-21T00:00:00.000Z");
  });

  it("throws a serializable error when the backend memory debug request fails", async () => {
    await expect(
      getBackendMemoryDebugSnapshot({
        baseUrl: "http://127.0.0.1:9",
        storagePath: "/tmp/aurabot.sqlite",
        profileId: "profile-1",
        timeoutMs: 50
      })
    ).rejects.toThrow(/Backend memory debug snapshot is unavailable/);
  });
});

describe("getBackendTaskDebugSnapshot", () => {
  it("returns task debug data from the backend gateway", async () => {
    const baseUrl = await startJsonServer({
      "/debug/tasks?storage_path=%2Ftmp%2Faurabot.sqlite&profile_id=profile-1&limit=50": {
        generatedAt: "2026-05-21T00:00:00.000Z",
        tasks: [
          {
            task: {
              id: "task-1",
              profileId: "profile-1",
              parentSessionId: "session-main",
              sessionId: "session-task",
              kind: "result_output",
              title: "整理周报",
              status: "completed",
              latestProgress: "已完成",
              createdAt: "2026-05-21T00:00:00.000Z",
              updatedAt: "2026-05-21T00:00:05.000Z"
            },
            inputArtifacts: [],
            artifacts: [],
            timeline: []
          }
        ]
      }
    });

    const snapshot = await getBackendTaskDebugSnapshot({
      baseUrl,
      storagePath: "/tmp/aurabot.sqlite",
      profileId: "profile-1",
      limit: 50,
      timeoutMs: 500
    });

    expect(snapshot.tasks[0].task).toMatchObject({
      id: "task-1",
      profileId: "profile-1",
      title: "整理周报"
    });
  });

  it("throws a serializable error when the backend task debug request fails", async () => {
    await expect(
      getBackendTaskDebugSnapshot({
        baseUrl: "http://127.0.0.1:9",
        storagePath: "/tmp/aurabot.sqlite",
        profileId: "profile-1",
        timeoutMs: 50
      })
    ).rejects.toThrow(/Backend task debug snapshot is unavailable/);
  });
});

async function startJsonServer(routes: Record<string, unknown>): Promise<string> {
  server = createServer((request, response) => {
    const payload = routes[request.url ?? ""];

    if (!payload) {
      response.writeHead(404);
      response.end();
      return;
    }

    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify(payload));
  });

  await new Promise<void>((resolve) => server?.listen(0, "127.0.0.1", resolve));
  const address = server.address();

  if (!address || typeof address === "string") {
    throw new Error("Expected TCP test server address.");
  }

  return `http://127.0.0.1:${address.port}`;
}
