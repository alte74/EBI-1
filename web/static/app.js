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
    if (msg.type === "interrupt") {
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
}

function applyLive(state) {
  lastState = state;
  const lightUntouched = Number(draft.light) === Number(live.light);
  live = {
    temperature: state.sensors.temperature,
    light: state.sensors.light,
    volume: state.sensors.volume,
    humidity: state.sensors.humidity,
    smell: state.sensors.smell,
    surface_touch: state.sensors.surface_touch,
  };
  if (lightUntouched) {
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
  if (!vision.enabled) {
    tag.textContent = "paused";
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
    setFact(
      "visionLight",
      `${verdict} · measured ${report.luminance.toFixed(2)} (${band})${notes}`,
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
    const key = `${v.report?.seq}|${v.enabled}|${v.drive_light}|${v.status}`;
    if (key !== visionKey) {
      const fresh = v.report && v.report.seq !== visionSeq;
      visionKey = key;
      applyLive(state);
      renderVision(v);
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
    addSystem(String(err.message || err));
  }
}

$("visionEnabled").addEventListener("change", (ev) => setVision({ enabled: ev.target.checked }));
$("visionDrive").addEventListener("change", (ev) => setVision({ drive_light: ev.target.checked }));

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

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
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
  socket.send(JSON.stringify({ type: "chat", text }));
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
  if (applied.interrupt) addInterrupt(applied.interrupt);
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

async function boot() {
  initSplitters();
  const state = await fetch("/api/state").then((r) => r.json());
  renderState(state);
  writeSlidersFrom(state.draft || state.sensors);
  connect();
  refreshPreview();
  initSimulation();
  pollVision();
}

boot();
