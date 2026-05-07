import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron as electron, expect, type ElectronApplication, type Page, test } from "@playwright/test";

async function launchAuraBot(): Promise<{
  electronApp: ElectronApplication;
  petWindow: Page;
  userDataDir: string;
}>;
async function launchAuraBot(userDataDir: string): Promise<{
  electronApp: ElectronApplication;
  petWindow: Page;
  userDataDir: string;
}>;
async function launchAuraBot(inputUserDataDir?: string): Promise<{
  electronApp: ElectronApplication;
  petWindow: Page;
  userDataDir: string;
}> {
  const userDataDir = inputUserDataDir ?? mkdtempSync(join(tmpdir(), "aurabot-e2e-"));
  const electronApp = await electron.launch({
    args: [join(process.cwd(), "out/main/index.js")],
    env: {
      ...process.env,
      NODE_ENV: "test",
      AURABOT_USER_DATA_DIR: userDataDir,
      AURABOT_DISABLE_SYSTEM_CONFIG: "1",
      AURABOT_DETERMINISTIC_MODEL_FALLBACK: "1"
    }
  });
  const petWindow = await electronApp.firstWindow();

  return { electronApp, petWindow, userDataDir };
}

async function cleanupAuraBot(electronApp: ElectronApplication, userDataDir: string): Promise<void> {
  await electronApp.close();
  rmSync(userDataDir, { force: true, recursive: true });
}

async function getPetWindowBounds(electronApp: ElectronApplication) {
  return electronApp.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].getBounds());
}

async function openConsole(electronApp: ElectronApplication, petWindow: Page): Promise<Page> {
  const consolePromise = electronApp.waitForEvent("window");
  await petWindow.getByTestId("live2d-model-surface").hover();
  await petWindow.getByRole("button", { name: "Open AuraBot console" }).click();
  const consoleWindow = await consolePromise;
  await consoleWindow.getByRole("main", { name: "AuraBot Console" }).waitFor({ state: "visible" });
  return consoleWindow;
}

async function openSettingsFromPet(electronApp: ElectronApplication, petWindow: Page): Promise<Page> {
  const settingsPromise = electronApp.waitForEvent("window");
  await petWindow.getByTestId("live2d-model-surface").hover();
  await petWindow.getByRole("button", { name: "Open AuraBot settings" }).click();
  const settingsWindow = await settingsPromise;
  await settingsWindow.getByRole("main", { name: "Settings" }).waitFor({ state: "visible" });
  return settingsWindow;
}

test("launches as a small desktop pet and opens console from the hover control", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    await expect(petWindow.getByLabel("Desktop Live2D pet")).toBeVisible();
    await expect(petWindow.getByTestId("live2d-model-surface")).toBeVisible();

    const consoleWindow = await openConsole(electronApp, petWindow);
    await expect(consoleWindow.getByRole("main", { name: "AuraBot Console" })).toBeVisible();
    await expect(consoleWindow.getByLabel("Agent status")).toContainText("idle");
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("reveals pet quick controls and syncs click-through settings from the console", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    await petWindow.mouse.move(4, 4);
    await expect(petWindow.getByLabel("Pet quick controls")).toHaveAttribute("data-visible", "false");
    await expect(petWindow.getByTestId("live2d-model-surface")).toHaveCSS("opacity", "1");

    await petWindow.getByTestId("live2d-model-surface").hover();
    await expect(petWindow.getByLabel("Pet quick controls")).toHaveAttribute("data-visible", "true");
    await expect(petWindow.getByTestId("live2d-model-surface")).toHaveCSS("opacity", "1");

    await petWindow.getByRole("button", { name: "Drag pet" }).hover();
    await expect(petWindow.getByLabel("Pet quick controls")).toHaveAttribute("data-visible", "true");
    await expect(petWindow.getByTestId("live2d-model-surface")).toHaveCSS("opacity", "1");
    await expect(petWindow.getByRole("button", { name: "Drag pet" })).not.toHaveCSS("-webkit-app-region", "drag");

    const consoleWindow = await openConsole(electronApp, petWindow);

    await expect(consoleWindow.getByLabel("Hide character on hover")).toHaveCount(0);
    await expect(consoleWindow.getByLabel("Language")).toHaveCount(0);
    await consoleWindow.getByRole("button", { name: "Open settings menu" }).click();
    await expect(consoleWindow.getByRole("menu", { name: "Settings menu" })).toContainText("Quick settings");
    await expect(consoleWindow.getByRole("menu", { name: "Settings menu" })).not.toContainText("Model config");

    const settingsPromise = electronApp.waitForEvent("window");
    await consoleWindow.getByRole("button", { name: "Open full settings" }).click();
    const settingsWindow = await settingsPromise;
    await settingsWindow.getByRole("main", { name: "Settings" }).waitFor({ state: "visible" });
    const settingsNav = settingsWindow.getByRole("tablist", { name: "Settings navigation" });
    await expect(settingsNav).toContainText("General");
    await expect(settingsNav).toContainText("Pet Behavior");
    await expect(settingsNav).toContainText("Model");

    await settingsWindow.getByLabel("Language").selectOption("zh");
    await expect(consoleWindow.getByLabel("Agent status")).toContainText("空闲中");
    await settingsWindow.getByLabel("语言").selectOption("en");
    await expect(consoleWindow.getByLabel("Agent status")).toContainText("idle");

    await settingsWindow.getByRole("tab", { name: "Pet Behavior" }).click();
    await settingsWindow.getByLabel("Click through character").check();
    await expect(petWindow.getByLabel("Desktop Live2D pet")).toHaveAttribute("data-click-through", "true");
    await petWindow.getByRole("button", { name: "Drag pet" }).hover();
    await expect(petWindow.getByLabel("Pet quick controls")).toHaveAttribute("data-visible", "true");
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("opens the product settings window directly from pet quick controls", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const settingsWindow = await openSettingsFromPet(electronApp, petWindow);

    await expect(settingsWindow.getByRole("main", { name: "Settings" })).toBeVisible();
    const settingsNav = settingsWindow.getByRole("tablist", { name: "Settings navigation" });
    await expect(settingsNav).toContainText("Persona");
    await expect(settingsNav).toContainText("Live2D Model");
    await expect(settingsNav).toContainText("Notifications");

    await settingsWindow.getByRole("tab", { name: "Model", exact: true }).click();
    await expect(settingsWindow.getByLabel("Model settings")).toContainText("Chat");
    await expect(settingsWindow.getByLabel("Task CLI settings")).toContainText("Codex");
    await expect(settingsWindow.getByLabel("Task CLI settings")).toContainText("Claude Code");
    await expect(settingsWindow.getByLabel("Task CLI settings")).toContainText("Hermes");
    await settingsWindow.getByLabel("Gate provider").selectOption("langchain-openai");
    await expect(settingsWindow.getByLabel("Gate provider")).toHaveValue("langchain-openai");
    await expect
      .poll(() => {
        const config = JSON.parse(readFileSync(join(userDataDir, "config.json"), "utf8")) as {
          gateModel?: {
            provider?: string;
            enabled?: boolean;
          };
        };

        return config.gateModel;
      })
      .toMatchObject({
        provider: "langchain-openai",
        enabled: true
      });
    await settingsWindow.getByLabel("Enable Claude Code task runner").check();
    await expect(settingsWindow.getByLabel("Enable Claude Code task runner")).toBeChecked();

    await settingsWindow.getByRole("tab", { name: "Persona", exact: true }).click();
    await expect(settingsWindow.getByRole("region", { name: "Persona" })).toContainText("Name");
    await settingsWindow.getByLabel("Open advanced persona editing").click();
    await expect(settingsWindow.getByRole("region", { name: "Persona" })).toContainText("Principles");

    await settingsWindow.getByRole("tab", { name: "Live2D Model", exact: true }).click();
    await expect(settingsWindow.getByRole("region", { name: "Live2D Model" })).toContainText("Current model");
    await expect(settingsWindow.getByRole("region", { name: "Live2D Model" })).toContainText("Hiyori");

    await settingsWindow.getByRole("tab", { name: "Notifications", exact: true }).click();
    await expect(settingsWindow.getByRole("region", { name: "Notifications" })).toContainText("System notifications");

    const settingsPage = settingsWindow.getByRole("main", { name: "Settings" });
    await expect
      .poll(() =>
        settingsPage.evaluate((element) => ({
          clientHeight: element.clientHeight,
          scrollHeight: element.scrollHeight
        }))
      )
      .toMatchObject({
        clientHeight: expect.any(Number),
        scrollHeight: expect.any(Number)
      });
    const scrollMetrics = await settingsPage.evaluate((element) => ({
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight
    }));
    expect(scrollMetrics.scrollHeight).toBeGreaterThan(scrollMetrics.clientHeight);

    await settingsPage.hover();
    await settingsWindow.mouse.wheel(0, 500);
    await expect.poll(() => settingsPage.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("persists the dragged pet window position across launches", async () => {
  const userDataDir = mkdtempSync(join(tmpdir(), "aurabot-e2e-position-"));
  let launched = await launchAuraBot(userDataDir);

  try {
    const initialBounds = await getPetWindowBounds(launched.electronApp);
    await launched.petWindow.evaluate(() => window.aurabot.movePetWindowBy({ x: -48, y: -36 }));
    const movedBounds = await getPetWindowBounds(launched.electronApp);

    expect(movedBounds.x).toBe(initialBounds.x - 48);
    expect(movedBounds.y).toBe(initialBounds.y - 36);

    await launched.electronApp.close();

    launched = await launchAuraBot(userDataDir);
    const restoredBounds = await getPetWindowBounds(launched.electronApp);

    expect(restoredBounds.x).toBe(movedBounds.x);
    expect(restoredBounds.y).toBe(movedBounds.y);
  } finally {
    await cleanupAuraBot(launched.electronApp, userDataDir);
  }
});

test("persists a chat message and shows a memory-aware acknowledgement in the console", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);
    await consoleWindow.getByPlaceholder("Say something").fill("我喜欢吃火锅。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();

    await expect(consoleWindow.locator(".message-user").filter({ hasText: "我喜欢吃火锅。" })).toBeVisible();
    await expect(consoleWindow.getByText(/我记住了.*火锅/)).toBeVisible();
    await expect(petWindow.getByLabel("Pet message")).toContainText(/火锅/);
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("shows a memory debug panel with nodes and recent memory events", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);

    await consoleWindow.getByPlaceholder("Say something").fill("我喜欢吃火锅。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.getByText(/我记住了.*火锅/)).toBeVisible();

    await consoleWindow.getByRole("button", { name: "Memory Debug" }).click();

    await expect(consoleWindow.getByLabel("Memory debug panel")).toBeVisible();
    await expect(consoleWindow.getByText("Nodes 1")).toBeVisible();
    await expect(consoleWindow.getByText("用户喜欢火锅")).toBeVisible();
    await expect(consoleWindow.getByText("memory.write_completed")).toBeVisible();
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("sends console input with Enter and inserts line breaks with Ctrl+Enter", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);
    const input = consoleWindow.getByPlaceholder("Say something");

    await input.fill("我喜欢吃火锅。");
    await input.press("Enter");
    await expect(consoleWindow.locator(".message-user").filter({ hasText: "我喜欢吃火锅。" })).toBeVisible();
    await expect(input).toHaveValue("");

    await input.fill("第一行");
    await input.press("Control+Enter");
    await input.type("第二行");
    await expect(input).toHaveValue("第一行\n第二行");
    await expect(consoleWindow.locator(".message-user").filter({ hasText: /第一行\s*第二行/ })).toHaveCount(0);

    await input.press("Enter");
    await expect(consoleWindow.locator(".message-user").filter({ hasText: /第一行\s*第二行/ })).toBeVisible();
    await expect(input).toHaveValue("");
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("waits on fragmented input and replies after the thought is completed", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);
    const input = consoleWindow.getByPlaceholder("Say something");

    await input.fill("然后我觉得");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.getByLabel("Agent status")).toContainText("listening");
    await expect(consoleWindow.locator(".message-assistant")).toHaveCount(0);

    await input.fill("我喜欢吃火锅。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.getByText(/我记住了.*火锅/)).toBeVisible();
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("keeps long console conversations scrollable and pinned to the newest message", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);
    const input = consoleWindow.getByPlaceholder("Say something");

    for (let index = 1; index <= 14; index += 1) {
      await input.fill(`第 ${index} 条，我喜欢吃火锅。`);
      await consoleWindow.getByRole("button", { name: "Send" }).click();
      await expect(consoleWindow.getByText(/我记住了.*火锅/).last()).toBeVisible();
    }

    const scrollMetrics = await consoleWindow.locator(".message-list").evaluate((element) => ({
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      scrollTop: element.scrollTop,
      windowHeight: window.innerHeight
    }));

    expect(scrollMetrics.clientHeight).toBeLessThan(scrollMetrics.scrollHeight);
    expect(scrollMetrics.clientHeight).toBeLessThan(scrollMetrics.windowHeight);
    expect(scrollMetrics.scrollTop + scrollMetrics.clientHeight).toBeGreaterThanOrEqual(scrollMetrics.scrollHeight - 2);
    await expect(consoleWindow.locator(".message-user").filter({ hasText: "第 14 条" })).toBeVisible();
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("creates a visible queued task from a task-like chat request", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);

    await consoleWindow.getByPlaceholder("Say something").fill("帮我整理一份明天会议的清单。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();

    await expect(consoleWindow.locator(".message-assistant").filter({ hasText: /开始弄/ })).toBeVisible();
    await expect(consoleWindow.getByLabel("Agent status")).toContainText("tasking");
    await expect(petWindow.getByLabel("Desktop Live2D pet")).toHaveAttribute("data-agent-status", "tasking");
    await expect(petWindow.locator(".live2d-canvas-host")).toHaveAttribute("data-motion-group", "TapBody");
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "明天会议" })).toContainText("queued");
    await expect(consoleWindow.locator(".task-panel")).toHaveCount(0);
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("controls a task through the main chat instead of task panel buttons", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);
    const input = consoleWindow.getByPlaceholder("Say something");

    await input.fill("帮我整理一份明天会议的清单。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "明天会议" })).toContainText("queued");

    await input.fill("这个任务做到哪了？");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.locator(".message-assistant").filter({ hasText: /还没开始/ })).toBeVisible();

    await input.fill("先停掉这个任务。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "明天会议" })).toContainText("stopped");

    await input.fill("改成整理后天路演的清单。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "后天路演" })).toContainText("queued");
    await expect(consoleWindow.locator(".task-panel")).toHaveCount(0);
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("uses task status card buttons to cancel and remove settled tasks", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);
    const input = consoleWindow.getByPlaceholder("Say something");

    await input.fill("帮我整理一份明天会议的清单。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    const meetingCard = consoleWindow.locator(".task-status-card").filter({ hasText: "明天会议" });
    await expect(meetingCard).toContainText("queued");

    await meetingCard.getByRole("button", { name: /Cancel task/ }).click();
    await expect(meetingCard).toContainText("stopped");
    await meetingCard.getByRole("button", { name: /Delete task/ }).click();
    await expect(meetingCard).toHaveCount(0);

    await input.fill("帮我写一份 GitHub 周报。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await input.fill("开始做这个任务。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    const reportCard = consoleWindow.locator(".task-status-card").filter({ hasText: "GitHub 周报" });
    await expect(reportCard).toContainText("completed");

    await reportCard.getByRole("button", { name: /Delete task/ }).click();
    await expect(reportCard).toHaveCount(0);
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("targets a specific task through main chat wording when multiple tasks exist", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);
    const input = consoleWindow.getByPlaceholder("Say something");

    await input.fill("帮我整理一份明天会议的清单。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "明天会议" })).toContainText("queued");

    await input.fill("帮我生成一份周报初稿。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "周报" })).toContainText("queued");

    await input.fill("把会议任务改成整理后天路演的清单。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "后天路演" })).toContainText("queued");
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "周报" })).toContainText("queued");

    await input.fill("停掉周报任务。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "周报" })).toContainText("stopped");
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "后天路演" })).toContainText("queued");
    await expect(consoleWindow.locator(".task-panel")).toHaveCount(0);
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("starts a queued task from the main chat and reports completion", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);
    const input = consoleWindow.getByPlaceholder("Say something");

    await input.fill("帮我整理一份明天会议的清单。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "明天会议" })).toContainText("queued");

    await input.fill("开始做这个任务。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.locator(".message-assistant").filter({ hasText: /整理好了/ })).toBeVisible();
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "明天会议" })).toContainText("completed");
    await expect(petWindow.getByLabel("Pet message")).toContainText("弄好了");

    await input.fill("这个任务做到哪了？");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.locator(".message-assistant").filter({ hasText: /整理好了/ }).last()).toBeVisible();
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});
