/**
 * Plays bounded clause-tagged 24 kHz PCM16 mono frames for both the formal Pet Renderer and diagnostic
 * client. The queue keeps paused PCM intact, flushes only on authoritative stop/cancel/teardown, and
 * emits a single completion only after the audio cursor consumes the final source sample of a clause.
 */

export class ClausePlaybackQueue {
  constructor({ outputSampleRate, maxQueuedSamples = 48_000 }) {
    this.outputSampleRate = outputSampleRate;
    this.maxQueuedSamples = maxQueuedSamples;
    this.queue = [];
    this.position = 0;
    this.step = 24_000 / outputSampleRate;
    this.paused = false;
    this.totalQueuedSamples = 0;
  }

  get queuedSamples() {
    return this.totalQueuedSamples;
  }

  append({ clauseId, samples, final }) {
    if (this.totalQueuedSamples + samples.length > this.maxQueuedSamples) {
      this.flush();
      return [{ type: "overrun", clauseId }];
    }
    this.queue.push({ clauseId, samples, final, offset: 0 });
    this.totalQueuedSamples += samples.length;
    return [];
  }

  pause() {
    this.paused = true;
  }

  resume() {
    this.paused = false;
  }

  flush() {
    this.queue = [];
    this.position = 0;
    this.totalQueuedSamples = 0;
  }

  render(channel) {
    const events = [];
    if (this.paused) {
      channel.fill(0);
      return events;
    }
    for (let index = 0; index < channel.length; index += 1) {
      const chunk = this.queue[0];
      if (!chunk) {
        channel[index] = 0;
        continue;
      }
      const left = Math.min(chunk.samples.length - 1, Math.floor(this.position));
      const right = Math.min(chunk.samples.length - 1, left + 1);
      const fraction = this.position - left;
      channel[index] = (
        chunk.samples[left] * (1 - fraction) + chunk.samples[right] * fraction
      ) / 32768;
      this.position += this.step;
      if (this.position >= chunk.samples.length) {
        this.queue.shift();
        this.totalQueuedSamples -= chunk.samples.length;
        this.position = 0;
        if (chunk.final) {
          events.push({ type: "clause-complete", clauseId: chunk.clauseId });
        }
      }
    }
    return events;
  }
}

const WorkletProcessor = globalThis.AudioWorkletProcessor;
if (WorkletProcessor && typeof globalThis.registerProcessor === "function") {
  class AuraPlaybackProcessor extends WorkletProcessor {
    constructor() {
      super();
      this.playback = new ClausePlaybackQueue({ outputSampleRate: globalThis.sampleRate });
      this.port.onmessage = (event) => {
        const data = event.data;
        let events = [];
        if (data?.type === "append") {
          events = this.playback.append(data);
        } else if (data?.type === "pause") {
          this.playback.pause();
        } else if (data?.type === "resume") {
          this.playback.resume();
        } else if (data?.type === "flush") {
          this.playback.flush();
        }
        for (const playbackEvent of events) this.port.postMessage(playbackEvent);
      };
    }

    process(_inputs, outputs) {
      const channel = outputs[0]?.[0];
      if (!channel) return true;
      for (const event of this.playback.render(channel)) this.port.postMessage(event);
      return true;
    }
  }

  globalThis.registerProcessor("aurabot-tts-playback", AuraPlaybackProcessor);
}
