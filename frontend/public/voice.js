"use strict";
// Voice for FirstRung: microphone capture, WAV encoding, playback of Gemini's voice, and the live coach.
// The live coach talks to the Gemini Live API directly over a WebSocket, using a single-use token from our API
// (POST /api/coach/start). The real API key never reaches the browser.

const LIVE_WS = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContentConstrained?access_token=";
const MIC_RATE = 16000;     // Gemini expects 16 kHz, 16-bit, mono PCM in
const VOICE_RATE = 24000;   // ... and sends 24 kHz PCM back

const b64FromBytes = (u8) => {
  let s = "";
  for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
  return btoa(s);
};
const bytesFromB64 = (b64) => Uint8Array.from(atob(b64), c => c.charCodeAt(0));

// Microphone -> 16 kHz Int16 chunks, whatever the device's own sample rate is.
class MicCapture {
  constructor(onChunk) { this.onChunk = onChunk; this.level = 0; this.carry = new Float32Array(0); }
  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    this.ctx = new AudioContext();
    if (this.ctx.state === "suspended") await this.ctx.resume();
    await this.ctx.audioWorklet.addModule("audio-worklet.js");
    this.node = new AudioWorkletNode(this.ctx, "pcm-capture");
    this.node.port.onmessage = (e) => this._push(e.data);
    this.ctx.createMediaStreamSource(this.stream).connect(this.node);   // never routed to the speakers
    this.ratio = this.ctx.sampleRate / MIC_RATE;
  }
  _push(f32) {
    let peak = 0;
    for (let i = 0; i < f32.length; i++) peak = Math.max(peak, Math.abs(f32[i]));
    this.level = peak;
    const buf = new Float32Array(this.carry.length + f32.length);
    buf.set(this.carry); buf.set(f32, this.carry.length);
    const n = Math.floor(buf.length / this.ratio), out = new Int16Array(n);
    for (let i = 0; i < n; i++) {   // downsample by averaging each window
      const a = Math.floor(i * this.ratio), b = Math.max(a + 1, Math.floor((i + 1) * this.ratio));
      let s = 0; for (let j = a; j < b; j++) s += buf[j];
      out[i] = Math.max(-1, Math.min(1, s / (b - a))) * 0x7fff;
    }
    this.carry = buf.slice(Math.floor(n * this.ratio));
    if (n) this.onChunk(out);
  }
  stop() { this.stream?.getTracks().forEach(t => t.stop()); this.ctx?.close(); }
}

// Int16 PCM chunks -> a WAV file (for "Ask by voice").
function wavFromPcm16(chunks, rate = MIC_RATE) {
  const len = chunks.reduce((a, c) => a + c.length, 0), buf = new ArrayBuffer(44 + len * 2), v = new DataView(buf);
  const str = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF"); v.setUint32(4, 36 + len * 2, true); str(8, "WAVE"); str(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, len * 2, true);
  let o = 44;
  for (const c of chunks) for (let i = 0; i < c.length; i++, o += 2) v.setInt16(o, c[i], true);
  return new Blob([buf], { type: "audio/wav" });
}

// Plays Gemini's 24 kHz PCM chunks back to back; interrupt() stops at once when the new hire talks over it.
class PcmPlayer {
  constructor() { this.ctx = new AudioContext(); this.next = 0; this.sources = new Set(); }
  play(b64) {
    const u8 = bytesFromB64(b64), pcm = new Int16Array(u8.buffer, 0, u8.byteLength >> 1), f = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) f[i] = pcm[i] / 0x8000;
    const buf = this.ctx.createBuffer(1, f.length, VOICE_RATE);
    buf.copyToChannel(f, 0);
    const src = this.ctx.createBufferSource();
    src.buffer = buf; src.connect(this.ctx.destination);
    const at = Math.max(this.ctx.currentTime + 0.04, this.next);
    src.start(at); this.next = at + buf.duration;
    this.sources.add(src); src.onended = () => this.sources.delete(src);
  }
  get speaking() { return this.next > this.ctx.currentTime; }
  interrupt() { this.sources.forEach(s => { try { s.stop(); } catch {} }); this.sources.clear(); this.next = 0; }
  close() { this.interrupt(); this.ctx.close(); }
}

// The live coach: microphone in, Gemini's voice out, optional screen pictures (1 per second), live captions.
// ui = { caption(who, text) | caption(null) to close the current lines, ended(reason) }
class LiveCoach {
  constructor(ui) { this.ui = ui; }

  start({ token, model }) {
    this.player = new PcmPlayer();   // created inside the click, so the browser allows it to play
    return new Promise((resolve, reject) => {
      let ready = false;
      const ws = this.ws = new WebSocket(LIVE_WS + encodeURIComponent(token));
      ws.onopen = () => ws.send(JSON.stringify({ setup: { model: "models/" + model } }));   // settings are locked in the token
      ws.onmessage = async (ev) => {
        const m = JSON.parse(typeof ev.data === "string" ? ev.data : await ev.data.text());
        if (m.setupComplete) {
          ws.send(JSON.stringify({ clientContent: { turnComplete: true, turns: [{ role: "user", parts: [{
            text: "(The new hire just opened the coach. Greet them in one short sentence and ask what they are stuck on.)" }] }] } }));
          try {
            this.mic = new MicCapture(pcm => this._send({ audio: { data: b64FromBytes(new Uint8Array(pcm.buffer)), mimeType: `audio/pcm;rate=${MIC_RATE}` } }));
            await this.mic.start();
            ready = true; resolve();
          } catch {
            reject(new Error("The microphone is blocked. Allow microphone access for this site and try again."));
            this.stop();
          }
        }
        const sc = m.serverContent;
        if (sc) {
          if (sc.interrupted) this.player?.interrupt();
          for (const p of sc.modelTurn?.parts || []) if (p.inlineData?.data) this.player?.play(p.inlineData.data);
          if (sc.inputTranscription?.text) this.ui.caption("you", sc.inputTranscription.text);
          if (sc.outputTranscription?.text) this.ui.caption("coach", sc.outputTranscription.text);
          if (sc.turnComplete) this.ui.caption(null);
        }
        if (m.goAway) this.ui.caption("system", "This session is about to end. Start a new one if you need more help.");
      };
      ws.onerror = () => { if (!ready) reject(new Error("Couldn't reach the live coach. Check your connection and try again.")); };
      ws.onclose = (e) => { const was = !!this.ws; this._cleanup(); if (was) this.ui.ended(e.code === 1000 || e.code === 1005 ? "" : e.reason || `connection closed (${e.code})`); };
    });
  }

  get level() { return this.mic?.level || 0; }
  get speaking() { return !!this.player?.speaking; }
  get sharing() { return !!this.screen; }

  async shareScreen(onStopped) {
    this.screen = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 5 }, audio: false });
    const video = Object.assign(document.createElement("video"), { muted: true, playsInline: true, srcObject: this.screen });
    await video.play();
    const canvas = document.createElement("canvas");
    this.screenTimer = setInterval(() => {   // Gemini Live takes at most one picture per second
      if (!video.videoWidth) return;
      const scale = Math.min(1, 1280 / video.videoWidth);
      canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      this._send({ video: { data: canvas.toDataURL("image/jpeg", 0.7).split(",")[1], mimeType: "image/jpeg" } });
    }, 1000);
    this.screen.getVideoTracks()[0].onended = () => { this.stopScreen(); onStopped?.(); };
  }
  stopScreen() { clearInterval(this.screenTimer); this.screen?.getTracks().forEach(t => t.stop()); this.screen = null; }

  stop() { const ws = this.ws; this.ws = null; this._cleanup(); try { ws?.close(1000); } catch {} this.ui.ended(""); }
  _send(input) { if (this.ws?.readyState === 1) this.ws.send(JSON.stringify({ realtimeInput: input })); }
  _cleanup() { this.mic?.stop(); this.mic = null; this.stopScreen(); this.player?.close(); this.player = null; }
}
