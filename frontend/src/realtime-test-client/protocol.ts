/**
 * Implements the shared browser half of aurabot.main-agent.v1 framing and stream ordering. The formal Pet
 * Renderer and standalone diagnostic client both use these exact AUR1 encoders/validators; connection
 * scoping and deterministic fault knobs remain available to the lab without creating a second protocol.
 */

export const AUR1_HEADER_BYTES = 24;
export const MIC_SAMPLES_PER_FRAME = 320;
export const TTS_SAMPLES_PER_FRAME = 480;

export interface MicFrameCoordinates {
  streamId: number;
  frameSequence: number;
  sampleStart: bigint;
  discontinuity: boolean;
}

export interface ParsedTtsFrame {
  streamId: number;
  frameSequence: number;
  sampleStart: bigint;
  discontinuity: boolean;
  final: boolean;
  samples: Int16Array;
}

export type Fault = "duplicate" | "gap" | "badEpoch" | "backpressure";

export function encodeMicFrame(samples: Int16Array, coordinates: MicFrameCoordinates): ArrayBuffer {
  if (samples.length !== MIC_SAMPLES_PER_FRAME) {
    throw new Error(`mic frame must contain ${MIC_SAMPLES_PER_FRAME} samples`);
  }
  const encoded = new ArrayBuffer(AUR1_HEADER_BYTES + samples.byteLength);
  const bytes = new Uint8Array(encoded);
  bytes.set(new TextEncoder().encode("AUR1"));
  const view = new DataView(encoded);
  view.setUint8(4, 1);
  view.setUint8(5, coordinates.discontinuity ? 1 : 0);
  view.setUint16(6, 0, true);
  view.setUint32(8, coordinates.streamId, true);
  view.setUint32(12, coordinates.frameSequence, true);
  view.setBigUint64(16, coordinates.sampleStart, true);
  for (let index = 0; index < samples.length; index += 1) {
    view.setInt16(AUR1_HEADER_BYTES + index * 2, samples[index], true);
  }
  return encoded;
}

export function parseTtsFrame(encoded: ArrayBuffer): ParsedTtsFrame {
  if (encoded.byteLength !== AUR1_HEADER_BYTES + TTS_SAMPLES_PER_FRAME * 2) {
    throw new Error("TTS frame must contain a 24-byte header and 480 PCM16 samples");
  }
  const bytes = new Uint8Array(encoded);
  if (new TextDecoder().decode(bytes.slice(0, 4)) !== "AUR1") {
    throw new Error("invalid AUR1 magic");
  }
  const view = new DataView(encoded);
  if (view.getUint8(4) !== 2) {
    throw new Error("binary message is not a TTS frame");
  }
  const flags = view.getUint8(5);
  if (flags & ~3 || view.getUint16(6, true) !== 0) {
    throw new Error("invalid AUR1 flags");
  }
  const samples = new Int16Array(TTS_SAMPLES_PER_FRAME);
  for (let index = 0; index < samples.length; index += 1) {
    samples[index] = view.getInt16(AUR1_HEADER_BYTES + index * 2, true);
  }
  return {
    streamId: view.getUint32(8, true),
    frameSequence: view.getUint32(12, true),
    sampleStart: view.getBigUint64(16, true),
    discontinuity: Boolean(flags & 1),
    final: Boolean(flags & 2),
    samples,
  };
}

export class FaultController {
  private readonly armed = new Set<Fault>();

  arm(fault: Fault): void {
    this.armed.add(fault);
  }

  consume(fault: Fault): boolean {
    const active = this.armed.has(fault);
    this.armed.delete(fault);
    return active;
  }

  nextFrameSequence(sequence: number): number[] {
    if (this.consume("duplicate")) return [sequence, sequence];
    if (this.consume("gap")) return [sequence + 2];
    return [sequence];
  }

  rendererEpoch(epoch: number): number {
    return this.consume("badEpoch") ? Math.max(0, epoch - 1) : epoch;
  }
}

export class SharedInitializer<T> {
  private value: T | null = null;
  private pending: Promise<T> | null = null;

  get(factory: () => Promise<T>): Promise<T> {
    if (this.value !== null) return Promise.resolve(this.value);
    if (this.pending !== null) return this.pending;
    this.pending = factory().then(
      (value) => {
        this.value = value;
        this.pending = null;
        return value;
      },
      (error: unknown) => {
        this.pending = null;
        throw error;
      },
    );
    return this.pending;
  }

  clear(): void {
    this.value = null;
    this.pending = null;
  }
}

export class ConnectionScope<T extends object> {
  private current: T | null = null;

  install(value: T): void {
    this.current = value;
  }

  isCurrent(value: T): boolean {
    return this.current === value;
  }

  clear(value: T): boolean {
    if (!this.isCurrent(value)) return false;
    this.current = null;
    return true;
  }
}

export class TtsFrameTracker {
  private readonly terminal = new Map<number, { frame: number; sample: bigint; count: number }>();
  private streamId: number | null = null;
  private nextFrame = 0;
  private nextSample = 0n;

  accept(frame: ParsedTtsFrame): "accepted" | "duplicate" | "discontinuity" {
    const terminal = this.terminal.get(frame.streamId);
    if (terminal) {
      if (
        frame.final
        && frame.frameSequence === terminal.frame
        && frame.sampleStart === terminal.sample
        && frame.samples.length === terminal.count
      ) {
        return "duplicate";
      }
      throw new Error("terminal TTS stream id was reused");
    }
    const sameStream = frame.streamId === this.streamId;
    if (this.streamId === null) {
      if (!frame.discontinuity && (frame.frameSequence !== 0 || frame.sampleStart !== 0n)) {
        throw new Error("new unflagged TTS stream must start at frame/sample zero");
      }
      this.streamId = frame.streamId;
    } else if (frame.streamId !== this.streamId) {
      if (!frame.discontinuity) throw new Error("new TTS stream overlaps the active stream");
      this.streamId = frame.streamId;
    }

    if (sameStream && frame.frameSequence < this.nextFrame) {
      return "duplicate";
    }
    let result: "accepted" | "discontinuity" = "accepted";
    if (frame.discontinuity) {
      result = "discontinuity";
    } else if (frame.frameSequence !== this.nextFrame || frame.sampleStart !== this.nextSample) {
      throw new Error("TTS frame/sample gap without discontinuity");
    }
    this.nextFrame = frame.frameSequence + 1;
    this.nextSample = frame.sampleStart + BigInt(frame.samples.length);
    if (frame.final) {
      this.terminal.set(frame.streamId, {
        frame: frame.frameSequence,
        sample: frame.sampleStart,
        count: frame.samples.length,
      });
      while (this.terminal.size > 128) {
        const oldest = this.terminal.keys().next().value as number | undefined;
        if (oldest === undefined) break;
        this.terminal.delete(oldest);
      }
      this.resetActive();
    }
    return result;
  }

  resetActive(): void {
    this.streamId = null;
    this.nextFrame = 0;
    this.nextSample = 0n;
  }

  cancelActive(): void {
    if (this.streamId !== null) {
      this.terminal.set(this.streamId, { frame: -1, sample: -1n, count: -1 });
      while (this.terminal.size > 128) {
        const oldest = this.terminal.keys().next().value as number | undefined;
        if (oldest === undefined) break;
        this.terminal.delete(oldest);
      }
    }
    this.resetActive();
  }

  reset(): void {
    this.resetActive();
    this.terminal.clear();
  }
}
