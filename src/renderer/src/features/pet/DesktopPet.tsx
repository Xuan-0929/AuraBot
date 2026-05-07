import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type PointerEvent } from "react";
import type { AgentStatus } from "../../../../shared/contracts/chat";
import type { PetCursorPosition } from "../../../../shared/contracts/ipc";
import type { PetActivity } from "../../../../shared/contracts/pet";
import type { PetSettings } from "../../../../shared/schemas/petSettings";
import { t } from "../../i18n";
import { Live2DModelCanvas } from "../live2d/Live2DModelCanvas";
import { extractSpeechTranscript, type SpeechRecognitionEventLike } from "./speechRecognition";

export interface DesktopPetProps {
  agentStatus: AgentStatus;
  modelPath?: string;
  petSettings: PetSettings;
  petActivity?: PetActivity;
  petCursorPosition?: PetCursorPosition;
  debugPetOverlay: boolean;
  onOpenConsole(): Promise<void>;
  onOpenSettings(): Promise<void>;
  onSendVoiceInput(content: string): Promise<void>;
  onSendTextInput(content: string): Promise<void>;
  onSetMousePassthrough(enabled: boolean): Promise<void>;
  onMovePetWindowBy(delta: { x: number; y: number }): Promise<void>;
  onShowPetNotice(message: string): void;
  onClearPetActivity(id?: string): void;
}

const DEFAULT_MODEL_PATH = "assets/live2d/hiyori/Hiyori.model3.json";
const PET_ACTIVITY_TIMEOUT_MS = 7000;
const HOVER_CHAT_WIDTH_PX = 224;
const HOVER_CHAT_GAP_PX = 12;
const HOVER_EXIT_GRACE_MS = 180;

type VoiceState = "idle" | "recording";
type HoverChatSide = "left" | "right";

interface SpeechRecognitionLike {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  start(): void;
  stop(): void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  }
}

export function DesktopPet({
  agentStatus,
  modelPath,
  petSettings,
  petActivity,
  petCursorPosition,
  debugPetOverlay,
  onOpenConsole,
  onOpenSettings,
  onSendVoiceInput,
  onSendTextInput,
  onSetMousePassthrough,
  onMovePetWindowBy,
  onShowPetNotice,
  onClearPetActivity
}: DesktopPetProps) {
  const [isModelHovering, setIsModelHovering] = useState(false);
  const [isControlsHovering, setIsControlsHovering] = useState(false);
  const [isHoverChatHovering, setIsHoverChatHovering] = useState(false);
  const [isHoverChatFocused, setIsHoverChatFocused] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [hoverChatSide, setHoverChatSide] = useState<HoverChatSide>("left");
  const [hoverChatContent, setHoverChatContent] = useState("");
  const [isHoverChatExpanded, setIsHoverChatExpanded] = useState(false);
  const [isHoverChatSubmitting, setIsHoverChatSubmitting] = useState(false);
  const modelButtonRef = useRef<HTMLButtonElement>(null);
  const hoverChatTextareaRef = useRef<HTMLTextAreaElement>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | undefined>(undefined);
  const hoverExitTimeoutRef = useRef<number | undefined>(undefined);
  const mousePassthroughRef = useRef<boolean | undefined>(undefined);
  const lastDragPositionRef = useRef<{ x: number; y: number } | undefined>(undefined);
  const isHoverUiVisible =
    isModelHovering || isControlsHovering || isHoverChatHovering || isHoverChatFocused || isDragging;

  useEffect(() => {
    if (!petActivity) {
      return;
    }

    const timeout = window.setTimeout(() => {
      onClearPetActivity(petActivity.id);
    }, PET_ACTIVITY_TIMEOUT_MS);

    return () => window.clearTimeout(timeout);
  }, [onClearPetActivity, petActivity]);

  useEffect(() => {
    mousePassthroughRef.current = undefined;
  }, [petSettings.clickThroughPet]);

  useEffect(() => {
    if (!isHoverChatExpanded) {
      return;
    }

    const frameId = window.requestAnimationFrame(() => {
      hoverChatTextareaRef.current?.focus();
    });

    return () => window.cancelAnimationFrame(frameId);
  }, [isHoverChatExpanded]);

  useEffect(() => {
    return () => {
      if (hoverExitTimeoutRef.current) {
        window.clearTimeout(hoverExitTimeoutRef.current);
      }
    };
  }, []);

  function setHoverPassthrough(enabled: boolean) {
    if (mousePassthroughRef.current === enabled) {
      return;
    }

    mousePassthroughRef.current = enabled;
    void onSetMousePassthrough(enabled);
  }

  function handlePetPointerMove(event: PointerEvent<HTMLElement>) {
    if (!petSettings.clickThroughPet) {
      return;
    }

    const target = event.target instanceof Element ? event.target : undefined;
    const isOverInteractivePetUi = Boolean(target?.closest(".pet-quick-controls, .pet-hover-chat"));

    setHoverPassthrough(!isOverInteractivePetUi);
  }

  function handleVoiceClick() {
    if (voiceState === "recording") {
      recognitionRef.current?.stop();
      setVoiceState("idle");
      return;
    }

    const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition;

    if (!Recognition) {
      onShowPetNotice("当前环境暂不支持语音识别。");
      return;
    }

    const recognition = new Recognition();
    recognitionRef.current = recognition;
    recognition.lang = navigator.language?.startsWith("zh") ? navigator.language : "zh-CN";
    recognition.interimResults = false;
    recognition.continuous = false;
    recognition.onstart = () => setVoiceState("recording");
    recognition.onend = () => setVoiceState("idle");
    recognition.onerror = () => {
      setVoiceState("idle");
      onShowPetNotice("这次没有听清。");
    };
    recognition.onresult = (event) => {
      const transcript = extractSpeechTranscript(event);

      if (transcript) {
        void onSendVoiceInput(transcript);
      } else {
        onShowPetNotice("这次没有听清。");
      }
    };

    try {
      recognition.start();
    } catch {
      onShowPetNotice("语音录制没有启动成功。");
    }
  }

  function handleControlsPointerEnter() {
    clearHoverExitTimeout();
    setIsControlsHovering(true);

    if (petSettings.clickThroughPet) {
      setHoverPassthrough(false);
    }
  }

  function handleControlsPointerLeave() {
    setIsControlsHovering(false);

    if (petSettings.clickThroughPet && !isDragging) {
      setHoverPassthrough(true);
    }
  }

  function handleModelPointerEnter() {
    clearHoverExitTimeout();
    updateHoverChatSide();
    setIsModelHovering(true);
  }

  function handleModelPointerLeave() {
    clearHoverExitTimeout();
    hoverExitTimeoutRef.current = window.setTimeout(() => {
      setIsModelHovering(false);
      hoverExitTimeoutRef.current = undefined;
    }, HOVER_EXIT_GRACE_MS);
  }

  function clearHoverExitTimeout() {
    if (!hoverExitTimeoutRef.current) {
      return;
    }

    window.clearTimeout(hoverExitTimeoutRef.current);
    hoverExitTimeoutRef.current = undefined;
  }

  function updateHoverChatSide() {
    const modelRect = modelButtonRef.current?.getBoundingClientRect();

    if (!modelRect) {
      setHoverChatSide("left");
      return;
    }

    const requiredLeftWidth = HOVER_CHAT_WIDTH_PX + HOVER_CHAT_GAP_PX;

    setHoverChatSide(modelRect.left >= requiredLeftWidth ? "left" : "right");
  }

  async function sendHoverChatContent(nextContent: string) {
    const trimmed = nextContent.trim();

    if (!trimmed || isHoverChatSubmitting) {
      return;
    }

    setIsHoverChatSubmitting(true);
    setHoverChatContent("");

    try {
      await onSendTextInput(trimmed);
      setIsHoverChatExpanded(false);
    } catch (error) {
      setHoverChatContent((currentContent) => currentContent || nextContent);
      throw error;
    } finally {
      setIsHoverChatSubmitting(false);
    }
  }

  function handleHoverChatSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void sendHoverChatContent(hoverChatContent);
  }

  function handleHoverChatKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.nativeEvent.isComposing) {
      return;
    }

    if (event.ctrlKey) {
      return;
    }

    event.preventDefault();
    void sendHoverChatContent(event.currentTarget.value);
  }

  function focusHoverChatInput() {
    hoverChatTextareaRef.current?.focus();
  }

  function handleDragPointerDown(event: PointerEvent<HTMLButtonElement>) {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    lastDragPositionRef.current = { x: event.screenX, y: event.screenY };
    setIsDragging(true);
    setIsControlsHovering(true);

    if (petSettings.clickThroughPet) {
      setHoverPassthrough(false);
    }
  }

  function handleDragPointerMove(event: PointerEvent<HTMLButtonElement>) {
    if (!isDragging || !lastDragPositionRef.current) {
      return;
    }

    const nextPosition = { x: event.screenX, y: event.screenY };
    const delta = {
      x: nextPosition.x - lastDragPositionRef.current.x,
      y: nextPosition.y - lastDragPositionRef.current.y
    };

    lastDragPositionRef.current = nextPosition;

    if (delta.x !== 0 || delta.y !== 0) {
      void onMovePetWindowBy(delta);
    }
  }

  function handleDragPointerEnd(event: PointerEvent<HTMLButtonElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }

    lastDragPositionRef.current = undefined;
    setIsDragging(false);
  }

  return (
    <main
      className="desktop-pet"
      aria-label={t(petSettings.displayLanguage, "desktopLive2DPet")}
      data-agent-status={agentStatus}
      data-click-through={petSettings.clickThroughPet ? "true" : "false"}
      onPointerMove={handlePetPointerMove}
    >
      {petActivity ? (
        <aside className="pet-message-bubble" aria-label={t(petSettings.displayLanguage, "petMessage")}>
          {petActivity.message}
        </aside>
      ) : null}
      <button
        className="desktop-pet-model-button"
        ref={modelButtonRef}
        type="button"
        aria-label={t(petSettings.displayLanguage, "interactWithAuraBot")}
        onClick={() => void onOpenConsole()}
      >
        <span
          className="desktop-pet-model-shell"
          data-testid="live2d-model-surface"
          onPointerEnter={handleModelPointerEnter}
          onPointerLeave={handleModelPointerLeave}
        >
          <Live2DModelCanvas
            modelPath={modelPath ?? DEFAULT_MODEL_PATH}
            agentStatus={agentStatus}
            compact
            debug={debugPetOverlay}
            cursorPosition={petCursorPosition}
          />
        </span>
      </button>
      {isHoverUiVisible ? (
        <form
          className="pet-hover-chat"
          aria-label={t(petSettings.displayLanguage, "petQuickChat")}
          data-side={hoverChatSide}
          data-expanded={isHoverChatExpanded ? "true" : "false"}
          onClick={focusHoverChatInput}
          onSubmit={handleHoverChatSubmit}
          onPointerEnter={() => {
            clearHoverExitTimeout();
            setIsHoverChatHovering(true);
          }}
          onPointerLeave={() => setIsHoverChatHovering(false)}
        >
          {isHoverChatExpanded ? (
            <>
              <textarea
                ref={hoverChatTextareaRef}
                value={hoverChatContent}
                onChange={(event) => setHoverChatContent(event.target.value)}
                onFocus={() => setIsHoverChatFocused(true)}
                onBlur={() => setIsHoverChatFocused(false)}
                onKeyDown={handleHoverChatKeyDown}
                placeholder={t(petSettings.displayLanguage, "petChatPlaceholder")}
                rows={2}
              />
              <button
                className="pet-hover-chat-send"
                type="submit"
                disabled={isHoverChatSubmitting || !hoverChatContent.trim()}
                aria-label={t(petSettings.displayLanguage, "sendPetChat")}
              >
                {t(petSettings.displayLanguage, "send")}
              </button>
            </>
          ) : (
            <button
              className="pet-hover-chat-trigger"
              type="button"
              aria-label={t(petSettings.displayLanguage, "openPetQuickChat")}
              onClick={(event) => {
                event.stopPropagation();
                setIsHoverChatExpanded(true);
              }}
            >
              {t(petSettings.displayLanguage, "chat")}
            </button>
          )}
        </form>
      ) : null}
      <div
        className="pet-quick-controls"
        aria-label={t(petSettings.displayLanguage, "petQuickControls")}
        data-visible={isHoverUiVisible ? "true" : "false"}
        onPointerEnter={handleControlsPointerEnter}
        onPointerLeave={handleControlsPointerLeave}
      >
        <button
          className="pet-action-button pet-action-drag"
          type="button"
          aria-label={t(petSettings.displayLanguage, "dragPet")}
          data-dragging={isDragging ? "true" : "false"}
          onPointerDown={handleDragPointerDown}
          onPointerMove={handleDragPointerMove}
          onPointerUp={handleDragPointerEnd}
          onPointerCancel={handleDragPointerEnd}
        >
          {t(petSettings.displayLanguage, "drag")}
        </button>
        <button
          className="pet-action-button"
          type="button"
          aria-label={
            voiceState === "recording"
              ? t(petSettings.displayLanguage, "stopVoiceRecording")
              : t(petSettings.displayLanguage, "startVoiceInput")
          }
          data-active={voiceState === "recording" ? "true" : "false"}
          onClick={handleVoiceClick}
        >
          {t(petSettings.displayLanguage, "voice")}
        </button>
        <button
          className="pet-action-button"
          type="button"
          aria-label={t(petSettings.displayLanguage, "openAuraBotConsole")}
          onClick={() => void onOpenConsole()}
        >
          {t(petSettings.displayLanguage, "console")}
        </button>
        <button
          className="pet-action-button"
          type="button"
          aria-label={t(petSettings.displayLanguage, "openAuraBotSettings")}
          onClick={() => void onOpenSettings()}
        >
          {t(petSettings.displayLanguage, "settings")}
        </button>
      </div>
    </main>
  );
}
