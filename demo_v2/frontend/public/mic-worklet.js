// AudioWorklet: downsample browser mic rate -> 16kHz Int16 PCM and emit ~100ms chunks.
// VAD and end-of-speech detection run server-side (Silero), so this stays simple.
// Ported from the source project's web/static/mic-worklet.js.

class MicDownsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this.srcRate = sampleRate; // provided by AudioWorkletGlobalScope
    this.dstRate = 16000;
    this.ratio = this.srcRate / this.dstRate;
    this.phase = 0;
    this.buffer = []; // Int16 samples awaiting flush
    this.flushEvery = 1600; // 100ms @ 16kHz
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const ch = input[0];
    if (!ch) return true;

    // Linear downsample to 16kHz.
    let i = this.phase;
    while (i < ch.length) {
      const idx = Math.floor(i);
      const frac = i - idx;
      const s0 = ch[idx] || 0;
      const s1 = ch[idx + 1] !== undefined ? ch[idx + 1] : s0;
      const sample = s0 + (s1 - s0) * frac;
      const clipped = Math.max(-1, Math.min(1, sample));
      this.buffer.push(clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff);
      i += this.ratio;
    }
    this.phase = i - ch.length;

    while (this.buffer.length >= this.flushEvery) {
      const slice = this.buffer.slice(0, this.flushEvery);
      this.buffer = this.buffer.slice(this.flushEvery);
      const int16 = new Int16Array(slice);
      this.port.postMessage(int16.buffer, [int16.buffer]);
    }
    return true;
  }
}

registerProcessor('mic-downsampler', MicDownsampler);
