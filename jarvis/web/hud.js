/* ==========================================================================
   JARVIS HUD
   Three independent pieces, wired together at the bottom:
     1. Reactor  -- the canvas visual
     2. Voice    -- Web Speech API in and out (free, no cloud STT bill)
     3. Link     -- WebSocket protocol with the Python backend
   ========================================================================== */

'use strict';

/* ==========================================================================
   1. REACTOR
   ========================================================================== */

const Reactor = (() => {
  const canvas = document.getElementById('reactor');
  const ctx = canvas.getContext('2d');
  const N_BARS = 72;

  const PALETTE = {
    idle:      { core: [79, 214, 255], glow: 0.35, spin: 0.10 },
    listening: { core: [79, 214, 255], glow: 1.00, spin: 0.30 },
    thinking:  { core: [120, 190, 255], glow: 0.85, spin: 1.30 },
    speaking:  { core: [255, 179, 71],  glow: 0.90, spin: 0.45 },
    awaiting:  { core: [255, 179, 71],  glow: 1.00, spin: 0.05 },
    error:     { core: [255, 95, 109],  glow: 0.90, spin: 0.06 }
  };

  let state = 'idle';
  let spectrum = new Float32Array(N_BARS);   // smoothed bar heights, 0..1
  let energy = 0;                            // smoothed overall amplitude
  let targetEnergy = 0;
  let phase = 0;
  let colour = PALETTE.idle.core.slice();

  function setState(next) {
    if (PALETTE[next]) state = next;
  }

  /** Feed live mic levels (Uint8Array from an AnalyserNode). */
  function feed(freqData) {
    if (!freqData) return;
    const step = Math.floor(freqData.length / N_BARS) || 1;
    let sum = 0;
    for (let i = 0; i < N_BARS; i++) {
      let v = 0;
      for (let j = 0; j < step; j++) v = Math.max(v, freqData[i * step + j] || 0);
      const norm = v / 255;
      spectrum[i] += (norm - spectrum[i]) * 0.35;
      sum += norm;
    }
    targetEnergy = Math.min(1, (sum / N_BARS) * 2.4);
  }

  /** Synthetic envelope for states with no real audio to analyse. */
  function simulate(t) {
    const base = state === 'thinking' ? 0.42 : state === 'speaking' ? 0.55 : 0.14;
    const wobble =
      Math.sin(t * 3.1) * 0.10 + Math.sin(t * 7.7) * 0.05 + Math.sin(t * 1.3) * 0.07;
    targetEnergy = Math.max(0, base + wobble);
    for (let i = 0; i < N_BARS; i++) {
      const target =
        base * (0.45 + 0.55 * Math.abs(Math.sin(t * 2.2 + i * 0.38) *
                                       Math.cos(t * 1.1 + i * 0.13)));
      spectrum[i] += (target - spectrum[i]) * 0.12;
    }
  }

  function draw(now) {
    const t = now / 1000;
    const cfg = PALETTE[state] || PALETTE.idle;

    // Ease the colour so state changes cross-fade instead of snapping.
    for (let i = 0; i < 3; i++) colour[i] += (cfg.core[i] - colour[i]) * 0.07;
    energy += (targetEnergy - energy) * 0.16;
    phase += cfg.spin * 0.016;

    const W = canvas.width, H = canvas.height;
    const cx = W / 2, cy = H / 2;
    const R = Math.min(W, H) / 2;
    const rgb = colour.map(Math.round);
    const C = (a) => `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a})`;

    ctx.clearRect(0, 0, W, H);

    // --- outer tick ring -------------------------------------------------
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(-phase * 0.25);
    for (let i = 0; i < 90; i++) {
      const major = i % 6 === 0;
      const a = (i / 90) * Math.PI * 2;
      const r0 = R * 0.945, r1 = R * (major ? 0.90 : 0.925);
      ctx.strokeStyle = C(major ? 0.62 : 0.24);
      ctx.lineWidth = major ? 2.4 : 1.1;
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * r0, Math.sin(a) * r0);
      ctx.lineTo(Math.cos(a) * r1, Math.sin(a) * r1);
      ctx.stroke();
    }
    ctx.restore();

    // --- rotating arc segments -------------------------------------------
    const arcs = [
      { r: 0.86, from: 0.0,  len: 1.15, w: 2.4, sp:  1.00, a: 0.78 },
      { r: 0.86, from: 3.4,  len: 0.75, w: 2.4, sp:  1.00, a: 0.52 },
      { r: 0.79, from: 1.7,  len: 1.90, w: 1.6, sp: -0.62, a: 0.44 },
      { r: 0.72, from: 4.6,  len: 1.30, w: 3.4, sp:  0.44, a: 0.62 }
    ];
    ctx.save();
    ctx.translate(cx, cy);
    for (const arc of arcs) {
      ctx.strokeStyle = C(arc.a * (0.5 + energy * 0.5));
      ctx.lineWidth = arc.w;
      ctx.lineCap = 'round';
      ctx.beginPath();
      const start = arc.from + phase * arc.sp;
      ctx.arc(0, 0, R * arc.r, start, start + arc.len);
      ctx.stroke();
    }
    ctx.restore();

    // --- spectrum bars ----------------------------------------------------
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(phase * 0.12 - Math.PI / 2);
    const inner = R * 0.44;
    for (let i = 0; i < N_BARS; i++) {
      const a = (i / N_BARS) * Math.PI * 2;
      const h = spectrum[i] * R * 0.24;
      const x0 = Math.cos(a) * inner, y0 = Math.sin(a) * inner;
      const x1 = Math.cos(a) * (inner + h), y1 = Math.sin(a) * (inner + h);
      const grad = ctx.createLinearGradient(x0, y0, x1, y1);
      grad.addColorStop(0, C(0.85));
      grad.addColorStop(1, C(0.05));
      ctx.strokeStyle = grad;
      ctx.lineWidth = 3.4;
      ctx.lineCap = 'round';
      ctx.beginPath();
      ctx.moveTo(x0, y0);
      ctx.lineTo(x1, y1);
      ctx.stroke();
    }
    ctx.restore();

    // --- inner rings ------------------------------------------------------
    ctx.save();
    ctx.translate(cx, cy);
    ctx.strokeStyle = C(0.22);
    ctx.lineWidth = 1;
    for (const r of [0.40, 0.335]) {
      ctx.beginPath();
      ctx.arc(0, 0, R * r, 0, Math.PI * 2);
      ctx.stroke();
    }
    // Triangular iris, the arc-reactor tell.
    ctx.rotate(phase * 0.5);
    ctx.strokeStyle = C(0.5);
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (let i = 0; i < 3; i++) {
      const a = (i / 3) * Math.PI * 2;
      const r = R * 0.30;
      const x = Math.cos(a) * r, y = Math.sin(a) * r;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.stroke();
    ctx.restore();

    // --- core -------------------------------------------------------------
    const pulse = 0.20 + energy * 0.11 + Math.sin(t * 2.0) * 0.008;
    const halo = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * (pulse + 0.30));
    halo.addColorStop(0, C(0.95 * cfg.glow));
    halo.addColorStop(0.30, C(0.42 * cfg.glow));
    halo.addColorStop(0.66, C(0.11 * cfg.glow));
    halo.addColorStop(1, C(0));
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(cx, cy, R * (pulse + 0.30), 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = `rgba(255,255,255,${0.72 + energy * 0.25})`;
    ctx.beginPath();
    ctx.arc(cx, cy, R * pulse * 0.44, 0, Math.PI * 2);
    ctx.fill();

    requestAnimationFrame(draw);
  }

  requestAnimationFrame(draw);
  return { setState, feed, simulate, get state() { return state; } };
})();


/* ==========================================================================
   2. VOICE  -- browser-native STT and TTS, so speech costs nothing
   ========================================================================== */

const Voice = (() => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const supported = Boolean(SR) && 'speechSynthesis' in window;

  let recog = null;
  let running = false;      // recogniser actually running
  let wakeMode = false;     // listening for "jarvis"
  let capturing = false;    // actively collecting a command
  let muted = false;
  let speaking = false;     // Jarvis is talking right now
  let speechStartedAt = 0;
  let captureTimer = null;
  let voice = null;
  let userName = 'Duke';

  const handlers = {
    command: () => {}, interim: () => {}, state: () => {}, acknowledged: () => {}
  };

  /* Wake-word matching lives in wake.js so it can be unit-tested. */
  const { WAKE, strip, nameOnly } = globalThis.JarvisWake;

  /* Barge-in: keep listening while Jarvis speaks so his name interrupts him.
     Relies on the browser's echo cancellation to avoid hearing himself. If it
     ever self-triggers on your hardware, set this to false. */
  const BARGE_IN = true;
  const SETTLE_MS = 700;  // ignore the first moment of our own speech

  function build() {
    const r = new SR();
    r.continuous = true;
    r.interimResults = true;
    r.lang = 'en-US';
    r.maxAlternatives = 1;

    r.onstart = () => { running = true; };

    r.onresult = (event) => {
      let interim = '';
      let final = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const chunk = event.results[i][0].transcript;
        if (event.results[i].isFinal) final += chunk;
        else interim += chunk;
      }

      const live = (final || interim).trim();
      if (!live) return;

      // While Jarvis is talking, the only thing worth hearing is his name.
      if (speaking) {
        if (!BARGE_IN) return;
        if (Date.now() - speechStartedAt < SETTLE_MS) return;
        if (WAKE.test(live)) bargeIn();
        return;
      }

      if (!capturing) {
        if (!WAKE.test(live)) return;   // still waiting to be addressed
        capturing = true;
        handlers.state('listening');
      }

      handlers.interim(strip(live) || '...');
      armTimeout();

      if (!final) return;

      // Someone said the name on its own. That is a summons, not a request:
      // answer it locally and keep the mic open. No API call, no cost.
      if (nameOnly(final)) {
        acknowledge();
        return;
      }

      const text = strip(final);
      if (text.length > 1) {
        clearTimeout(captureTimer);
        capturing = false;
        handlers.interim('');
        handlers.command(text);
      }
    };

    r.onerror = (event) => {
      // 'no-speech' and 'aborted' are routine; anything else is worth showing.
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        wakeMode = false;
        handlers.state('mic-denied');
      }
    };

    r.onend = () => {
      running = false;
      // Chrome ends the session every ~60s, and abort() ends it deliberately.
      // Either way, come back if we are still meant to be listening.
      if (wakeMode || capturing) {
        setTimeout(() => { try { r.start(); } catch (_) {} }, 200);
      }
    };

    return r;
  }

  /** If the user trails off, close the capture window rather than hanging. */
  function armTimeout(ms = 6000) {
    clearTimeout(captureTimer);
    captureTimer = setTimeout(() => {
      capturing = false;
      handlers.interim('');
      handlers.state('idle');
    }, ms);
  }

  /* Spoken replies to being called by name. Said locally, so answering to his
     own name costs nothing and has no round-trip latency. */
  const ACKS = ['Yes, NAME?', 'NAME.', 'Go ahead.', 'Listening.'];
  let ackIndex = 0;

  /** They said the name and stopped. Answer, and hold the mic open. */
  function acknowledge() {
    clearTimeout(captureTimer);
    capturing = true;
    handlers.interim('');
    handlers.state('listening');

    const line = ACKS[ackIndex++ % ACKS.length].replace('NAME', userName);
    handlers.acknowledged(line);
    say(line, () => {
      // Clear whatever the recogniser buffered while we spoke, then wait
      // rather more patiently than usual -- they were only getting our
      // attention, so the actual request is still coming.
      restart();
      capturing = true;
      handlers.state('listening');
      armTimeout(12000);
    });
  }

  /** Heard our name mid-sentence: stop talking and listen. */
  function bargeIn() {
    speechSynthesis.cancel();
    speaking = false;
    capturing = true;
    handlers.interim('');
    handlers.state('listening');
    restart();
    armTimeout(9000);
  }

  /** Drop the current recognition session so its buffer starts clean. */
  function restart() {
    if (!recog) return;
    try { recog.abort(); } catch (_) {}   // onend brings it straight back
  }

  function start() {
    if (!supported) return false;
    if (!recog) recog = build();
    wakeMode = true;
    if (!running) { try { recog.start(); } catch (_) {} }
    return true;
  }

  function stop() {
    wakeMode = false;
    capturing = false;
    clearTimeout(captureTimer);
    if (recog && running) { try { recog.stop(); } catch (_) {} }
  }

  /** Click-to-talk: skip the wake word for one utterance. */
  function trigger() {
    if (!supported) return;
    if (!recog) recog = build();
    capturing = true;
    handlers.state('listening');
    armTimeout();
    if (!running) { try { recog.start(); } catch (_) {} }
  }

  /** Speak.
   *
   * The mic deliberately stays open so his name can cut him off mid-sentence.
   * Everything heard while `speaking` is discarded except the wake word, and
   * the recogniser is reset afterwards, so anything of his own that leaked
   * through echo cancellation never reaches a request.
   */
  function say(text, onDone) {
    if (!supported || muted || !text) { onDone && onDone(); return; }

    speechSynthesis.cancel();
    speaking = true;
    speechStartedAt = Date.now();
    if (wakeMode && !running && recog) { try { recog.start(); } catch (_) {} }

    const utter = new SpeechSynthesisUtterance(stripForSpeech(text));
    if (voice) utter.voice = voice;
    utter.rate = 1.06;
    utter.pitch = 0.92;
    utter.volume = 1;

    let settled = false;
    const finish = () => {
      if (settled) return;   // onend and onerror can both fire
      settled = true;
      if (!speaking) return; // barge-in already took over
      speaking = false;
      onDone && onDone();
    };
    utter.onend = finish;
    utter.onerror = finish;
    speechSynthesis.speak(utter);
  }

  function shutUp() {
    speechSynthesis.cancel();
    speaking = false;
  }

  /** Strip anything that sounds wrong when read aloud. */
  function stripForSpeech(text) {
    return text
      .replace(/https?:\/\/\S+/g, 'the link')
      .replace(/[*_`#>]/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  return {
    supported,
    start, stop, trigger, say, shutUp,
    // Exposed for tests and for the HUD's own matching.
    _internals: { WAKE, strip, nameOnly, ACKS },
    on(name, fn) { handlers[name] = fn; },
    get muted() { return muted; },
    set muted(v) { muted = v; if (v) speechSynthesis.cancel(); },
    get wakeMode() { return wakeMode; },
    get capturing() { return capturing; },
    get name() { return userName; },
    set name(v) { if (v) userName = v; }
  };
})();


/* ==========================================================================
   3. MIC ANALYSER  -- drives the reactor from real input levels
   ========================================================================== */

const Mic = (() => {
  let analyser = null;
  let data = null;

  async function open() {
    if (analyser) return true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        // Echo cancellation is what stops Jarvis hearing his own voice
        // through the speakers and interrupting himself.
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      });
      const audio = new (window.AudioContext || window.webkitAudioContext)();
      const source = audio.createMediaStreamSource(stream);
      analyser = audio.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.75;
      source.connect(analyser);
      data = new Uint8Array(analyser.frequencyBinCount);
      return true;
    } catch (_) {
      return false;
    }
  }

  function sample() {
    if (!analyser) return null;
    analyser.getByteFrequencyData(data);
    return data;
  }

  return { open, sample, get live() { return Boolean(analyser); } };
})();


/* ==========================================================================
   4. LINK  -- WebSocket protocol
   ========================================================================== */

const UI = {
  transcript: document.getElementById('transcript'),
  stateLabel: document.getElementById('state-label'),
  stateDetail: document.getElementById('state-detail'),
  heard: document.getElementById('heard'),
  greeting: document.getElementById('greeting'),
  compose: document.getElementById('compose'),
  spend: document.getElementById('spend'),
  toolCount: document.getElementById('tool-count'),
  confirmLayer: document.getElementById('confirm-layer'),
  confirmText: document.getElementById('confirm-text'),
  btnWake: document.getElementById('btn-wake'),
  btnMute: document.getElementById('btn-mute'),
  btnStop: document.getElementById('btn-stop')
};

let socket = null;
let currentBubble = null;
let pendingConfirm = null;
let toolsRun = 0;

function setState(state, detail) {
  document.body.dataset.state = state;
  Reactor.setState(state);
  const labels = {
    idle: 'standby', listening: 'listening', thinking: 'processing',
    speaking: 'speaking', awaiting: 'awaiting approval', error: 'fault'
  };
  UI.stateLabel.textContent = labels[state] || state;
  UI.stateDetail.textContent = detail || '';
}

function bubble(kind, who) {
  const el = document.createElement('div');
  el.className = `msg ${kind}`;
  el.innerHTML = `<div class="who">${who}</div><div class="body"></div>`;
  UI.transcript.appendChild(el);
  UI.transcript.scrollTop = UI.transcript.scrollHeight;
  return el.querySelector('.body');
}

function note(text, kind = 'system') {
  bubble(kind, kind === 'error' ? 'FAULT' : 'SYSTEM').textContent = text;
  UI.transcript.scrollTop = UI.transcript.scrollHeight;
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${proto}://${location.host}/ws`);

  socket.onopen = () => {
    document.getElementById('cap-link').classList.add('on');
    setState('idle');
  };

  socket.onclose = () => {
    document.getElementById('cap-link').classList.remove('on');
    setState('error', 'link lost -- reconnecting');
    setTimeout(connect, 2500);
  };

  socket.onmessage = (event) => handle(JSON.parse(event.data));
}

function handle(msg) {
  switch (msg.type) {
    case 'ready':
      UI.greeting.textContent = `online // ${msg.user}`;
      Voice.name = msg.user;
      break;

    case 'status':
      setState(msg.state, msg.detail);
      break;

    case 'delta':
      if (!currentBubble) currentBubble = bubble('jarvis', 'JARVIS');
      currentBubble.textContent += msg.text;
      UI.transcript.scrollTop = UI.transcript.scrollHeight;
      break;

    case 'tool': {
      if (msg.phase === 'start') {
        toolsRun++;
        UI.toolCount.textContent = `${toolsRun} calls`;
        const line = document.createElement('div');
        line.className = 'tool-line';
        line.dataset.tool = msg.name;
        line.textContent = `> ${msg.detail}`;
        UI.transcript.appendChild(line);
        UI.transcript.scrollTop = UI.transcript.scrollHeight;
      } else if (msg.phase === 'progress') {
        // Long jobs (3D generation) narrate themselves in place.
        const lines = UI.transcript.querySelectorAll(`.tool-line[data-tool="${msg.name}"]`);
        const last = lines[lines.length - 1];
        if (last) {
          last.textContent = `> ${msg.detail}`;
          UI.stateDetail.textContent = msg.detail;
        }
      } else {
        const lines = UI.transcript.querySelectorAll(`.tool-line[data-tool="${msg.name}"]`);
        const last = lines[lines.length - 1];
        if (last) {
          last.classList.add(msg.detail === 'ok' ? 'done' : 'failed');
          if (msg.detail !== 'ok') last.textContent += `  [${msg.detail}]`;
        }
      }
      break;
    }

    case 'artifact':
      showArtifact(msg);
      break;

    case 'confirm':
      pendingConfirm = msg.id;
      UI.confirmText.textContent = msg.preview;
      UI.confirmLayer.classList.add('show');
      setState('awaiting', 'say yes or no');
      Voice.say('Confirm?');
      break;

    case 'confirm_timeout':
      closeConfirm();
      note('Confirmation timed out. Nothing was sent.');
      break;

    case 'done': {
      const text = msg.text || '';
      currentBubble = null;
      UI.spend.textContent = `$${(msg.month_usd || 0).toFixed(4)} / mo`;
      if (text) {
        setState('speaking');
        Voice.say(text, () => setState('idle'));
      } else {
        setState('idle');
      }
      break;
    }

    case 'error':
      currentBubble = null;
      note(msg.message, 'error');
      setState('error', '');
      Voice.say('Something went wrong.');
      setTimeout(() => setState('idle'), 2500);
      break;
  }
}

/** Render a generated image or 3D model into the transcript. */
function showArtifact({ kind, label, url, path }) {
  const box = document.createElement('div');
  box.className = `artifact ${kind}`;

  // For a model, `url` is the mesh download and `path` is Tripo's rendered
  // preview image, when it gave us one.
  const preview = kind === 'image' ? url : path;

  if (preview) {
    const img = document.createElement('img');
    img.src = preview;
    img.alt = label || kind;
    img.loading = 'lazy';
    img.addEventListener('click', () => {
      document.getElementById('viewer-img').src = preview;
      document.getElementById('viewer').classList.add('show');
    });
    img.style.cursor = 'zoom-in';
    box.appendChild(img);
  } else {
    const ph = document.createElement('div');
    ph.className = 'placeholder';
    ph.textContent = '3D MODEL READY';
    box.appendChild(ph);
  }

  const meta = document.createElement('div');
  meta.className = 'meta';

  const k = document.createElement('span');
  k.className = 'kind';
  k.textContent = kind === 'image' ? 'IMAGE' : 'MODEL';
  meta.appendChild(k);

  const l = document.createElement('span');
  l.className = 'label';
  l.textContent = label || '';
  l.title = label || '';
  meta.appendChild(l);

  if (url) {
    const a = document.createElement('a');
    a.href = url;
    a.textContent = kind === 'image' ? 'OPEN' : 'DOWNLOAD';
    a.target = '_blank';
    a.rel = 'noopener';
    if (kind === 'image') a.download = '';
    meta.appendChild(a);
  }

  box.appendChild(meta);
  UI.transcript.appendChild(box);
  UI.transcript.scrollTop = UI.transcript.scrollHeight;
  // A new artifact starts a fresh reply bubble, so text after it is separate.
  currentBubble = null;
}

function send(text) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    note('Not connected to the Jarvis service.', 'error');
    return;
  }
  Voice.shutUp();
  bubble('user', 'YOU').textContent = text;
  currentBubble = null;
  socket.send(JSON.stringify({ type: 'message', text }));
  setState('thinking', '');
}

function closeConfirm() {
  UI.confirmLayer.classList.remove('show');
  pendingConfirm = null;
}

function answerConfirm(approved) {
  if (!pendingConfirm) return;
  socket.send(JSON.stringify({ type: 'confirm_response', id: pendingConfirm, approved }));
  closeConfirm();
  setState('thinking', approved ? 'approved' : 'declined');
}


/* ==========================================================================
   5. WIRING
   ========================================================================== */

// Reactor animation source: real mic levels when we have them, else synthetic.
setInterval(() => {
  const state = Reactor.state;
  const data = (state === 'listening' && Mic.live) ? Mic.sample() : null;
  if (data) Reactor.feed(data);
  else Reactor.simulate(performance.now() / 1000);
}, 33);

Voice.on('command', (text) => {
  UI.heard.textContent = '';
  UI.heard.classList.remove('live');

  // While a confirmation is up, a spoken yes/no answers it instead of
  // starting a new turn.
  if (pendingConfirm) {
    if (/\b(yes|yeah|yep|confirm|approve|do it|go ahead|send it|please do)\b/i.test(text)) {
      answerConfirm(true);
      return;
    }
    if (/\b(no|nope|cancel|stop|don'?t|decline|abort|never ?mind)\b/i.test(text)) {
      answerConfirm(false);
      return;
    }
  }
  send(text);
});

Voice.on('interim', (text) => {
  UI.heard.textContent = text;
  UI.heard.classList.toggle('live', Boolean(text));
});

Voice.on('acknowledged', (line) => {
  bubble('jarvis', 'JARVIS').textContent = line;
  currentBubble = null;
});

Voice.on('state', (state) => {
  if (state === 'mic-denied') {
    UI.btnWake.classList.remove('active');
    note('Microphone access was denied. Type instead, or allow the mic and click WAKE WORD.', 'error');
    setState('idle');
  } else {
    setState(state, state === 'listening' ? 'go ahead' : '');
  }
});

document.getElementById('reactor').addEventListener('click', async () => {
  await Mic.open();
  Voice.shutUp();
  Voice.trigger();
});

const WAKE_PREF = 'jarvis.wakeMode';

function remember(key, value) {
  try { localStorage.setItem(key, value ? '1' : '0'); } catch (_) {}
}

function recall(key) {
  try { return localStorage.getItem(key) === '1'; } catch (_) { return false; }
}

async function enableWake(persist = true) {
  await Mic.open();
  if (!Voice.start()) {
    note('This browser has no Web Speech API. Use Chrome or Edge for voice.', 'error');
    return false;
  }
  UI.btnWake.classList.add('active');
  document.getElementById('cap-voice').classList.add('on');
  setState('idle', `say "Jarvis"`);
  if (persist) remember(WAKE_PREF, true);
  return true;
}

function disableWake() {
  Voice.stop();
  UI.btnWake.classList.remove('active');
  document.getElementById('cap-voice').classList.remove('on');
  setState('idle');
  remember(WAKE_PREF, false);
}

UI.btnWake.addEventListener('click', () => {
  if (Voice.wakeMode) disableWake();
  else enableWake();
});

UI.btnMute.addEventListener('click', () => {
  Voice.muted = !Voice.muted;
  UI.btnMute.textContent = Voice.muted ? 'VOICE OFF' : 'VOICE ON';
  UI.btnMute.classList.toggle('active', !Voice.muted);
});

UI.btnStop.addEventListener('click', () => {
  Voice.shutUp();
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'interrupt' }));
  }
  setState('idle');
});

UI.compose.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && UI.compose.value.trim()) {
    send(UI.compose.value.trim());
    UI.compose.value = '';
  }
});

document.getElementById('viewer').addEventListener('click', () => {
  document.getElementById('viewer').classList.remove('show');
});

document.getElementById('confirm-yes').addEventListener('click', () => answerConfirm(true));
document.getElementById('confirm-no').addEventListener('click', () => answerConfirm(false));

document.addEventListener('keydown', (event) => {
  const viewer = document.getElementById('viewer');
  if (event.key === 'Escape' && viewer.classList.contains('show')) {
    viewer.classList.remove('show');
    return;
  }
  if (pendingConfirm) {
    if (event.key === 'Enter') answerConfirm(true);
    if (event.key === 'Escape') answerConfirm(false);
    return;
  }
  // Space toggles push-to-talk unless the user is typing.
  if (event.code === 'Space' && document.activeElement !== UI.compose) {
    event.preventDefault();
    Mic.open().then(() => { Voice.shutUp(); Voice.trigger(); });
  }
});

// Capability lamps from the backend.
fetch('/api/status')
  .then((r) => r.json())
  .then((s) => {
    document.getElementById('cap-email').classList.toggle('on', s.capabilities.email);
    document.getElementById('cap-cal').classList.toggle('on', s.capabilities.calendar);
    document.getElementById('cap-search').classList.toggle('on', s.capabilities.search);
    document.getElementById('cap-image').classList.toggle('on', s.capabilities.images);
    document.getElementById('cap-3d').classList.toggle('on', s.capabilities.threed);
    UI.spend.textContent = `$${(s.spend.month_usd || 0).toFixed(4)} / mo`;
    UI.greeting.textContent = `online // ${s.user}`;
    Voice.name = s.user;

    // If wake mode was on last time and the mic is already permitted, come
    // back up listening. Chrome can refuse without a user gesture; if it
    // does, the button is still there.
    if (recall(WAKE_PREF)) {
      enableWake(false).then((ok) => {
        if (!ok) note('Click WAKE WORD to start listening again.');
      }).catch(() => {});
    }
    if (!s.capabilities.email) note('Gmail is not connected. Run setup_google.py to enable mail.');
    if (!s.capabilities.calendar) note('Calendar is not connected. Run setup_microsoft.py to enable it.');
  })
  .catch(() => {});

if (!Voice.supported) {
  note('Voice needs Chrome or Edge. You can still type.', 'error');
}
UI.btnMute.classList.add('active');
connect();
