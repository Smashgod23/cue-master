/**
 * AudioWorklet processor: captures microphone audio, downsamples to 16 kHz
 * mono, converts to 16-bit PCM, and posts 300 ms chunks to the main thread.
 *
 * The main thread sends each chunk as a binary WebSocket frame.
 */

const TARGET_RATE = 16000;
const FRAME_MS = 300;

class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    // Filled with downsampled float32 samples
    this._buf = [];
    // Computed once we know sampleRate (global in worklet scope)
    this._ratio = null;
    this._frameSize = null;
    // Leftover fractional index from previous block
    this._phase = 0;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel || channel.length === 0) return true;

    if (this._ratio === null) {
      // sampleRate is a global provided by the AudioWorklet runtime
      this._ratio = sampleRate / TARGET_RATE;
      this._frameSize = Math.floor(TARGET_RATE * (FRAME_MS / 1000)); // 4800
    }

    // Simple linear downsample: step through the input at _ratio pace
    let i = this._phase;
    while (i < channel.length) {
      this._buf.push(channel[Math.min(Math.floor(i), channel.length - 1)]);
      i += this._ratio;
    }
    // Carry the fractional remainder into the next block
    this._phase = i - channel.length;

    // Emit a frame whenever we have enough samples
    while (this._buf.length >= this._frameSize) {
      const frame = this._buf.splice(0, this._frameSize);
      const pcm = new Int16Array(frame.length);
      for (let j = 0; j < frame.length; j++) {
        pcm[j] = Math.max(-32768, Math.min(32767, Math.round(frame[j] * 32767)));
      }
      // Transfer the ArrayBuffer so we avoid copying it
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }

    return true;
  }
}

registerProcessor("pcm-processor", PCMProcessor);
