// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Live2DModelSettingsPage } from "../../../src/renderer/src/features/settings/pages/Live2DModelSettingsPage";
import type { Live2DModelState } from "../../../src/shared/contracts/live2dModel";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const state: Live2DModelState = {
  currentModelId: "hiyori",
  currentModelPath: "assets/live2d/hiyori/Hiyori.model3.json",
  models: [
    {
      id: "hiyori",
      displayName: "Hiyori",
      folderName: "hiyori",
      manifestFileName: "Hiyori.model3.json",
      manifestPath: "assets/live2d/hiyori/Hiyori.model3.json"
    },
    {
      id: "beta",
      displayName: "Beta",
      folderName: "beta",
      manifestFileName: "Beta.model3.json",
      manifestPath: "assets/live2d/beta/Beta.model3.json"
    }
  ]
};

describe("Live2DModelSettingsPage", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  it("shows the current model and the locally switchable model list", async () => {
    await act(async () => {
      root.render(
        <Live2DModelSettingsPage
          petSettings={{ clickThroughPet: false, displayLanguage: "en" }}
          live2dModelState={state}
          onSelectModel={vi.fn(async () => undefined)}
        />
      );
    });

    expect(container.textContent).toContain("Live2D Model");
    expect(container.textContent).toContain("Hiyori");
    expect(container.textContent).toContain("Beta");
    expect(container.textContent).toContain("Current model");
    expect(container.textContent).toContain("Local models");
  });

  it("switches the active model from the settings page", async () => {
    const onSelectModel = vi.fn(async () => undefined);

    await act(async () => {
      root.render(
        <Live2DModelSettingsPage
          petSettings={{ clickThroughPet: false, displayLanguage: "en" }}
          live2dModelState={state}
          onSelectModel={onSelectModel}
        />
      );
    });

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[aria-label="Switch to Beta model"]')!.click();
    });

    expect(onSelectModel).toHaveBeenCalledWith("beta");
  });
});
