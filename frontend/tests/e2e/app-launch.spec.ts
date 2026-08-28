/**
 * Electron 端到端烟测：验证桌宠、正式语音失败清理、主聊天、设置与 Backend 缺席错误投影。
 * 测试使用隔离 userData 且不启动 Python Backend，确认桌面不会回落到旧 Chat/Memory；语音用
 * 本地请求拦截验证真实 UI 生命周期，不访问麦克风、
 * runner、Task IPC、Task UI 或外部服务。
 */
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { _electron as electron, expect, type ElectronApplication, type Page, test } from "@playwright/test";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

async function launchAuraBot(): Promise<{ electronApp: ElectronApplication; petWindow: Page; userDataDir: string }> {
  const userDataDir = mkdtempSync(join(tmpdir(), "aurabot-e2e-"));
  const electronApp = await electron.launch({
    args: [join(appRoot, "out/main/index.js")],
    cwd: appRoot,
    env: {
      ...process.env,
      NODE_ENV: "test",
      AURABOT_USER_DATA_DIR: userDataDir,
      AURABOT_DISABLE_SYSTEM_CONFIG: "1",
      AURABOT_DETERMINISTIC_MODEL_FALLBACK: "1"
    }
  });
  return { electronApp, petWindow: await electronApp.firstWindow(), userDataDir };
}

async function cleanup(electronApp: ElectronApplication, userDataDir: string): Promise<void> {
  await electronApp.close();
  rmSync(userDataDir, { force: true, recursive: true });
}

async function openConsole(electronApp: ElectronApplication, petWindow: Page): Promise<Page> {
  const consolePromise = electronApp.waitForEvent("window");
  await petWindow.getByTestId("live2d-model-surface").hover();
  await petWindow.getByRole("button", { name: "Open AuraBot console" }).click();
  const consoleWindow = await consolePromise;
  await consoleWindow.getByRole("main", { name: "AuraBot Console" }).waitFor();
  return consoleWindow;
}

test("launches the Live2D pet and opens the chat-only Console", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();
  try {
    await expect(petWindow.getByLabel("Desktop Live2D pet")).toBeVisible();
    const consoleWindow = await openConsole(electronApp, petWindow);
    await expect(consoleWindow.getByPlaceholder("Say something")).toBeVisible();
    await expect(consoleWindow.locator(".console-task-sidebar")).toHaveCount(0);
  } finally {
    await cleanup(electronApp, userDataDir);
  }
});

test("formal Pet voice toggle reports token failure and returns to a retryable inactive state", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();
  try {
    await petWindow.route(
      "http://127.0.0.1:6401/api/v1/main-agent/realtime-token",
      (route) => route.abort("connectionrefused")
    );
    await petWindow.getByTestId("live2d-model-surface").hover();
    const voice = petWindow.getByRole("button", { name: "Start voice input" });
    await voice.click();
    await expect(voice).toHaveAttribute("data-state", "error");
    await expect(voice).toHaveAttribute("data-active", "false");
  } finally {
    await cleanup(electronApp, userDataDir);
  }
});

test("returns the explicit Main Agent unavailable message when Backend is absent", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();
  try {
    const consoleWindow = await openConsole(electronApp, petWindow);
    await consoleWindow.getByPlaceholder("Say something").fill("帮我写一份周报");
    await consoleWindow.getByPlaceholder("Say something").press("Enter");
    await expect(consoleWindow.getByLabel("Conversation messages"))
      .toContainText("主 Agent 暂时不可用，请确认本地 Backend 已启动后重试。");
    await expect(petWindow.getByLabel("Pet message"))
      .toContainText("主 Agent 暂时不可用，请确认本地 Backend 已启动后重试。");
  } finally {
    await cleanup(electronApp, userDataDir);
  }
});

test("broadcasts the Main Agent unavailable message from Pet to Console", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();
  try {
    const consoleWindow = await openConsole(electronApp, petWindow);
    await petWindow.evaluate(async () => {
      await window.aurabot.sendChatMessage({
        content: "帮我整理桌面",
        source: "pet_text"
      });
    });
    await expect(consoleWindow.getByLabel("Conversation messages"))
      .toContainText("主 Agent 暂时不可用，请确认本地 Backend 已启动后重试。");
  } finally {
    await cleanup(electronApp, userDataDir);
  }
});

test("opens retained settings without Task CLI or scheduled reminder controls", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();
  try {
    const consoleWindow = await openConsole(electronApp, petWindow);
    const settingsPromise = electronApp.waitForEvent("window");
    await consoleWindow.getByRole("button", { name: "Open AuraBot settings" }).click();
    const settingsWindow = await settingsPromise;
    await settingsWindow.getByRole("main", { name: "Settings" }).waitFor();
    await expect(settingsWindow.getByText("Task CLI", { exact: true })).toHaveCount(0);
    await expect(settingsWindow.getByText("Scheduled reminders", { exact: true })).toHaveCount(0);
    await expect(settingsWindow.getByText("Models & Connections", { exact: true })).toBeVisible();
  } finally {
    await cleanup(electronApp, userDataDir);
  }
});
