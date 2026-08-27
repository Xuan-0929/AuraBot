/**
 * Converts browser-rate microphone floats into bounded 16 kHz PCM16LE mono 20 ms chunks inside the
 * audio render thread. It only posts complete 320-sample frames to the diagnostic page; the main thread
 * owns AUR1 headers, stream sequencing, WebSocket transport, and fault injection.
 */

export class ContinuousPcm16Resampler {
  constructor(sourceRate, targetRate) {
    this.sourceRate = Math.round(sourceRate);
    this.targetRate = Math.round(targetRate);
    this.sourceIndex = 0;
    this.nextOutputNumerator = 0;
    this.previous = null;
  }

  push(samples) {
    const output = [];
    for (const current of samples) {
      const index = this.sourceIndex;
      if (this.previous === null) {
        if (this.nextOutputNumerator === 0) {
          output.push(toPcm16(current));
          this.nextOutputNumerator += this.sourceRate;
        }
      } else {
        const boundary = index * this.targetRate;
        while (this.nextOutputNumerator <= boundary) {
          const fraction =
            (this.nextOutputNumerator - (index - 1) * this.targetRate) / this.targetRate;
          output.push(toPcm16(this.previous + (current - this.previous) * fraction));
          this.nextOutputNumerator += this.sourceRate;
        }
      }
      this.previous = current;
      this.sourceIndex += 1;
    }
    return Int16Array.from(output);
  }
}

function toPcm16(value) {
  return Math.max(-32768, Math.min(32767, Math.round(value * 32767)));
}

const WorkletProcessor = globalThis.AudioWorkletProcessor;
if (WorkletProcessor && typeof globalThis.registerProcessor === "function") {
  class AuraMicProcessor extends WorkletProcessor {
    constructor() {
      super();
      this.resampler = new ContinuousPcm16Resampler(globalThis.sampleRate, 16_000);
      this.output = [];
    }

    process(inputs) {
      const channel = inputs[0]?.[0];
      if (!channel) return true;
      this.output.push(...this.resampler.push(channel));
      while (this.output.length >= 320) {
        const frame = Int16Array.from(this.output.splice(0, 320));
        this.port.postMessage(frame, [frame.buffer]);
      }
      return true;
    }
  }

  globalThis.registerProcessor("aurabot-mic-capture", AuraMicProcessor);
}
