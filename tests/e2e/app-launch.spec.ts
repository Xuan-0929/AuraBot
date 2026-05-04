import { mkdtempSync, rmSync } from "node:fs";
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
      AURABOT_DISABLE_SYSTEM_CONFIG: "1"
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
  await consoleWindow.getByLabel("AuraBot console window").waitFor({ state: "visible" });
  return consoleWindow;
}

test("launches as a small desktop pet and opens console from the hover control", async () => {
  const { electronApp, petWindow, userDataDir } = await launchAuraBot();

  try {
    await expect(petWindow.getByLabel("Desktop Live2D pet")).toBeVisible();
    await expect(petWindow.getByTestId("live2d-model-surface")).toBeVisible();
    await expect(petWindow.getByLabel("AuraBot console window")).toHaveCount(0);

    const consoleWindow = await openConsole(electronApp, petWindow);
    await expect(consoleWindow.getByLabel("AuraBot console window")).toBeVisible();
    await expect(consoleWindow.getByLabel("Agent status")).toContainText("Idle");
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

    await consoleWindow.getByLabel("Click through character").check();
    await expect(petWindow.getByLabel("Desktop Live2D pet")).toHaveAttribute("data-click-through", "true");
    await petWindow.getByRole("button", { name: "Drag pet" }).hover();
    await expect(petWindow.getByLabel("Pet quick controls")).toHaveAttribute("data-visible", "true");
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
    await expect(consoleWindow.getByLabel("Agent status")).toContainText("Listening");
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

    await expect(consoleWindow.getByText(/我先把这个任务排上/)).toBeVisible();
    await expect(consoleWindow.getByLabel("Agent status")).toContainText("Tasking");
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
    await expect(consoleWindow.getByText(/现在是 queued/)).toBeVisible();

    await input.fill("先停掉这个任务。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "明天会议" })).toContainText("interrupted");

    await input.fill("改成整理后天路演的清单。");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "后天路演" })).toContainText("queued");
    await expect(consoleWindow.locator(".task-panel")).toHaveCount(0);
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
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "周报" })).toContainText("interrupted");
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
    await expect(consoleWindow.locator(".message-assistant").filter({ hasText: /这个任务我跑完了/ })).toBeVisible();
    await expect(consoleWindow.locator(".task-status-card").filter({ hasText: "明天会议" })).toContainText("completed");
    await expect(petWindow.getByLabel("Pet message")).toContainText("任务完成");

    await input.fill("这个任务做到哪了？");
    await consoleWindow.getByRole("button", { name: "Send" }).click();
    await expect(consoleWindow.getByText(/现在是 completed/)).toBeVisible();
  } finally {
    await cleanupAuraBot(electronApp, userDataDir);
  }
});
