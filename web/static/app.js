const $ = (id) => document.getElementById(id);

const draft = {
  temperature: 0.5,
  light: 0.55,
  volume: 0.5,
  humidity: 0.45,
  smell: "none",
  surface_touch: "none",
};

let live = { ...draft };
let socket = null;
let speaking = false;
let debounceTimer = 0;
let botBubble = null;
let lastState = null;
let cameraLumensSeq = null;
let screen = "main";
const PRIMARY_EMOTIONS = ["Neutral", "Joy", "Sadness", "Fear", "Anger", "Surprise", "Disgust"];
const SCREEN_META = {
  main: {
    title: "Emotion by Interruption",
    eyebrow: "Sentient AI · Individual_001",
    lede: "Draft values. Apply while the bot is mid-sentence to interrupt it.",
  },
  edit: {
    title: "Edit Personality",
    eyebrow: "Personality programming",
    lede: "Drag the colored threshold line to resize bands. Emotion dropdowns follow the new ranges.",
  },
  simulation: {
    title: "Simulation",
    eyebrow: "Canonical happy path · Neutral environment",
    lede: "Insert a sensor value between two words, then apply EBI-1.",
  },
  memory: {
    title: "Memory",
    eyebrow: "Everything heard, said and seen",
    lede: "Draft values. Apply while the bot is mid-sentence to interrupt it.",
  },
};

const sim = {
  messages: [],
  selected: null,
  after: null,
};

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${proto}://${location.host}/ws/chat`);
  socket.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "token") appendBot(msg.text);
    if (msg.type === "audio" && voice.settings.on) playPcm(msg.audio, msg.rate);
    if (msg.type === "interrupt") {
      stopVoice();
      addInterrupt(msg.interrupt);
      botBubble = null;
    }
    if (msg.state) renderState(msg.state);
    if (msg.type === "error") {
      addSystem(msg.message);
      setSpeaking(false);
    }
    if (msg.type === "done") {
      setSpeaking(false);
      botBubble = null;
      refreshPreview();
      sendSpeechAfterReply();
    }
  };
  socket.onclose = () => {
    setTimeout(connect, 800);
  };
}

function sensorsPayload() {
  return {
    temperature: Number(draft.temperature),
    light: Number(draft.light),
    volume: Number(draft.volume),
    humidity: Number(draft.humidity),
    smell: draft.smell,
    surface_touch: draft.surface_touch,
  };
}

function formatSide(side, title) {
  const s = side.sensors;
  const l = side.labels;
  const lines = [
    `${title}`,
    `Emotion: ${side.emotion.name} (${side.emotion.intensity.toFixed(2)})`,
    `Temperature: ${s.temperature.toFixed(2)} (${l.touch})`,
    `Lumens: ${s.light.toFixed(2)} (${l.vision})`,
    `Volume: ${s.volume.toFixed(2)} (${l.hearing})`,
    `Humidity: ${s.humidity.toFixed(2)} (${l.humidity})`,
    `Smell: ${l.smell}`,
    `Surface: ${l.surface || s.surface_touch}`,
    "",
    side.will_interrupt ? "INTERRUPT" : "NO INTERRUPT",
    side.utterance,
  ];
  if (side.reply) {
    lines.push("", "REPLY", side.reply);
  } else {
    lines.push("", "REPLY", "(Click Preview replies to generate the next spoken reply.)");
  }
  return lines.join("\n");
}

function renderPreview(bundle) {
  $("beforeBox").value = formatSide(bundle.before, "BEFORE applying selectors");
  $("afterBox").value = formatSide(bundle.after, "AFTER applying selectors");
}

const BAND_EMOTIONS = {
  touch: { cold: "Sadness", normal: "Neutral", hot: "Anger" },
  vision: { too_dark: "Fear", normal: "Neutral", too_bright: "Surprise" },
  hearing: { low: "Surprise", normal: "Neutral", loud: "Anger" },
  humidity: { dry: "Sadness", normal: "Neutral", wet: "Disgust" },
};

const TRIGGER_NAMES = {
  touch: { cold: "temperature cold", hot: "temperature hot", normal: "temperature normal" },
  vision: { too_dark: "light too dark", too_bright: "light too bright", normal: "light normal" },
  hearing: { low: "volume quiet", loud: "volume loud", normal: "volume normal" },
  humidity: { dry: "air dry", wet: "air wet", normal: "air normal" },
  smell: { flowers: "smell flowers", rotten_eggs: "smell rotten eggs", none: "smell clear" },
  surface: {
    soft: "surface soft",
    hard: "surface hard",
    cold: "surface cold",
    hot: "surface hot",
    none: "surface none",
  },
};

function triggerLabel(trigger) {
  if (!trigger) return "";
  const names = TRIGGER_NAMES[trigger.sense] || {};
  return names[trigger.band] || `${trigger.sense} ${trigger.band}`;
}

function emotionMap(state) {
  return state?.emotion_map || BAND_EMOTIONS;
}

function emotionsList(state) {
  return state?.primary_emotions || PRIMARY_EMOTIONS;
}

function emotionPicker(sense, band, current, state) {
  if (screen !== "edit") {
    return `<i>Emotion:</i> ${current}`;
  }
  const options = emotionsList(state)
    .map((name) => `<option value="${name}"${name === current ? " selected" : ""}>${name}</option>`)
    .join("");
  return `<i>Emotion:</i><select class="emo-pick" data-sense="${sense}" data-band="${band}">${options}</select>`;
}

function paintTicks(state) {
  const mapping = emotionMap(state);
  document.querySelectorAll(".ticks").forEach((el) => {
    const kind = el.dataset.kind;
    const bands = state.bands?.[kind];
    if (!bands) return;
    layoutThreshold(kind, bands, mapping, state);
  });
  paintChoiceEmotions(state);
}

const BAND_NAMES = {
  touch: ["cold", "normal", "hot"],
  vision: ["too_dark", "normal", "too_bright"],
  hearing: ["low", "normal", "loud"],
  humidity: ["dry", "normal", "wet"],
};

function bandsFromCuts(kind, low, high) {
  const labels = BAND_NAMES[kind];
  return [
    { low: 0, high: low, label: labels[0] },
    { low: Number((low + 0.01).toFixed(4)), high: high, label: labels[1] },
    { low: Number((high + 0.01).toFixed(4)), high: 1, label: labels[2] },
  ];
}

function layoutThreshold(kind, bands, mapping, state) {
  const ticks = document.querySelector(`.ticks[data-kind="${kind}"]`);
  const track = document.querySelector(`.threshold-track[data-kind="${kind}"]`);
  const legend = document.querySelector(`.band-emos[data-kind="${kind}"]`);
  if (!ticks || !bands) return;
  const colors = ["#6e8aa8", "#7f9a62", "#d2653a"];
  const stops = bands.map((b, i) => `${colors[i % colors.length]} ${b.low * 100}% ${b.high * 100}%`);
  ticks.style.background = `linear-gradient(90deg, ${stops.join(", ")})`;
  if (track) {
    const lowPct = bands[0].high * 100;
    const highPct = bands[1].high * 100;
    track.querySelector('[data-cut="low"]').style.left = `${lowPct}%`;
    track.querySelector('[data-cut="high"]').style.left = `${highPct}%`;
    track.querySelector('[data-cut="low"]').title = bands[0].high.toFixed(2);
    track.querySelector('[data-cut="high"]').title = bands[1].high.toFixed(2);
  }
  if (!legend) return;
  const emos = mapping[kind] || {};
  legend.innerHTML = bands
    .map((b) => {
      const width = Math.max(8, (b.high - b.low) * 100);
      const name = emos[b.label] || "Neutral";
      return `<span class="band-emo" style="flex:${width}">${emotionPicker(kind, b.label, name, state)}</span>`;
    })
    .join("");
}

function paintChoiceEmotions(state) {
  const mapping = emotionMap(state);
  document.querySelectorAll(".emo-slot").forEach((slot) => {
    const sense = slot.dataset.sense;
    const band = slot.dataset.band;
    const name = mapping[sense]?.[band] || "Neutral";
    slot.innerHTML = emotionPicker(sense, band, name, state);
  });
}

function isDirty() {
  return ["temperature", "light", "volume", "humidity", "smell", "surface_touch"].some(
    (key) => String(draft[key]) !== String(live[key])
  );
}

function renderState(state) {
  applyLive(state);
  $("modelTag").textContent = state.model;
  setSpeaking(Boolean(state.streaming));
  paintTicks(state);
  renderVision(state.vision);
  renderSpeech(state.speech);
  renderVoice(state.voice);
}

function applyLive(state) {
  lastState = state;
  live = {
    temperature: state.sensors.temperature,
    light: state.sensors.light,
    volume: state.sensors.volume,
    humidity: state.sensors.humidity,
    smell: state.sensors.smell,
    surface_touch: state.sensors.surface_touch,
  };
  const lumensSeq = state.vision?.lumens_seq;
  if (lumensSeq != null && lumensSeq !== cameraLumensSeq) {
    cameraLumensSeq = lumensSeq;
    draft.light = Number(live.light);
    $("light").value = draft.light;
    $("val-light").textContent = draft.light.toFixed(2);
  }
  const emotion = state.emotion.name;
  const badge = $("emotionBadge");
  const trigger = triggerLabel(state.trigger);
  badge.textContent = `${emotion} ${state.emotion.intensity.toFixed(2)}`;
  if (trigger) badge.textContent += ` · ${trigger}`;
  badge.className = `emotion ${emotion}`;
  const pending = state.pending || [];
  badge.title = pending.length
    ? `Waiting to interrupt: ${pending.map((p) => triggerLabel(p)).join(", ")}`
    : "No pending sensations";
  $("dirtyTag").textContent = isDirty() ? "pending apply" : "synced";
  $("dirtyTag").className = isDirty() ? "pill live" : "pill idle";
}

const LIGHTING_NAMES = { too_dim: "too dim", normal: "normal", too_bright: "too bright" };
const VISION_POLL_MS = 2000;
let visionSeq = null;
let visionKey = "";

function setFact(id, text, warn) {
  const el = $(id);
  el.textContent = text;
  el.title = text;
  el.classList.toggle("warn", Boolean(warn));
}

function renderVision(vision) {
  if (!vision) return;
  const report = vision.report;
  const tag = $("visionTag");
  $("visionEnabled").checked = vision.enabled;
  $("visionDrive").checked = vision.drive_light;
  if (document.activeElement !== $("visionInterval")) $("visionInterval").value = vision.interval;
  if (!vision.enabled) {
    tag.textContent = "paused";
    tag.className = "pill idle";
  } else if (vision.interval <= 0) {
    tag.textContent = "auto off";
    tag.className = "pill idle";
  } else if (report?.error) {
    tag.textContent = "error";
    tag.className = "pill live";
  } else {
    tag.textContent = `every ${vision.interval}s`;
    tag.className = "pill idle";
  }
  if (!report) return;

  $("visionSummary").textContent = report.error || report.summary || "…";
  if (report.room_type !== "unknown") {
    setFact("visionRoom", report.room_type);
    setFact("visionPeople", report.people.length ? report.people.join("; ") : "Nobody in view");
    setFact("visionProps", report.room_properties.join(", ") || "—");
  }
  if (report.luminance != null) {
    const verdict = LIGHTING_NAMES[report.lighting] || report.lighting;
    const band = vision.light_band.replace("_", " ");
    const notes = report.lighting_notes ? ` — ${report.lighting_notes}` : "";
    const lumens = vision.drive_light && vision.lumens != null ? ` → Lumens ${vision.lumens.toFixed(2)}` : "";
    setFact(
      "visionLight",
      `${verdict} · measured ${report.luminance.toFixed(2)}${lumens} (${band})${notes}`,
      report.lighting === "too_dim" || report.lighting === "too_bright" || vision.light_band !== "normal"
    );
    if (report.seq !== visionSeq) {
      const img = $("visionFrame");
      img.src = `/api/vision/frame.jpg?seq=${report.seq}`;
      img.hidden = false;
    }
  }
  visionSeq = report.seq;
}

async function pollVision() {
  try {
    const state = await fetch("/api/state").then((r) => r.json());
    const v = state.vision;
    const key = `${v.report?.seq}|${v.enabled}|${v.drive_light}|${v.status}|${v.interval}|${state.speech?.status}|${state.voice?.status}`;
    if (key !== visionKey) {
      const fresh = v.report && v.report.seq !== visionSeq;
      visionKey = key;
      applyLive(state);
      renderVision(v);
      renderSpeech(state.speech);
      renderVoice(state.voice);
      if (fresh) refreshPreview();
    }
  } catch {
    /* server restarting; try again next tick */
  } finally {
    setTimeout(pollVision, VISION_POLL_MS);
  }
}

async function setVision(body) {
  try {
    const state = await postJSON("/api/vision", body);
    applyLive(state);
    renderVision(state.vision);
  } catch (err) {
    visionKey = "";
    addSystem(String(err.message || err));
  }
}

$("visionEnabled").addEventListener("change", (ev) => setVision({ enabled: ev.target.checked }));
$("visionDrive").addEventListener("change", (ev) => setVision({ drive_light: ev.target.checked }));
$("visionIntervalForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const raw = $("visionInterval").value.trim();
  if (raw === "") return;
  $("visionInterval").blur();
  setVision({ interval: Number(raw) });
});

function setSpeaking(on) {
  speaking = on;
  $("streamTag").textContent = on ? "speaking" : "idle";
  $("streamTag").className = on ? "pill live" : "pill idle";
  $("sendBtn").disabled = on;
}

function addMsg(role, text, extraClass) {
  const div = document.createElement("div");
  div.className = `msg ${role} ${extraClass || ""}`;
  div.innerHTML = `<span class="who">${role}</span>`;
  const body = document.createElement("span");
  body.textContent = text;
  div.appendChild(body);
  $("log").appendChild(div);
  $("log").scrollTop = $("log").scrollHeight;
  return body;
}

function addSystem(text) {
  addMsg("bot", text, "interrupt");
}

function addInterrupt(info) {
  const spoken = info?.spoken || "environment interrupted the sentence";
  addMsg("bot", spoken, "interrupt");
}

function appendBot(text) {
  if (!botBubble) botBubble = addMsg("bot", "");
  botBubble.textContent += text;
  $("log").scrollTop = $("log").scrollHeight;
}

function setSeg(id, value) {
  document.querySelectorAll(`#${id} button`).forEach((btn) => {
    btn.classList.toggle("on", btn.dataset.value === value);
  });
}

function readSliders() {
  ["temperature", "light", "volume", "humidity"].forEach((key) => {
    draft[key] = Number($(key).value);
    $(`val-${key}`).textContent = draft[key].toFixed(2);
  });
}

function writeSlidersFrom(src) {
  ["temperature", "light", "volume", "humidity"].forEach((key) => {
    $(key).value = src[key];
    $(`val-${key}`).textContent = Number(src[key]).toFixed(2);
    draft[key] = Number(src[key]);
  });
  draft.smell = src.smell;
  draft.surface_touch = src.surface_touch || "none";
  setSeg("smell", draft.smell);
  setSeg("surface", draft.surface_touch);
}

async function sendJSON(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: method === "DELETE" ? undefined : JSON.stringify(body || {}),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

function postJSON(url, body) {
  return sendJSON("POST", url, body);
}

async function refreshPreview() {
  const bundle = await postJSON("/api/draft", sensorsPayload());
  renderPreview(bundle);
  $("dirtyTag").textContent = isDirty() ? "pending apply" : "synced";
  $("dirtyTag").className = isDirty() ? "pill live" : "pill idle";
}

function queuePreview() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(refreshPreview, 120);
}

$("chatForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = $("message").value.trim();
  if (!text || speaking) return;
  if ($("applyOnSend").checked && isDirty()) {
    const applied = await postJSON("/api/apply?defer=true", sensorsPayload());
    renderState(applied.state);
  }
  addMsg("user", text);
  $("message").value = "";
  botBubble = null;
  setSpeaking(true);
  stopVoice();
  const spoken = voiceRequest();
  if (spoken) audioOut();
  socket.send(JSON.stringify({ type: "chat", text, voice: spoken }));
});

$("previewBtn").addEventListener("click", async () => {
  $("previewBtn").disabled = true;
  $("beforeBox").value = "Generating before/after replies...";
  $("afterBox").value = "Generating before/after replies...";
  try {
    const bundle = await postJSON("/api/preview/replies", {
      message: $("message").value.trim() || "What has your day been like?",
      sensors: sensorsPayload(),
    });
    renderPreview(bundle);
  } catch (err) {
    $("afterBox").value = String(err.message || err);
  } finally {
    $("previewBtn").disabled = false;
  }
});

$("applyBtn").addEventListener("click", async () => {
  const applied = await postJSON("/api/apply", sensorsPayload());
  renderState(applied.state);
  writeSlidersFrom(live);
  if (applied.interrupt) {
    addInterrupt(applied.interrupt);
    stopVoice();
    sayLine(applied.interrupt.spoken);
  }
  refreshPreview();
});

$("resetEnvBtn").addEventListener("click", async () => {
  const state = await postJSON("/api/reset/environment", {});
  renderState(state);
  writeSlidersFrom(live);
  refreshPreview();
});

$("resetChatBtn").addEventListener("click", async () => {
  await postJSON("/api/reset/conversation", {});
  stopVoice();
  $("log").innerHTML = "";
});

["temperature", "light", "volume", "humidity"].forEach((key) => {
  $(key).addEventListener("input", () => {
    readSliders();
    queuePreview();
  });
});

document.querySelectorAll("#smell button").forEach((btn) => {
  btn.addEventListener("click", () => {
    draft.smell = btn.dataset.value;
    setSeg("smell", draft.smell);
    queuePreview();
  });
});
document.querySelectorAll("#surface button").forEach((btn) => {
  btn.addEventListener("click", () => {
    draft.surface_touch = btn.dataset.value;
    setSeg("surface", draft.surface_touch);
    queuePreview();
  });
});

document.querySelectorAll(".menu [data-screen]").forEach((btn) => {
  btn.addEventListener("click", () => showScreen(btn.dataset.screen));
});

function showScreen(name) {
  screen = name;
  document.body.dataset.screen = name;
  document.querySelectorAll(".menu [data-screen]").forEach((btn) => {
    btn.classList.toggle("on", btn.dataset.screen === name);
  });
  const meta = SCREEN_META[name] || SCREEN_META.main;
  $("screenTitle").textContent = meta.title;
  $("screenEyebrow").textContent = meta.eyebrow;
  $("screenLede").textContent = meta.lede;
  if (name === "simulation") {
    renderSimulation();
  }
  if (name === "memory") {
    loadMemories().catch((err) => showMemError(err));
  }
  if (lastState) {
    paintTicks(lastState);
    paintChoiceEmotions(lastState);
  }
}

document.addEventListener("change", async (ev) => {
  const sel = ev.target.closest("select.emo-pick");
  if (!sel) return;
  try {
    const state = await postJSON("/api/emotion-map", {
      sense: sel.dataset.sense,
      band: sel.dataset.band,
      emotion: sel.value,
    });
    renderState(state);
    refreshPreview();
  } catch (err) {
    addSystem(String(err.message || err));
  }
});

$("resetMapBtn").addEventListener("click", async () => {
  const state = await postJSON("/api/emotion-map/reset", {});
  renderState(state);
  refreshPreview();
});

$("resetThreshBtn").addEventListener("click", async () => {
  const state = await postJSON("/api/thresholds/reset", {});
  renderState(state);
  refreshPreview();
});

const MIN_GAP = 0.12;
let drag = null;

function pointerToValue(track, clientX) {
  const rect = track.getBoundingClientRect();
  if (rect.width <= 0) return 0;
  return Math.max(0.05, Math.min(0.95, (clientX - rect.left) / rect.width));
}

function currentCuts(kind) {
  const bands = lastState?.bands?.[kind];
  if (!bands) return { low: 0.3, high: 0.7 };
  return { low: bands[0].high, high: bands[1].high };
}

function applyLocalCuts(kind, low, high) {
  if (!lastState) return;
  if (!lastState.thresholds) lastState.thresholds = {};
  lastState.thresholds[kind] = { low, high };
  lastState.bands[kind] = bandsFromCuts(kind, low, high);
  layoutThreshold(kind, lastState.bands[kind], emotionMap(lastState), lastState);
}

document.querySelectorAll(".threshold-track").forEach((track) => {
  track.addEventListener("pointerdown", (ev) => {
    if (screen !== "edit") return;
    const kind = track.dataset.kind;
    const handle = ev.target.closest(".cut-handle");
    const cuts = currentCuts(kind);
    let which = handle?.dataset.cut;
    if (!which) {
      const value = pointerToValue(track, ev.clientX);
      which = Math.abs(value - cuts.low) <= Math.abs(value - cuts.high) ? "low" : "high";
    }
    drag = { kind, which, track };
    track.setPointerCapture(ev.pointerId);
    ev.preventDefault();
  });
  track.addEventListener("pointermove", (ev) => {
    if (!drag || drag.track !== track) return;
    const cuts = currentCuts(drag.kind);
    let value = pointerToValue(track, ev.clientX);
    if (drag.which === "low") value = Math.min(value, cuts.high - MIN_GAP);
    else value = Math.max(value, cuts.low + MIN_GAP);
    const next = drag.which === "low" ? { low: value, high: cuts.high } : { low: cuts.low, high: value };
    applyLocalCuts(drag.kind, next.low, next.high);
  });
  const finish = async (ev) => {
    if (!drag || drag.track !== track) return;
    const kind = drag.kind;
    drag = null;
    const cuts = currentCuts(kind);
    try {
      const state = await postJSON("/api/thresholds", {
        sense: kind,
        low: cuts.low,
        high: cuts.high,
      });
      renderState(state);
      refreshPreview();
    } catch (err) {
      addSystem(String(err.message || err));
    }
  };
  track.addEventListener("pointerup", finish);
  track.addEventListener("pointercancel", finish);
});

const LAYOUT_KEY = "sentient-layout";
const LAYOUT_DEFAULTS = { menu: 176, chat: 50, env: 66, before: 50, sim: 50 };

function loadLayout() {
  try {
    return { ...LAYOUT_DEFAULTS, ...JSON.parse(localStorage.getItem(LAYOUT_KEY) || "{}") };
  } catch {
    return { ...LAYOUT_DEFAULTS };
  }
}

function saveLayout(sizes) {
  localStorage.setItem(LAYOUT_KEY, JSON.stringify(sizes));
}

function applyLayout(sizes) {
  const chat = Number(sizes.chat);
  const env = Number(sizes.env);
  const before = Number(sizes.before);
  const simSplit = Number(sizes.sim);
  const root = document.documentElement.style;
  root.setProperty("--menu-w", `${sizes.menu}px`);
  root.setProperty("--chat-track", `${chat}fr`);
  root.setProperty("--side-track", `${Math.max(1, 100 - chat)}fr`);
  root.setProperty("--env-track", `${env}fr`);
  root.setProperty("--preview-track", `${Math.max(1, 100 - env)}fr`);
  root.setProperty("--before-track", `${before}fr`);
  root.setProperty("--after-track", `${Math.max(1, 100 - before)}fr`);
  root.setProperty("--sim-left-track", `${simSplit}fr`);
  root.setProperty("--sim-right-track", `${Math.max(1, 100 - simSplit)}fr`);

  const shell = document.querySelector(".shell");
  const layout = document.querySelector(".layout");
  const side = document.querySelector(".side");
  const preview = document.querySelector(".preview-grid");
  const simLayout = document.querySelector(".sim-layout");
  if (shell) shell.style.gridTemplateColumns = `${sizes.menu}px 8px minmax(0, 1fr)`;
  if (layout) layout.style.gridTemplateColumns = `minmax(0, ${chat}fr) 8px minmax(0, ${Math.max(1, 100 - chat)}fr)`;
  if (side) side.style.gridTemplateRows = `minmax(0, ${env}fr) 8px minmax(0, ${Math.max(1, 100 - env)}fr)`;
  if (preview) preview.style.gridTemplateColumns = `minmax(0, ${before}fr) 8px minmax(0, ${Math.max(1, 100 - before)}fr)`;
  if (simLayout) {
    simLayout.style.gridTemplateColumns = `minmax(0, ${simSplit}fr) 8px minmax(0, ${Math.max(1, 100 - simSplit)}fr)`;
  }
}

function initSplitters() {
  const sizes = loadLayout();
  applyLayout(sizes);

  const specs = {
    menu: {
      axis: "x",
      key: "menu",
      min: 120,
      max: 280,
      parent: () => document.querySelector(".shell"),
      read: (ev, rect) => ev.clientX - rect.left,
      write: (px) => {
        sizes.menu = Math.round(Math.min(specs.menu.max, Math.max(specs.menu.min, px)));
        applyLayout(sizes);
      },
    },
    chat: {
      axis: "x",
      key: "chat",
      min: 22,
      max: 78,
      parent: () => document.querySelector(".layout"),
      read: (ev, rect) => ((ev.clientX - rect.left) / rect.width) * 100,
      write: (pct) => {
        sizes.chat = Math.round(Math.min(specs.chat.max, Math.max(specs.chat.min, pct)));
        applyLayout(sizes);
      },
    },
    env: {
      axis: "y",
      key: "env",
      min: 22,
      max: 82,
      parent: () => document.querySelector(".side"),
      read: (ev, rect) => ((ev.clientY - rect.top) / rect.height) * 100,
      write: (pct) => {
        sizes.env = Math.round(Math.min(specs.env.max, Math.max(specs.env.min, pct)));
        applyLayout(sizes);
      },
    },
    preview: {
      axis: "x",
      key: "before",
      min: 20,
      max: 80,
      parent: () => document.querySelector(".preview-grid"),
      read: (ev, rect) => ((ev.clientX - rect.left) / rect.width) * 100,
      write: (pct) => {
        sizes.before = Math.round(Math.min(specs.preview.max, Math.max(specs.preview.min, pct)));
        applyLayout(sizes);
      },
    },
    sim: {
      axis: "x",
      key: "sim",
      min: 22,
      max: 78,
      parent: () => document.querySelector(".sim-layout"),
      read: (ev, rect) => ((ev.clientX - rect.left) / rect.width) * 100,
      write: (pct) => {
        sizes.sim = Math.round(Math.min(specs.sim.max, Math.max(specs.sim.min, pct)));
        applyLayout(sizes);
      },
    },
  };

  let drag = null;

  const onMove = (ev) => {
    if (!drag || ev.pointerId !== drag.pointerId) return;
    const parent = drag.spec.parent();
    if (!parent) return;
    drag.spec.write(drag.spec.read(ev, parent.getBoundingClientRect()));
  };

  const onUp = (ev) => {
    if (!drag || ev.pointerId !== drag.pointerId) return;
    try {
      drag.handle.releasePointerCapture(ev.pointerId);
    } catch {
      /* capture already released */
    }
    document.body.classList.remove("resizing-col", "resizing-row");
    saveLayout(sizes);
    drag = null;
  };

  window.addEventListener("pointermove", onMove, true);
  window.addEventListener("pointerup", onUp, true);
  window.addEventListener("pointercancel", onUp, true);

  document.querySelectorAll("[data-split]").forEach((handle) => {
    const spec = specs[handle.dataset.split];
    if (!spec) return;
    handle.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      const parent = spec.parent();
      if (!parent) return;
      ev.preventDefault();
      ev.stopPropagation();
      drag = { spec, handle, pointerId: ev.pointerId };
      document.body.classList.add(spec.axis === "x" ? "resizing-col" : "resizing-row");
      try {
        handle.setPointerCapture(ev.pointerId);
      } catch {
        /* Opera/older Chromium: window listeners still track the drag */
      }
    });
    handle.addEventListener("dblclick", (ev) => {
      ev.preventDefault();
      sizes[spec.key] = LAYOUT_DEFAULTS[spec.key];
      applyLayout(sizes);
      saveLayout(sizes);
    });
  });
}

function tokenizeText(text) {
  return String(text || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
}

function cloneSimMessages(messages) {
  return (messages || []).map((msg) => ({
    role: msg.role,
    text: msg.text,
    words: tokenizeText(msg.text),
    tag: msg.tag ? { ...msg.tag } : null,
  }));
}

function simTagDisplay(tag) {
  if (!tag) return "";
  return `[${tag.sensor} = ${tag.value}]`;
}

function renderPlainLog(el, messages) {
  el.innerHTML = "";
  (messages || []).forEach((msg) => {
    const div = document.createElement("div");
    div.className = `msg ${msg.role}`;
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = msg.role;
    const body = document.createElement("span");
    body.textContent = msg.text;
    div.appendChild(who);
    div.appendChild(body);
    el.appendChild(div);
  });
}

function renderSimPlay() {
  const el = $("simPlay");
  el.innerHTML = "";
  sim.messages.forEach((msg, msgIndex) => {
    const div = document.createElement("div");
    div.className = `msg ${msg.role}`;
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = msg.role;
    const body = document.createElement("span");
    const words = msg.words || tokenizeText(msg.text);
    words.forEach((word, wordIndex) => {
      if (wordIndex > 0) {
        const gapIndex = wordIndex - 1;
        const gap = document.createElement("button");
        gap.type = "button";
        gap.className = "sim-gap";
        gap.dataset.msg = String(msgIndex);
        gap.dataset.gap = String(gapIndex);
        gap.title = msg.role === "bot" ? `space #${gapIndex + 1}` : "EBI-1 only fires on bot lines";
        const selected = sim.selected && sim.selected.msgIndex === msgIndex && sim.selected.gap === gapIndex;
        const tagged = msg.tag && msg.tag.gap === gapIndex;
        if (tagged) {
          gap.classList.add("has-tag");
          gap.textContent = ` ${simTagDisplay(msg.tag)} `;
        } else {
          gap.textContent = " ";
        }
        if (selected) gap.classList.add("selected");
        if (msg.role === "bot") {
          gap.addEventListener("click", () => {
            if (
              msg.tag &&
              msg.tag.gap === gapIndex &&
              sim.selected &&
              sim.selected.msgIndex === msgIndex &&
              sim.selected.gap === gapIndex
            ) {
              msg.tag = null;
              sim.selected = null;
              sim.after = null;
              renderSimulation();
              return;
            }
            insertSimTag(msgIndex, gapIndex);
          });
        } else {
          gap.disabled = true;
        }
        body.appendChild(gap);
      }
      const token = document.createElement("span");
      token.className = "sim-word";
      token.textContent = word;
      body.appendChild(token);
    });
    div.appendChild(who);
    div.appendChild(body);
    el.appendChild(div);
  });
}

function renderSimAfter(bundle) {
  const panel = $("simAfter");
  const log = $("simAfterLog");
  const meta = $("simAfterMeta");
  if (!bundle) {
    panel.hidden = true;
    log.innerHTML = "";
    meta.textContent = "";
    return;
  }
  panel.hidden = false;
  log.innerHTML = "";
  const hits = [];
  (bundle.applied || []).forEach((msg) => {
    const div = document.createElement("div");
    div.className = `msg ${msg.role}`;
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = msg.role;
    const body = document.createElement("span");
    body.textContent = msg.text;
    div.appendChild(who);
    div.appendChild(body);
    if (msg.eliminated) {
      const note = document.createElement("span");
      note.className = "sim-note";
      note.textContent = `Eliminated: "${msg.eliminated}"`;
      div.appendChild(note);
    }
    if (msg.interrupt) {
      const cut = document.createElement("span");
      cut.className = "sim-cut";
      const spaceLabel = msg.cut_space == null ? "—" : `#${msg.cut_space}`;
      const label = triggerLabel(msg.interrupt);
      cut.textContent = `Cut at remainder space ${spaceLabel} · ${label} · ${msg.interrupt.emotion}`;
      div.appendChild(cut);
      hits.push(`${label} → ${msg.interrupt.emotion}`);
    }
    log.appendChild(div);
  });
  meta.textContent = hits.length ? hits.join(" · ") : "no interruption";
}

function renderSimulation() {
  renderPlainLog($("simCanonical"), sim.messages.map((msg) => ({ role: msg.role, text: msg.text })));
  renderSimPlay();
  renderSimAfter(sim.after);
}

function currentSimTag() {
  return {
    sensor: $("simSensor").value,
    value: $("simValue").value.trim(),
  };
}

function insertSimTag(msgIndex, gap) {
  const { sensor, value } = currentSimTag();
  if (!value) return;
  sim.messages.forEach((msg, index) => {
    if (index !== msgIndex) return;
    msg.tag = { gap, sensor, value };
  });
  sim.selected = { msgIndex, gap };
  sim.after = null;
  renderSimulation();
}

function randomBotGap() {
  const options = [];
  sim.messages.forEach((msg, msgIndex) => {
    if (msg.role !== "bot") return;
    const words = msg.words || tokenizeText(msg.text);
    for (let gap = 0; gap < words.length - 1; gap += 1) {
      options.push({ msgIndex, gap });
    }
  });
  if (!options.length) return null;
  return options[Math.floor(Math.random() * options.length)];
}

async function loadCanonical() {
  const data = await fetch("/api/simulation/canonical").then((r) => r.json());
  sim.messages = cloneSimMessages(data.messages);
  sim.selected = null;
  sim.after = null;
  renderSimulation();
}

function initSimulation() {
  $("simInsertBtn").addEventListener("click", () => {
    if (!sim.selected) return;
    insertSimTag(sim.selected.msgIndex, sim.selected.gap);
  });
  $("simRandomBtn").addEventListener("click", () => {
    const picked = randomBotGap();
    if (!picked) return;
    insertSimTag(picked.msgIndex, picked.gap);
  });
  $("simResetBtn").addEventListener("click", () => {
    loadCanonical();
  });
  $("simApplyBtn").addEventListener("click", async () => {
    const tagged = sim.messages.some((msg) => msg.role === "bot" && msg.tag);
    if (!tagged) {
      const picked = randomBotGap();
      if (picked) insertSimTag(picked.msgIndex, picked.gap);
    }
    $("simApplyBtn").disabled = true;
    $("simAfter").hidden = false;
    $("simAfterMeta").textContent = "applying EBI-1…";
    $("simAfterLog").innerHTML = "";
    try {
      const bundle = await postJSON("/api/simulation/apply", {
        messages: sim.messages.map((msg) => ({
          role: msg.role,
          text: msg.text,
          tag: msg.tag,
        })),
      });
      sim.after = bundle;
      renderSimAfter(bundle);
    } catch (err) {
      $("simAfterMeta").textContent = String(err.message || err);
    } finally {
      $("simApplyBtn").disabled = false;
    }
  });
  $("simSensor").addEventListener("change", () => {
    const hints = {
      temp: "011",
      light: "090",
      volume: "092",
      humidity: "088",
      smell: "rotten_eggs",
      surface: "cold",
    };
    const next = hints[$("simSensor").value];
    if (next && !$("simValue").value) $("simValue").value = next;
  });
  loadCanonical();
}

const MEMORY_POLL_MS = 3000;
const MEMORY_PAGE = 100;
const MEMORY_LABELS = { user: "You", bot: "Bot", interrupt: "Interrupt", vision: "Vision", note: "Added" };

const mem = {
  items: [],
  version: null,
  filter: "all",
  query: "",
  limit: MEMORY_PAGE,
  editing: null,
};

function readDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error("Could not read the picture."));
    reader.readAsDataURL(file);
  });
}

function showMemError(err) {
  const el = $("memFormError");
  el.textContent = err ? String(err.message || err) : "";
  el.hidden = !err;
}

function memoryLabel(item) {
  return MEMORY_LABELS[item.kind === "chat" ? item.role : item.kind] || item.kind;
}

function memoryImageUrl(item) {
  return `/api/memory/${item.id}/image?v=${item.updated_at || item.created_at}`;
}

function memoryFacts(item) {
  const meta = item.meta || {};
  if (item.kind !== "vision") return "";
  const parts = [];
  if (meta.room_type && meta.room_type !== "unknown") parts.push(meta.room_type);
  if (meta.people) parts.push(meta.people.length === 1 ? "1 person" : `${meta.people.length} people`);
  if (meta.lighting && meta.lighting !== "unknown") parts.push(`lighting ${LIGHTING_NAMES[meta.lighting] || meta.lighting}`);
  if (meta.luminance != null) parts.push(`luminance ${Number(meta.luminance).toFixed(2)}`);
  return parts.join(" · ");
}

function filteredMemories() {
  const q = mem.query.trim().toLowerCase();
  return mem.items
    .filter((item) => mem.filter === "all" || item.kind === mem.filter)
    .filter((item) => !q || item.text.toLowerCase().includes(q))
    .reverse();
}

function memoryButton(label, action, ghost) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = label;
  btn.dataset.action = action;
  if (ghost) btn.className = "ghost";
  return btn;
}

function memoryEditor(item) {
  const wrap = document.createElement("div");
  wrap.className = "mem-editor";
  const text = document.createElement("textarea");
  text.className = "mem-edit-text";
  text.rows = 4;
  text.value = item.text;
  const file = document.createElement("label");
  file.className = "mem-edit-file";
  file.textContent = item.image ? "Replace picture" : "Add picture";
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/jpeg,image/png,image/webp,image/gif";
  input.className = "mem-edit-image";
  file.appendChild(input);
  wrap.append(text, file);
  if (item.image) {
    const remove = document.createElement("label");
    remove.className = "check";
    remove.innerHTML = '<input type="checkbox" class="mem-edit-remove" /> Remove picture';
    wrap.appendChild(remove);
  }
  const actions = document.createElement("div");
  actions.className = "mem-actions";
  actions.append(memoryButton("Save", "save"), memoryButton("Cancel", "cancel", true));
  const error = document.createElement("p");
  error.className = "mem-error";
  error.hidden = true;
  wrap.append(actions, error);
  return wrap;
}

function memoryCard(item) {
  const card = document.createElement("article");
  card.className = `mem-card ${item.kind} ${item.role || ""}`;
  card.dataset.id = item.id;

  const head = document.createElement("header");
  const tag = document.createElement("span");
  tag.className = "mem-tag";
  tag.textContent = memoryLabel(item);
  const when = document.createElement("time");
  when.textContent = new Date(item.created_at * 1000).toLocaleString();
  if (item.updated_at) when.textContent += " · edited";
  head.append(tag, when);
  card.appendChild(head);

  if (item.image) {
    const link = document.createElement("a");
    link.href = memoryImageUrl(item);
    link.target = "_blank";
    link.rel = "noopener";
    const img = document.createElement("img");
    img.className = "mem-photo";
    img.loading = "lazy";
    img.src = link.href;
    img.alt = item.text || "Memory picture";
    link.appendChild(img);
    card.appendChild(link);
  }

  if (mem.editing === item.id) {
    card.appendChild(memoryEditor(item));
    return card;
  }
  if (item.text) {
    const body = document.createElement("p");
    body.className = "mem-text";
    body.textContent = item.text;
    card.appendChild(body);
  }
  const facts = memoryFacts(item);
  if (facts) {
    const line = document.createElement("p");
    line.className = "mem-facts";
    line.textContent = facts;
    card.appendChild(line);
  }
  const actions = document.createElement("div");
  actions.className = "mem-actions";
  actions.append(memoryButton("Edit", "edit", true), memoryButton("Delete", "delete", true));
  card.appendChild(actions);
  return card;
}

function renderMemories() {
  const list = $("memList");
  const shown = filteredMemories();
  list.innerHTML = "";
  shown.slice(0, mem.limit).forEach((item) => list.appendChild(memoryCard(item)));
  if (!shown.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = mem.items.length ? "No memories match." : "No memories yet.";
    list.appendChild(empty);
  }
  $("memMoreBtn").hidden = shown.length <= mem.limit;
  const total = mem.items.length;
  $("memCount").textContent = shown.length === total ? `${total} memories` : `${shown.length} of ${total}`;
}

async function loadMemories() {
  const data = await fetch("/api/memory").then((r) => r.json());
  if (data.version === mem.version) return;
  mem.version = data.version;
  mem.items = data.items;
  if (!mem.editing) renderMemories();
}

async function pollMemories() {
  if (screen === "memory") {
    try {
      await loadMemories();
    } catch {
      /* server restarting; try again next tick */
    }
  }
  setTimeout(pollMemories, MEMORY_POLL_MS);
}

function replaceMemory(item) {
  mem.items = mem.items.map((old) => (old.id === item.id ? item : old));
}

async function saveMemory(card, id) {
  const error = card.querySelector(".mem-editor .mem-error");
  const body = { text: card.querySelector(".mem-edit-text").value };
  const file = card.querySelector(".mem-edit-image").files[0];
  try {
    if (file) body.image = await readDataUrl(file);
    else if (card.querySelector(".mem-edit-remove")?.checked) body.remove_image = true;
    replaceMemory(await sendJSON("PATCH", `/api/memory/${id}`, body));
    mem.editing = null;
    renderMemories();
  } catch (err) {
    error.textContent = String(err.message || err);
    error.hidden = false;
  }
}

async function deleteMemory(id) {
  if (!confirm("Delete this memory? This cannot be undone.")) return;
  try {
    await sendJSON("DELETE", `/api/memory/${id}`);
    mem.items = mem.items.filter((item) => item.id !== id);
    if (mem.editing === id) mem.editing = null;
    renderMemories();
  } catch (err) {
    alert(String(err.message || err));
  }
}

function clearMemoryForm() {
  $("memText").value = "";
  $("memImage").value = "";
  $("memPreview").hidden = true;
  $("memPreview").removeAttribute("src");
  showMemError(null);
}

function initMemory() {
  $("memImage").addEventListener("change", async () => {
    const file = $("memImage").files[0];
    if (!file) {
      $("memPreview").hidden = true;
      return;
    }
    $("memPreview").src = await readDataUrl(file);
    $("memPreview").hidden = false;
  });

  $("memClearBtn").addEventListener("click", clearMemoryForm);

  $("memForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const file = $("memImage").files[0];
    const body = { text: $("memText").value };
    $("memAddBtn").disabled = true;
    try {
      if (file) body.image = await readDataUrl(file);
      mem.items.push(await postJSON("/api/memory", body));
      clearMemoryForm();
      renderMemories();
      $("memList").scrollTop = 0;
    } catch (err) {
      showMemError(err);
    } finally {
      $("memAddBtn").disabled = false;
    }
  });

  document.querySelectorAll("#memFilter button").forEach((btn) => {
    btn.addEventListener("click", () => {
      mem.filter = btn.dataset.value;
      mem.limit = MEMORY_PAGE;
      setSeg("memFilter", mem.filter);
      renderMemories();
    });
  });

  $("memSearch").addEventListener("input", () => {
    mem.query = $("memSearch").value;
    mem.limit = MEMORY_PAGE;
    renderMemories();
  });

  $("memMoreBtn").addEventListener("click", () => {
    mem.limit += MEMORY_PAGE;
    renderMemories();
  });

  $("memList").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-action]");
    const card = btn?.closest(".mem-card");
    if (!card) return;
    const id = card.dataset.id;
    if (btn.dataset.action === "edit") {
      mem.editing = id;
      renderMemories();
      $("memList").querySelector(`[data-id="${id}"] .mem-edit-text`)?.focus();
    } else if (btn.dataset.action === "cancel") {
      mem.editing = null;
      renderMemories();
    } else if (btn.dataset.action === "save") {
      saveMemory(card, id);
    } else if (btn.dataset.action === "delete") {
      deleteMemory(id);
    }
  });

  pollMemories();
}

const SPEECH_RATE = 16000;
const SPEECH_TICK_MS = 50;
const SPEECH_MIN_VOICE_MS = 250;
const SPEECH_SILENCE_MS = 1500;
const SPEECH_NO_VOICE_MS = 8000;
const SPEECH_MAX_MS = 120000;
const SPEECH_FLOOR_TICKS = 60;
const MESSAGE_PLACEHOLDER = $("message").placeholder;

const mic = {
  status: "stopped",
  error: null,
  phase: "idle",
  stream: null,
  ctx: null,
  recorder: null,
  timer: 0,
  startedAt: 0,
  lastVoiceAt: 0,
  voicedMs: 0,
  levels: [],
  sendAfterReply: false,
};

function micSupported() {
  return Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
}

function micTitle() {
  if (mic.phase === "listening") return "Listening. Pause when you're done, or click to stop now.";
  if (mic.phase === "transcribing") return "Turning your speech into text…";
  if (!micSupported()) return "The browser only allows the microphone on http://127.0.0.1, localhost, or HTTPS.";
  if (mic.status === "ready") return "Talk to the bot. Listening stops when you pause.";
  if (mic.status === "error") return mic.error || "The speech model failed to load.";
  return "Loading the speech model… (the first run downloads about 2.5 GB)";
}

function paintMic() {
  const btn = $("micBtn");
  btn.classList.toggle("listening", mic.phase === "listening");
  btn.classList.toggle("busy", mic.phase === "transcribing");
  btn.disabled = mic.phase === "idle" ? !(mic.status === "ready" && micSupported()) : mic.phase !== "listening";
  btn.title = micTitle();
  $("micLabel").textContent = { listening: "Stop", transcribing: "Hearing…" }[mic.phase] || "Speak";
  if (mic.phase !== "listening") btn.style.removeProperty("--level");
}

function renderSpeech(speech) {
  if (!speech) return;
  mic.status = speech.status;
  mic.error = speech.error;
  $("micBtn").hidden = !speech.enabled;
  $("micAutoSendLabel").hidden = !speech.enabled;
  paintMic();
}

function micHint(text) {
  $("message").placeholder = text || MESSAGE_PLACEHOLDER;
}

function micErrorText(err) {
  if (err?.name === "NotAllowedError") {
    return "Microphone access is blocked. Allow it for this page in the browser's site settings, then click Speak again.";
  }
  if (err?.name === "NotFoundError") return "No microphone found. Plug one in, or pick one in the browser's site settings.";
  if (err?.name === "NotReadableError") return "The microphone is in use by another app. Close it there and try again.";
  return `Microphone error: ${err?.message || err}`;
}

function releaseMic() {
  clearInterval(mic.timer);
  mic.stream?.getTracks().forEach((track) => track.stop());
  mic.ctx?.close();
  mic.stream = null;
  mic.ctx = null;
}

async function openMic() {
  mic.stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  mic.ctx = new AudioContext();
  await mic.ctx.resume();
  const analyser = mic.ctx.createAnalyser();
  analyser.fftSize = 2048;
  mic.ctx.createMediaStreamSource(mic.stream).connect(analyser);

  const recorder = new MediaRecorder(mic.stream, { audioBitsPerSecond: 128000 });
  const chunks = [];
  const startedAt = performance.now();
  recorder.ondataavailable = (ev) => {
    if (ev.data.size) chunks.push(ev.data);
  };
  recorder.onstop = () => {
    releaseMic();
    transcribeRecording(new Blob(chunks, { type: recorder.mimeType }), performance.now() - startedAt);
  };
  recorder.start();

  Object.assign(mic, { recorder, startedAt, lastVoiceAt: startedAt, voicedMs: 0, levels: [], phase: "listening" });
  const frame = new Float32Array(analyser.fftSize);
  mic.timer = setInterval(() => listenTick(analyser, frame), SPEECH_TICK_MS);
}

async function startListening() {
  stopVoice();
  mic.phase = "opening";
  paintMic();
  try {
    await openMic();
    micHint("Listening… pause when you're done, or click Stop.");
  } catch (err) {
    releaseMic();
    mic.phase = "idle";
    addSystem(micErrorText(err));
  }
  paintMic();
}

function stopListening() {
  if (mic.phase === "listening" && mic.recorder?.state === "recording") mic.recorder.stop();
}

function listenTick(analyser, frame) {
  analyser.getFloatTimeDomainData(frame);
  let sum = 0;
  for (let i = 0; i < frame.length; i += 1) sum += frame[i] * frame[i];
  const rms = Math.sqrt(sum / frame.length);

  // Background level = quietest tenth of the last few seconds, so the cut-off adapts to the room.
  mic.levels.push(rms);
  if (mic.levels.length > SPEECH_FLOOR_TICKS) mic.levels.shift();
  const floor = [...mic.levels].sort((a, b) => a - b)[Math.floor(mic.levels.length / 10)];
  const now = performance.now();
  if (rms > Math.max(0.008, floor * 3)) {
    mic.voicedMs += SPEECH_TICK_MS;
    mic.lastVoiceAt = now;
  }
  const db = 20 * Math.log10(rms + 1e-6);
  $("micBtn").style.setProperty("--level", Math.min(1, Math.max(0, (db + 60) / 45)).toFixed(2));

  const heard = mic.voicedMs >= SPEECH_MIN_VOICE_MS;
  const elapsed = now - mic.startedAt;
  if (
    (heard && now - mic.lastVoiceAt >= SPEECH_SILENCE_MS) ||
    (!heard && elapsed >= SPEECH_NO_VOICE_MS) ||
    elapsed >= SPEECH_MAX_MS
  ) {
    stopListening();
  }
}

function wavFile(samples, rate) {
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const ascii = (offset, text) => [...text].forEach((ch, i) => view.setUint8(offset + i, ch.charCodeAt(0)));
  ascii(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  ascii(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((x, i) => view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, x)) * 0x7fff, true));
  return view.buffer;
}

async function speechWav(blob) {
  // Decoding through a 16 kHz context resamples to the rate the speech model expects.
  const decoded = await new OfflineAudioContext(1, 1, SPEECH_RATE).decodeAudioData(await blob.arrayBuffer());
  const samples = new Float32Array(decoded.length);
  for (let c = 0; c < decoded.numberOfChannels; c += 1) {
    const data = decoded.getChannelData(c);
    for (let i = 0; i < samples.length; i += 1) samples[i] += data[i] / decoded.numberOfChannels;
  }
  return wavFile(samples, SPEECH_RATE);
}

function useTranscript(text) {
  const box = $("message");
  box.value = box.value.trim() ? `${box.value.trimEnd()} ${text}` : text;
  if (!$("micAutoSend").checked) box.focus();
  else if (speaking) mic.sendAfterReply = true;
  else $("chatForm").requestSubmit();
}

function sendSpeechAfterReply() {
  if (!mic.sendAfterReply) return;
  mic.sendAfterReply = false;
  if ($("micAutoSend").checked && $("message").value.trim()) $("chatForm").requestSubmit();
}

async function transcribeRecording(blob, durationMs) {
  mic.phase = "transcribing";
  paintMic();
  micHint("Turning your speech into text…");
  let hint = "";
  try {
    if (durationMs < 300 || !blob.size) return;
    const res = await fetch("/api/speech/transcribe", {
      method: "POST",
      headers: { "Content-Type": "audio/wav" },
      body: await speechWav(blob),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    const { text } = await res.json();
    if (text) useTranscript(text);
    else hint = "Didn't catch anything. Click Speak and try again.";
  } catch (err) {
    addSystem(`Speech: ${err.message || err}`);
  } finally {
    mic.phase = "idle";
    paintMic();
    micHint(hint);
  }
}

function initSpeech() {
  $("micBtn").addEventListener("click", () => {
    if (mic.phase === "idle") startListening();
    else stopListening();
  });
}

const VOICE_KEY = "sentient-voice";
const VOICE_DEFAULTS = { on: true, voice: "", speed: 1, volume: 0.9, emotion: true };

const voice = {
  enabled: false,
  status: "stopped",
  error: null,
  default: "",
  voices: [],
  settings: loadVoiceSettings(),
  ctx: null,
  gain: null,
  sources: new Set(),
  playEnd: 0,
};

function loadVoiceSettings() {
  try {
    return { ...VOICE_DEFAULTS, ...JSON.parse(localStorage.getItem(VOICE_KEY) || "{}") };
  } catch {
    return { ...VOICE_DEFAULTS };
  }
}

function saveVoiceSettings() {
  localStorage.setItem(VOICE_KEY, JSON.stringify(voice.settings));
}

function voiceReady() {
  return voice.enabled && voice.status === "ready";
}

function voiceRequest(force) {
  if (!voiceReady() || !(voice.settings.on || force)) return null;
  const { speed, emotion } = voice.settings;
  return { voice: voice.settings.voice || voice.default, speed, emotion };
}

function voiceTitle() {
  if (voice.status === "ready") return "The bot's voice runs on this computer.";
  if (voice.status === "error") return voice.error || "The voice model failed to load.";
  if (voice.status === "downloading") return "Downloading the voice model (about 340 MB, first run only)…";
  return "Loading the voice model…";
}

function renderVoice(state) {
  if (!state) return;
  Object.assign(voice, {
    enabled: state.enabled,
    status: state.status,
    error: state.error,
    default: state.default,
    voices: state.voices,
  });
  $("voiceBar").hidden = !state.enabled;
  const select = $("voiceName");
  if (select.options.length !== state.voices.length) {
    select.replaceChildren(
      ...state.voices.map(({ id, label }) => new Option(id === state.default ? `${label} (default)` : label, id))
    );
  }
  if (!state.voices.some(({ id }) => id === voice.settings.voice)) voice.settings.voice = state.default;
  select.value = voice.settings.voice;
  const ready = voiceReady();
  ["voiceName", "voiceSpeed", "voiceEmotion", "voiceTest"].forEach((id) => {
    $(id).disabled = !ready;
  });
  const tag = $("voiceTag");
  tag.textContent = { ready: "voice ready", error: "voice error", downloading: "downloading voice" }[voice.status] || "loading voice";
  tag.className = voice.status === "error" ? "pill live" : "pill idle";
  tag.title = voiceTitle();
}

function paintVoiceControls() {
  const s = voice.settings;
  $("voiceOn").checked = s.on;
  $("voiceSpeed").value = s.speed;
  $("voiceSpeedVal").textContent = `${Number(s.speed).toFixed(2)}×`;
  $("voiceVolume").value = s.volume;
  $("voiceEmotion").checked = s.emotion;
}

function audioOut() {
  if (!voice.ctx) {
    voice.ctx = new AudioContext();
    voice.gain = voice.ctx.createGain();
    voice.gain.connect(voice.ctx.destination);
  }
  voice.gain.gain.value = voice.settings.volume;
  // Browsers keep audio suspended until the page is clicked; every click calls this.
  if (voice.ctx.state === "suspended") voice.ctx.resume();
  return voice.ctx;
}

function playBuffer(buffer) {
  const ctx = audioOut();
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(voice.gain);
  const at = Math.max(ctx.currentTime + 0.03, voice.playEnd);
  source.start(at);
  voice.playEnd = at + buffer.duration;
  voice.sources.add(source);
  source.onended = () => voice.sources.delete(source);
}

function playPcm(base64, rate) {
  const bytes = Uint8Array.from(atob(base64), (ch) => ch.charCodeAt(0));
  const pcm = new Int16Array(bytes.buffer, 0, bytes.length >> 1);
  const buffer = audioOut().createBuffer(1, pcm.length, rate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < pcm.length; i += 1) data[i] = pcm[i] / 32768;
  playBuffer(buffer);
}

function stopVoice() {
  voice.sources.forEach((source) => {
    source.onended = null;
    source.stop();
  });
  voice.sources.clear();
  voice.playEnd = 0;
}

async function sayLine(text, force) {
  const request = voiceRequest(force);
  if (!request || !text) return;
  audioOut();
  try {
    const res = await fetch("/api/voice/say", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, ...request }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    playBuffer(await audioOut().decodeAudioData(await res.arrayBuffer()));
  } catch (err) {
    addSystem(`Voice: ${err.message || err}`);
  }
}

function initVoice() {
  paintVoiceControls();
  document.addEventListener("pointerdown", () => voiceReady() && audioOut(), { capture: true });
  const update = (patch) => {
    Object.assign(voice.settings, patch);
    saveVoiceSettings();
    paintVoiceControls();
  };
  $("voiceOn").addEventListener("change", (ev) => {
    update({ on: ev.target.checked });
    if (!ev.target.checked) stopVoice();
  });
  $("voiceName").addEventListener("change", (ev) => update({ voice: ev.target.value }));
  $("voiceSpeed").addEventListener("input", (ev) => update({ speed: Number(ev.target.value) }));
  $("voiceVolume").addEventListener("input", (ev) => {
    update({ volume: Number(ev.target.value) });
    if (voice.gain) voice.gain.gain.value = voice.settings.volume;
  });
  $("voiceEmotion").addEventListener("change", (ev) => update({ emotion: ev.target.checked }));
  $("voiceTest").addEventListener("click", () => {
    const name = ($("voiceName").selectedOptions[0]?.textContent || "").split(" · ")[0];
    stopVoice();
    sayLine(`Hi, I'm ${name || "the bot"}. This is how I sound at this speed.`, true);
  });
}

async function boot() {
  initSplitters();
  const state = await fetch("/api/state").then((r) => r.json());
  renderState(state);
  writeSlidersFrom(state.draft || state.sensors);
  connect();
  refreshPreview();
  initSimulation();
  initMemory();
  initSpeech();
  initVoice();
  pollVision();
}

boot();
