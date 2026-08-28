/**
 * Registers the Pet's microphone capture and TTS playback processors in one AudioWorklet module. Capture
 * continuously resamples browser audio to raw 16 kHz/20 ms PCM and emits local RMS speech hints; playback
 * keeps one clause's 24 kHz PCM queued across pauses and reports completion only after audio_end and the
 * physical output cursor consume the final source sample. Pure helpers are exported for deterministic tests.
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
          const fraction = (this.nextOutputNumerator - (index - 1) * this.targetRate) / this.targetRate;
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
    for (const sample of samples) energy += (sample / 32768) ** 2;
    const rms = Math.sqrt(energy / Math.max(1, samples.length));
    if (!this.candidate) {
      if (rms >= Math.max(0.020, this.noiseFloor * 3)) {
        this.candidate = true;
        this.quietFrames = 0;
        return "candidate";
      }
      this.noiseFloor += this.alpha * (rms - this.noiseFloor);
      return null;
    }
    if (rms < Math.max(0.012, this.noiseFloor * 1.8)) {
      this.quietFrames += 1;
      if (this.quietFrames >= this.releaseFrames) {
        this.candidate = false;
        this.quietFrames = 0;
        return "released";
      }
    } else this.quietFrames = 0;
    return null;
  }
}

export class ClausePlaybackQueue {
  constructor({ outputSampleRate, maxQueuedSamples = 48_000 }) {
    this.maxQueuedSamples = maxQueuedSamples;
    this.step = 24_000 / outputSampleRate;
    this.samples = [];
    this.offset = 0;
    this.position = 0;
    this.paused = false;
    this.clauseId = null;
    this.finished = false;
    this.completed = false;
  }

  get queuedSamples() { return this.samples.length - this.offset; }

  offer(clauseId) {
    if (this.clauseId) throw new Error("a clause is already active");
    this.clauseId = clauseId;
    this.finished = false;
    this.completed = false;
  }

  append(samples) {
    if (!this.clauseId) return [{ type: "overrun" }];
    if (this.queuedSamples + samples.length > this.maxQueuedSamples) {
      const clauseId = this.clauseId;
      this.flush();
      return [{ type: "overrun", clauseId }];
    }
    this.samples.push(...samples);
    return [];
  }

  finish(clauseId) {
    if (this.clauseId !== clauseId) throw new Error("audio_end clause mismatch");
    this.finished = true;
  }

  pause() { this.paused = true; }
  resume() { this.paused = false; }
  flush() {
    this.samples = [];
    this.offset = 0;
    this.position = 0;
    this.clauseId = null;
    this.finished = false;
    this.completed = false;
  }

  render(channel) {
    const events = [];
    channel.fill(0);
    if (this.paused || this.completed) return events;
    for (let index = 0; index < channel.length && this.offset < this.samples.length; index += 1) {
      const left = Math.min(this.samples.length - 1, this.offset + Math.floor(this.position));
      const right = Math.min(this.samples.length - 1, left + 1);
      const fraction = this.position - left;
      channel[index] = (this.samples[left] * (1 - fraction) + this.samples[right] * fraction) / 32768;
      this.position += this.step;
      const consumed = Math.floor(this.position);
      this.offset = Math.min(this.samples.length, this.offset + consumed);
      this.position -= consumed;
      if (this.offset > 4096 && this.offset * 2 > this.samples.length) {
        this.samples = this.samples.slice(this.offset);
        this.offset = 0;
      }
      if (this.finished && this.offset === this.samples.length && this.clauseId) {
        this.completed = true;
        events.push({ type: "clause-complete", clauseId: this.clauseId });
        break;
      }
    }
    return events;
  }
}

function toPcm16(value) { return Math.max(-32768, Math.min(32767, Math.round(value * 32767))); }

const Processor = globalThis.AudioWorkletProcessor;
if (Processor && typeof globalThis.registerProcessor === "function") {
  class MicProcessor extends Processor {
    constructor() {
      super();
      this.resampler = new ContinuousPcm16Resampler(globalThis.sampleRate, 16_000);
      this.gate = new LocalSpeechGate();
      this.pending = [];
    }

    process(inputs) {
      const channel = inputs[0]?.[0];
      if (!channel) return true;
      this.pending.push(...this.resampler.push(channel));
      while (this.pending.length >= 320) {
        const frame = Int16Array.from(this.pending.splice(0, 320));
        const gate = this.gate.accept(frame);
        if (gate) this.port.postMessage({ type: gate });
        this.port.postMessage({ type: "frame", samples: frame }, [frame.buffer]);
      }
      return true;
    }
  }

  class PlaybackProcessor extends Processor {
    constructor() {
      super();
      this.playback = new ClausePlaybackQueue({ outputSampleRate: globalThis.sampleRate });
      this.port.onmessage = ({ data }) => {
        let events = [];
        if (data?.type === "offer") this.playback.offer(data.clauseId);
        else if (data?.type === "append") events = this.playback.append(data.samples);
        else if (data?.type === "finish") this.playback.finish(data.clauseId);
        else if (data?.type === "pause") this.playback.pause();
        else if (data?.type === "resume") this.playback.resume();
        else if (data?.type === "flush") this.playback.flush();
        for (const event of events) this.port.postMessage(event);
      };
    }

    process(_inputs, outputs) {
      const channel = outputs[0]?.[0];
      if (!channel) return true;
      for (const event of this.playback.render(channel)) this.port.postMessage(event);
      return true;
    }
  }

  globalThis.registerProcessor("aurabot-mic-capture", MicProcessor);
  globalThis.registerProcessor("aurabot-tts-playback", PlaybackProcessor);
}
