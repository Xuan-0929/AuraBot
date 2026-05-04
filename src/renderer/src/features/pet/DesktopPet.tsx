import { useEffect, useRef, useState, type PointerEvent } from "react";
import type { AgentStatus } from "../../../../shared/contracts/chat";
import type { PetActivity } from "../../../../shared/contracts/pet";
import type { PetSettings } from "../../../../shared/schemas/petSettings";
import { Live2DModelCanvas } from "../live2d/Live2DModelCanvas";
import { extractSpeechTranscript, type SpeechRecognitionEventLike } from "./speechRecognition";

export interface DesktopPetProps {
  agentStatus: AgentStatus;
  petSettings: PetSettings;
  petActivity?: PetActivity;
  debugPetOverlay: boolean;
  onOpenConsole(): Promise<void>;
  onSendVoiceInput(content: string): Promise<void>;
  onSetMousePassthrough(enabled: boolean): Promise<void>;
  onMovePetWindowBy(delta: { x: number; y: number }): Promise<void>;
  onShowPetNotice(message: string): void;
  onClearPetActivity(id?: string): void;
}

const MODEL_PATH = "assets/live2d/hiyori/Hiyori.model3.json";
const PET_ACTIVITY_TIMEOUT_MS = 7000;

type VoiceState = "idle" | "recording";

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
  petSettings,
  petActivity,
  debugPetOverlay,
  onOpenConsole,
  onSendVoiceInput,
  onSetMousePassthrough,
  onMovePetWindowBy,
  onShowPetNotice,
  onClearPetActivity
}: DesktopPetProps) {
  const [isModelHovering, setIsModelHovering] = useState(false);
  const [isControlsHovering, setIsControlsHovering] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const recognitionRef = useRef<SpeechRecognitionLike | undefined>(undefined);
  const mousePassthroughRef = useRef<boolean | undefined>(undefined);
  const lastDragPositionRef = useRef<{ x: number; y: number } | undefined>(undefined);
  const areQuickControlsVisible = isModelHovering || isControlsHovering || isDragging;

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
    const isOverQuickControls = Boolean(target?.closest(".pet-quick-controls"));

    setHoverPassthrough(!isOverQuickControls);
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
      aria-label="Desktop Live2D pet"
      data-agent-status={agentStatus}
      data-click-through={petSettings.clickThroughPet ? "true" : "false"}
      onPointerMove={handlePetPointerMove}
    >
      {petActivity ? (
        <aside className="pet-message-bubble" aria-label="Pet message">
          {petActivity.message}
        </aside>
      ) : null}
      <button
        className="desktop-pet-model-button"
        type="button"
        aria-label="Interact with AuraBot"
        onClick={() => void onOpenConsole()}
      >
        <span
          className="desktop-pet-model-shell"
          data-testid="live2d-model-surface"
          onPointerEnter={() => setIsModelHovering(true)}
          onPointerLeave={() => setIsModelHovering(false)}
        >
          <Live2DModelCanvas modelPath={MODEL_PATH} agentStatus={agentStatus} compact debug={debugPetOverlay} />
        </span>
      </button>
      <div
        className="pet-quick-controls"
        aria-label="Pet quick controls"
        data-visible={areQuickControlsVisible ? "true" : "false"}
        onPointerEnter={handleControlsPointerEnter}
        onPointerLeave={handleControlsPointerLeave}
      >
        <button
          className="pet-action-button pet-action-drag"
          type="button"
          aria-label="Drag pet"
          data-dragging={isDragging ? "true" : "false"}
          onPointerDown={handleDragPointerDown}
          onPointerMove={handleDragPointerMove}
          onPointerUp={handleDragPointerEnd}
          onPointerCancel={handleDragPointerEnd}
        >
          Drag
        </button>
        <button
          className="pet-action-button"
          type="button"
          aria-label={voiceState === "recording" ? "Stop voice recording" : "Start voice input"}
          data-active={voiceState === "recording" ? "true" : "false"}
          onClick={handleVoiceClick}
        >
          Voice
        </button>
        <button
          className="pet-action-button"
          type="button"
          aria-label="Open AuraBot console"
          onClick={() => void onOpenConsole()}
        >
          Console
        </button>
      </div>
    </main>
  );
}
