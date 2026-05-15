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

async function openSettingsFromConsole(electronApp: ElectronApplication, consoleWindow: Page): Promise<Page> {
  const settingsPromise = electronApp.waitForEvent("window");
  await consoleWindow.getByRole("button", { name: /Open AuraBot settings|打开 AuraBot 设置/ }).click();
  const settingsWindow = await settingsPromise;
  await settingsWindow.getByRole("main", { name: "Settings" }).waitFor({ state: "visible" });
  return settingsWindow;
}

async function openSettingsViaConsole(electronApp: ElectronApplication, petWindow: Page): Promise<Page> {
  const consoleWindow = await openConsole(electronApp, petWindow);
  return openSettingsFromConsole(electronApp, consoleWindow);
}

async function sendConsoleMessage(consoleWindow: Page, content: string): Promise<void> {
  const input = consoleWindow.getByPlaceholder("Say something");
  await input.fill(content);
  await input.press("Enter");
}

function getConversationMessages(consoleWindow: Page) {
  return consoleWindow.getByLabel(/Conversation messages|对话消息/);
}

function getConsoleStickyNotes(consoleWindow: Page) {
  return consoleWindow.getByLabel(/Today's notes|今日便签/);
}

function getConsoleStickyNote(consoleWindow: Page, title: string) {
  return getConsoleStickyNotes(consoleWindow).locator("article").filter({ hasText: title });
}

async function backToSettingsRoot(settingsWindow: Page): Promise<void> {
  for (let step = 0; step < 2; step += 1) {
    const backButton = settingsWindow.getByRole("button", { name: /Back to settings|返回设置/ });
    if ((await backButton.count()) === 0) {
      return;
    }
    await backButton.first().click();
  }
}

async function openSettingsPage(settingsWindow: Page, section: string, page: string): Promise<void> {
  await backToSettingsRoot(settingsWindow);
  await settingsWindow.getByRole("button", { name: section, exact: true }).click();
  await settingsWindow.getByRole("button", { name: page, exact: true }).click();
}

test("launches as a small desktop pet and opens console from the hover control", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    await expect(petWindow.getByLabel("Desktop Live2D pet")).toBeVisible();
    await expect(petWindow.getByTestId("live2d-model-surface")).toBeVisible();

    const consoleWindow = await openConsole(electronApp, petWindow);
    await expect(consoleWindow.getByRole("main", { name: "AuraBot Console" })).toBeVisible();
    await expect(consoleWindow.getByLabel("Agent status")).toContainText("idle");

    const settingsWindow = await openSettingsFromConsole(electronApp, consoleWindow);
    await expect(settingsWindow.getByRole("main", { name: "Settings" })).toBeVisible();
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("keeps pet quick controls available and syncs click-through settings while the console is open", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    await petWindow.mouse.move(4, 4);
    await expect(petWindow.getByLabel("Pet quick controls")).toHaveAttribute("data-visible", "true");
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
    await expect(consoleWindow.getByRole("button", { name: "Open settings menu" })).toHaveCount(0);
    await expect(consoleWindow.getByRole("button", { name: "Open AuraBot settings" })).toBeVisible();

    const settingsWindow = await openSettingsFromConsole(electronApp, consoleWindow);
    const settingsNav = settingsWindow.getByRole("navigation", { name: "Settings navigation" });
    await expect(settingsNav).toContainText("Basics");
    await expect(settingsNav).toContainText("Companion");
    await expect(settingsNav).toContainText("Intelligence");

    await openSettingsPage(settingsWindow, "Basics", "General");
    await settingsWindow.getByLabel("Language").selectOption("zh");
    await expect(consoleWindow.getByLabel("Agent status")).toContainText("空闲中");
    await expect(consoleWindow.getByRole("button", { name: "打开 AuraBot 设置" })).toBeVisible();
    await settingsWindow.getByLabel("语言").selectOption("en");
    await expect(consoleWindow.getByLabel("Agent status")).toContainText("idle");
    await expect(consoleWindow.getByRole("button", { name: "Open AuraBot settings" })).toBeVisible();

    await openSettingsPage(settingsWindow, "Companion", "Pet Behavior");
    await settingsWindow.getByLabel("Click through character").check();
    await expect(petWindow.getByLabel("Desktop Live2D pet")).toHaveAttribute("data-click-through", "true");
    await petWindow.getByRole("button", { name: "Drag pet" }).hover();
    await expect(petWindow.getByLabel("Pet quick controls")).toHaveAttribute("data-visible", "true");
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("opens the product settings window from the console settings button", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const settingsWindow = await openSettingsViaConsole(electronApp, petWindow);

    await expect(settingsWindow.getByRole("main", { name: "Settings" })).toBeVisible();
    const settingsNav = settingsWindow.getByRole("navigation", { name: "Settings navigation" });
    await expect(settingsNav).toContainText("Basics");
    await expect(settingsNav).toContainText("Companion");
    await expect(settingsNav).toContainText("Intelligence");
    await expect(settingsNav).toContainText("Automation");

    await openSettingsPage(settingsWindow, "Intelligence", "Model");
    await expect(settingsWindow.getByLabel("Model settings")).toContainText("Chat");
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
    await openSettingsPage(settingsWindow, "Automation", "Task CLI");
    await expect(settingsWindow.getByLabel("Task CLI settings")).toContainText("Codex");
    await expect(settingsWindow.getByLabel("Task CLI settings")).toContainText("Claude Code");
    await expect(settingsWindow.getByLabel("Task CLI settings")).toContainText("Hermes");
    await settingsWindow.getByLabel("Enable Claude Code task runner").check();
    await expect(settingsWindow.getByLabel("Enable Claude Code task runner")).toBeChecked();
    await settingsWindow.getByLabel("Codex task time limit minutes").fill("12");
    await expect(settingsWindow.getByLabel("Codex task time limit minutes")).toHaveValue("12");
    await expect
      .poll(() => {
        const config = JSON.parse(readFileSync(join(userDataDir, "config.json"), "utf8")) as {
          taskRunner?: {
            runners?: Array<{
              id: string;
              timeoutMs?: number;
            }>;
          };
        };

        return config.taskRunner?.runners?.find((runner) => runner.id === "codex")?.timeoutMs;
      })
      .toBe(720000);

    await openSettingsPage(settingsWindow, "Companion", "Persona");
    await expect(settingsWindow.getByRole("region", { name: "Persona" })).toContainText("Name");
    await settingsWindow.getByLabel("Open advanced persona editing").click();
    await expect(settingsWindow.getByRole("region", { name: "Persona" })).toContainText("Principles");

    await openSettingsPage(settingsWindow, "Companion", "Live2D Model");
    await expect(settingsWindow.getByRole("region", { name: "Live2D Model" })).toContainText("Current model");
    await expect(settingsWindow.getByRole("region", { name: "Live2D Model" })).toContainText("Hiyori");

    await openSettingsPage(settingsWindow, "Basics", "Notifications");
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

test("shows memory debug inside the full settings window instead of the console body", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);

    await consoleWindow.getByPlaceholder("Say something").fill("我喜欢吃火锅。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.getByText(/我记住了.*火锅/)).toBeVisible();

    const settingsWindow = await openSettingsFromConsole(electronApp, consoleWindow);

    await openSettingsPage(settingsWindow, "Intelligence", "Memory Debug");
    await expect(settingsWindow.getByLabel("Memory debug panel")).toBeVisible();
    await expect(settingsWindow.getByText("Nodes 1")).toBeVisible();
    await expect(settingsWindow.getByText("用户喜欢火锅")).toBeVisible();
    await expect(settingsWindow.getByText("memory.write_completed")).toBeVisible();
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

test("keeps long conversations pinned in the conversation message list once chat overflows", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);
    const messageList = getConversationMessages(consoleWindow);

    await sendConsoleMessage(consoleWindow, "第 1 条，我喜欢吃火锅。");
    await expect(consoleWindow.getByText(/我记住了.*火锅/).last()).toBeVisible();

    for (let index = 2; index <= 24; index += 1) {
      await sendConsoleMessage(consoleWindow, `第 ${index} 条，我喜欢吃火锅。`);
      await expect(consoleWindow.getByText(/我记住了.*火锅/).last()).toBeVisible();
    }

    const scrollMetrics = await messageList.evaluate((element) => ({
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      scrollTop: element.scrollTop
    }));

    expect(scrollMetrics.clientHeight).toBeLessThan(scrollMetrics.scrollHeight);
    expect(scrollMetrics.scrollTop).toBeGreaterThan(0);
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
    const conversation = consoleWindow.getByLabel("Main conversation");
    const beforeBox = await conversation.boundingBox();

    await sendConsoleMessage(consoleWindow, "帮我整理一份明天会议的清单。");

    await expect(consoleWindow.locator(".message-assistant").filter({ hasText: /开始弄/ })).toBeVisible();
    await expect(consoleWindow.getByLabel("Agent status")).toContainText("tasking");
    await expect(petWindow.getByLabel("Desktop Live2D pet")).toHaveAttribute("data-agent-status", "tasking");
    await expect(petWindow.locator(".live2d-canvas-host")).toHaveAttribute("data-render-state", "ready");
    await expect(getConsoleStickyNotes(consoleWindow)).toBeVisible();
    await expect(getConsoleStickyNote(consoleWindow, "明天会议")).toContainText("Not started yet");
    const noteBox = await getConsoleStickyNotes(consoleWindow).boundingBox();
    const afterBox = await conversation.boundingBox();

    expect(beforeBox).not.toBeNull();
    expect(afterBox).not.toBeNull();
    expect(noteBox).not.toBeNull();
    expect(afterBox?.x ?? 0).toBeCloseTo(beforeBox?.x ?? 0, 1);
    expect(afterBox?.y ?? 0).toBeCloseTo(beforeBox?.y ?? 0, 1);
    expect(afterBox?.width ?? 0).toBeCloseTo(beforeBox?.width ?? 0, 1);
    expect(afterBox?.height ?? 0).toBeCloseTo(beforeBox?.height ?? 0, 1);
    expect(noteBox?.y ?? 0).toBeLessThan(afterBox?.y ?? 0);
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("controls a task through the main chat instead of task panel buttons", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);
    const meetingNote = getConsoleStickyNote(consoleWindow, "明天会议");

    await sendConsoleMessage(consoleWindow, "帮我整理一份明天会议的清单。");
    await expect(meetingNote).toContainText("Not started yet");

    await sendConsoleMessage(consoleWindow, "这个任务做到哪了？");
    await expect(consoleWindow.locator(".message-assistant").filter({ hasText: /直接跟我说一声/ })).toBeVisible();

    await sendConsoleMessage(consoleWindow, "先停掉这个任务。");
    await expect(meetingNote).toContainText("Stopped");

    await sendConsoleMessage(consoleWindow, "改成整理后天路演的清单。");
    await expect(getConsoleStickyNote(consoleWindow, "后天路演")).toContainText("Not started yet");
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("uses sticky-note actions to cancel active tasks and remove settled ones", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);

    await sendConsoleMessage(consoleWindow, "帮我整理一份明天会议的清单。");
    const meetingNote = getConsoleStickyNote(consoleWindow, "明天会议");
    await expect(meetingNote).toContainText("Not started yet");

    await meetingNote.getByRole("button", { name: /Cancel task/ }).click();
    await expect(meetingNote).toContainText("Stopped");
    await meetingNote.getByRole("button", { name: /Delete task/ }).click();
    await expect(meetingNote).toHaveCount(0);

    await sendConsoleMessage(consoleWindow, "帮我写一份 GitHub 周报。");
    await sendConsoleMessage(consoleWindow, "开始做这个任务。");
    const reportNote = getConsoleStickyNote(consoleWindow, "GitHub 周报");
    await expect(reportNote).toContainText("Result is ready");

    await reportNote.getByRole("button", { name: /Delete task/ }).click();
    await expect(reportNote).toHaveCount(0);
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("targets a specific task through main chat wording when multiple tasks exist", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);
    const reportNote = getConsoleStickyNote(consoleWindow, "周报");

    await sendConsoleMessage(consoleWindow, "帮我整理一份明天会议的清单。");
    await expect(getConsoleStickyNote(consoleWindow, "明天会议")).toContainText("Not started yet");

    await sendConsoleMessage(consoleWindow, "帮我生成一份周报初稿。");
    await expect(reportNote).toContainText("Not started yet");

    await sendConsoleMessage(consoleWindow, "把会议任务改成整理后天路演的清单。");
    await expect(getConsoleStickyNote(consoleWindow, "后天路演")).toContainText("Not started yet");
    await expect(reportNote).toContainText("Not started yet");

    await sendConsoleMessage(consoleWindow, "停掉周报任务。");
    await expect(reportNote).toContainText("Stopped");
    await expect(getConsoleStickyNote(consoleWindow, "后天路演")).toContainText("Not started yet");
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});

test("starts a queued task from the main chat and reports completion", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    const consoleWindow = await openConsole(electronApp, petWindow);
    const meetingNote = getConsoleStickyNote(consoleWindow, "明天会议");

    await sendConsoleMessage(consoleWindow, "帮我整理一份明天会议的清单。");
    await expect(meetingNote).toContainText("Not started yet");

    await sendConsoleMessage(consoleWindow, "开始做这个任务。");
    await expect(consoleWindow.locator(".message-assistant").filter({ hasText: /整理好了/ })).toBeVisible();
    await expect(meetingNote).toContainText("Result is ready");
    await expect(petWindow.getByLabel("Pet message")).toContainText("弄好了");

    await sendConsoleMessage(consoleWindow, "这个任务做到哪了？");
    await expect(consoleWindow.locator(".message-assistant").filter({ hasText: /整理好了/ }).last()).toBeVisible();
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});
