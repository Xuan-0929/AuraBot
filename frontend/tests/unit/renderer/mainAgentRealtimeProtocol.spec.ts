/**
 * Verifies the browser lab's AUR1 bytes, rational resampling, connection/playback single-flight ownership,
 * TTS discontinuity rules, and deterministic fault injection independently from Python. Browser permission
 * prompts remain an integration boundary; the actual AudioWorklet resampler is imported into this suite.
 */

import { describe, expect, it } from "vitest";

import {
  AUR1_HEADER_BYTES,
  ConnectionScope,
  FaultController,
  SharedInitializer,
  TtsFrameTracker,
  encodeMicFrame,
  parseTtsFrame,
} from "@renderer/realtime-test-client/protocol";
// @ts-expect-error The raw AudioWorklet module intentionally has no production TypeScript dependency.
import { ContinuousPcm16Resampler, LocalSpeechGate } from "@renderer/realtime-test-client/mic-worklet.js";
// @ts-expect-error The raw AudioWorklet module exports a pure queue for deterministic tests.
import { ClausePlaybackQueue } from "@renderer/realtime-test-client/playback-worklet.js";
import { PlaybackGate } from "@renderer/main-agent-realtime/client";

describe("main-agent realtime browser protocol", () => {
  it("writes the exact 24-byte AUR1 little-endian header", () => {
    const payload = new Int16Array(320);
    const encoded = encodeMicFrame(payload, {
      streamId: 7,
      frameSequence: 4,
      sampleStart: 1280n,
      discontinuity: false,
    });
    const view = new DataView(encoded);

    expect(AUR1_HEADER_BYTES).toBe(24);
    expect(new TextDecoder().decode(encoded.slice(0, 4))).toBe("AUR1");
    expect(view.getUint8(4)).toBe(1);
    expect(view.getUint32(8, true)).toBe(7);
    expect(view.getUint32(12, true)).toBe(4);
    expect(view.getBigUint64(16, true)).toBe(1280n);
    expect(encoded.byteLength).toBe(664);
  });

  it("accepts only exact 24 kHz mono 20 ms TTS frames", () => {
    const bytes = new Uint8Array(24 + 960);
    bytes.set(new TextEncoder().encode("AUR1"));
    const view = new DataView(bytes.buffer);
    view.setUint8(4, 2);

    expect(parseTtsFrame(bytes.buffer).samples).toHaveLength(480);
    view.setUint8(4, 1);
    expect(() => parseTtsFrame(bytes.buffer)).toThrow(/TTS/);
  });

  it("injects one duplicate, one unflagged gap, and one bad epoch deterministically", () => {
    const faults = new FaultController();
    faults.arm("duplicate");
    expect(faults.nextFrameSequence(5)).toEqual([5, 5]);
    faults.arm("gap");
    expect(faults.nextFrameSequence(6)).toEqual([8]);
    faults.arm("badEpoch");
    expect(faults.rendererEpoch(9)).toBe(8);
    expect(faults.rendererEpoch(9)).toBe(9);
  });

  it("resamples continuously across arbitrary chunks with exact rational long-run counts", () => {
    for (const [sourceRate, expected] of [[48_000, 16_000], [44_100, 16_000]] as const) {
      const resampler = new ContinuousPcm16Resampler(sourceRate, 16_000);
      let output = 0;
      let offset = 0;
      const input = new Float32Array(sourceRate).fill(0.25);
      const chunkSizes = [1, 127, 128, 17, 509, 3, 256];
      let chunk = 0;
      while (offset < input.length) {
        const end = Math.min(input.length, offset + chunkSizes[chunk % chunkSizes.length]);
        output += resampler.push(input.subarray(offset, end)).length;
        offset = end;
        chunk += 1;
      }
      expect(output).toBe(expected);
    }
  });

  it("pauses within one loud 20ms frame and releases only after 200ms of quiet", () => {
    const gate = new LocalSpeechGate();
    const loud = new Int16Array(320).fill(Math.round(0.03 * 32767));
    const quiet = new Int16Array(320);

    expect(gate.accept(loud)).toBe("candidate");
    for (let frame = 0; frame < 9; frame += 1) {
      expect(gate.accept(quiet)).toBeNull();
    }
    expect(gate.accept(quiet)).toBe("released");
  });

  it("keeps paused PCM and completes a clause once its final source sample is consumed", () => {
    const queue = new ClausePlaybackQueue({ outputSampleRate: 24_000, maxQueuedSamples: 32 });
    expect(queue.append({
      clauseId: "clause-1",
      samples: new Int16Array([100, 200, 300, 400]),
      final: true,
    })).toEqual([]);

    queue.pause();
    expect(queue.render(new Float32Array(8))).toEqual([]);
    expect(queue.queuedSamples).toBe(4);
    queue.resume();
    expect(queue.render(new Float32Array(3))).toEqual([]);
    expect(queue.render(new Float32Array(1))).toEqual([
      { type: "clause-complete", clauseId: "clause-1" },
    ]);
    expect(queue.render(new Float32Array(8))).toEqual([]);
  });

  it("resumes after a local false positive only when the server gate is also open", () => {
    const gate = new PlaybackGate();
    expect(gate.setLocalCandidate(true)).toBe("pause");
    expect(gate.setServerBlocked(true)).toBeNull();
    expect(gate.setLocalCandidate(false)).toBeNull();
    expect(gate.setServerBlocked(false)).toBe("resume");
  });

  it("shares playback initialization and scopes callbacks to only the current socket", async () => {
    const initializer = new SharedInitializer<object>();
    let creations = 0;
    const factory = async () => {
      creations += 1;
      await Promise.resolve();
      return {};
    };
    const [first, second] = await Promise.all([initializer.get(factory), initializer.get(factory)]);
    expect(first).toBe(second);
    expect(creations).toBe(1);

    const scope = new ConnectionScope<object>();
    const oldSocket = {};
    const newSocket = {};
    scope.install(oldSocket);
    scope.install(newSocket);
    expect(scope.isCurrent(oldSocket)).toBe(false);
    expect(scope.clear(oldSocket)).toBe(false);
    expect(scope.isCurrent(newSocket)).toBe(true);
  });

  it("requires a zero-origin unflagged TTS stream, drops duplicates, and resets on discontinuity", () => {
    const tracker = new TtsFrameTracker();
    const frame = (streamId: number, frameSequence: number, sampleStart: bigint, discontinuity = false, final = false) => ({
      streamId, frameSequence, sampleStart, discontinuity, final, samples: new Int16Array(480),
    });

    expect(() => tracker.accept(frame(1, 2, 960n))).toThrow(/zero/);
    expect(tracker.accept(frame(1, 0, 0n))).toBe("accepted");
    expect(tracker.accept(frame(1, 0, 0n))).toBe("duplicate");
    expect(() => tracker.accept(frame(1, 2, 960n))).toThrow(/gap/);
    expect(tracker.accept(frame(1, 8, 3_840n, true))).toBe("discontinuity");
    const final = frame(1, 9, 4_320n, false, true);
    expect(tracker.accept(final)).toBe("accepted");
    expect(tracker.accept(final)).toBe("duplicate");
    expect(() => tracker.accept(frame(1, 0, 0n))).toThrow(/reused/);
    expect(tracker.accept(frame(2, 0, 0n))).toBe("accepted");
    tracker.cancelActive();
    expect(() => tracker.accept(frame(2, 0, 0n))).toThrow(/reused/);
  });

  it("invalidates an in-flight playback continuation as soon as replacement arrives", async () => {
    const scope = new ConnectionScope<object>();
    const socket = {};
    scope.install(socket);
    let resume: (() => void) | undefined;
    const initialization = new Promise<void>((resolve) => { resume = resolve; });
    const continued = (async () => {
      await initialization;
      return scope.isCurrent(socket);
    })();

    expect(scope.clear(socket)).toBe(true);
    resume?.();
    await expect(continued).resolves.toBe(false);
  });
});
