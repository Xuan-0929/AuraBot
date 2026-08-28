/**
 * Converts browser-rate microphone floats into bounded 16 kHz PCM16LE mono 20 ms chunks and runs the
 * shared production/diagnostic local speech-candidate gate inside the audio render thread. The main
 * thread owns AUR1 headers and transport; this module only emits complete frames plus immediate local
 * candidate/release hints, which never replace backend VAD authority.
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

export class LocalSpeechGate {
  constructor({ noiseFloor = 0.005, alpha = 0.05, releaseFrames = 10 } = {}) {
    this.noiseFloor = noiseFloor;
    this.alpha = alpha;
    this.releaseFrames = releaseFrames;
    this.candidate = false;
    this.quietFrames = 0;
  }

  accept(samples) {
    let energy = 0;
    for (const sample of samples) {
      const normalized = sample / 32768;
      energy += normalized * normalized;
    }
    const rms = Math.sqrt(energy / Math.max(1, samples.length));
    if (!this.candidate) {
      const attackThreshold = Math.max(0.020, this.noiseFloor * 3.0);
      if (rms >= attackThreshold) {
        this.candidate = true;
        this.quietFrames = 0;
        return "candidate";
      }
      this.noiseFloor += this.alpha * (rms - this.noiseFloor);
      return null;
    }
    const releaseThreshold = Math.max(0.012, this.noiseFloor * 1.8);
    if (rms < releaseThreshold) {
      this.quietFrames += 1;
      if (this.quietFrames >= this.releaseFrames) {
        this.candidate = false;
        this.quietFrames = 0;
        return "released";
      }
    } else {
      this.quietFrames = 0;
    }
    return null;
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
      this.gate = new LocalSpeechGate();
      this.output = [];
    }

    process(inputs) {
      const channel = inputs[0]?.[0];
      if (!channel) return true;
      this.output.push(...this.resampler.push(channel));
      while (this.output.length >= 320) {
        const frame = Int16Array.from(this.output.splice(0, 320));
        const candidateEvent = this.gate.accept(frame);
        if (candidateEvent !== null) {
          this.port.postMessage({ type: candidateEvent });
        }
        this.port.postMessage({ type: "frame", samples: frame }, [frame.buffer]);
      }
      return true;
    }
  }

  globalThis.registerProcessor("aurabot-mic-capture", AuraMicProcessor);
}
