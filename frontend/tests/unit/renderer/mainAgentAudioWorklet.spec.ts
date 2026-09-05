/**
 * Tests the product AudioWorklet's pure resampling, local speech gate, and clause playback queue. These
 * behaviors protect immediate barge-in and physical ACK while leaving browser plumbing to the client test.
 */

import { describe, expect, it } from "vitest";

// @ts-expect-error AudioWorklet code exports pure helpers only for deterministic unit checks.
import { ClausePlaybackQueue, ContinuousPcm16Resampler, LocalSpeechGate } from "@renderer/main-agent-realtime/audio-worklet.js";

describe("main-agent audio worklet", () => {
  it("resamples continuously and emits exact 20ms frames", () => {
    const resampler = new ContinuousPcm16Resampler(48_000, 16_000);
    expect(resampler.push(new Float32Array(960).fill(0.25))).toHaveLength(320);
  });

  it("pauses in one loud frame and releases after 200ms of quiet", () => {
    const gate = new LocalSpeechGate();
    expect(gate.accept(new Int16Array(320).fill(Math.round(0.03 * 32767)))).toBe("candidate");
    for (let frame = 0; frame < 9; frame += 1) expect(gate.accept(new Int16Array(320))).toBeNull();
    expect(gate.accept(new Int16Array(320))).toBe("released");
  });

  it("keeps paused PCM and completes only after audio_end data is consumed", () => {
    const queue = new ClausePlaybackQueue({ outputSampleRate: 24_000, maxQueuedSamples: 32 });
    queue.offer("clause-1");
    queue.append(new Int16Array([100, 200, 300, 400]));
    queue.finish("clause-1");
    queue.pause();
    expect(queue.render(new Float32Array(8))).toEqual([]);
    expect(queue.queuedSamples).toBe(4);
    queue.resume();
    expect(queue.render(new Float32Array(3))).toEqual([]);
    expect(queue.render(new Float32Array(1))).toEqual([{ type: "clause-complete", clauseId: "clause-1", frameOffset:1 }]);
    expect(queue.render(new Float32Array(8))).toEqual([]);
  });
  it("accepts a second clause and completes when audio_end arrives after PCM drained", () => {
    const queue = new ClausePlaybackQueue({ outputSampleRate: 24_000 });
    queue.offer("first");queue.append(new Int16Array([100]));queue.render(new Float32Array(8));
    queue.finish("first");
    expect(queue.render(new Float32Array(8))).toEqual([expect.objectContaining({type:"clause-complete",clauseId:"first"})]);
    expect(()=>queue.offer("second")).not.toThrow();
    queue.append(new Int16Array([200]));queue.finish("second");
    expect(queue.render(new Float32Array(8))).toEqual([expect.objectContaining({type:"clause-complete",clauseId:"second"})]);
  });
  it("interpolates using fractional source position independently of the buffer offset", () => {
    const queue = new ClausePlaybackQueue({ outputSampleRate: 48_000 });
    queue.offer("ramp");queue.append(new Int16Array([0, 1000, 2000, 3000]));
    const channel=new Float32Array(6);queue.render(channel);
    expect(Array.from(channel).map(value=>Math.round(value*32768))).toEqual([0,500,1000,1500,2000,2500]);
  });

});
