/**
 * Plays bounded 24 kHz PCM16 mono frames from the realtime protocol at the browser audio rate. The
 * diagnostic main thread validates AUR1 ordering before posting samples; this processor keeps a small
 * sample FIFO, resamples linearly, and reports underruns without timers or polling.
 */

class AuraPlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.position = 0;
    this.step = 24_000 / sampleRate;
    this.maxQueuedSamples = 48_000;
    this.port.onmessage = (event) => {
      if (event.data?.type === "flush") {
        this.queue = [];
        this.position = 0;
      } else {
        if (this.queue.length + event.data.length > this.maxQueuedSamples) {
          this.queue = [];
          this.position = 0;
          this.port.postMessage({ type: "overrun" });
          return;
        }
        for (const sample of event.data) this.queue.push(sample / 32768);
      }
    };
  }

  process(_inputs, outputs) {
    const channel = outputs[0]?.[0];
    if (!channel) return true;
    for (let index = 0; index < channel.length; index += 1) {
      const left = Math.floor(this.position);
      if (left + 1 >= this.queue.length) {
        channel[index] = 0;
        continue;
      }
      const fraction = this.position - left;
      channel[index] = this.queue[left] * (1 - fraction) + this.queue[left + 1] * fraction;
      this.position += this.step;
    }
    const consumed = Math.floor(this.position);
    if (consumed > 0) {
      this.queue.splice(0, consumed);
      this.position -= consumed;
    }
    return true;
  }
}

registerProcessor("aurabot-tts-playback", AuraPlaybackProcessor);
