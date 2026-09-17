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
  lastState = state;
  live = {
    temperature: state.sensors.temperature,
    light: state.sensors.light,
    volume: state.sensors.volume,
    humidity: state.sensors.humidity,
    smell: state.sensors.smell,
    surface_touch: state.sensors.surface_touch,
  };
  const emotion = state.emotion.name;
  $("emotionBadge").textContent = `${emotion} ${state.emotion.intensity.toFixed(2)}`;
  $("emotionBadge").className = `emotion ${emotion}`;
  $("modelTag").textContent = state.model;
  setSpeaking(Boolean(state.streaming));
  paintTicks(state);
  $("dirtyTag").textContent = isDirty() ? "pending apply" : "synced";
  $("dirtyTag").className = isDirty() ? "pill live" : "pill idle";
}

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
  $("screenTitle").textContent =
    name === "edit" ? "Edit Personality" : "Emotion by Interruption";
  $("screenEyebrow").textContent =
    name === "edit" ? "Personality programming" : "Sentient AI · Individual_001";
  $("screenLede").textContent =
    name === "edit"
      ? "Drag the colored threshold line to resize bands. Emotion dropdowns follow the new ranges."
      : "Draft values. Apply while the bot is mid-sentence to interrupt it.";
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

async function boot() {
  const state = await fetch("/api/state").then((r) => r.json());
  renderState(state);
  writeSlidersFrom(state.draft || state.sensors);
  connect();
  refreshPreview();
}

boot();
