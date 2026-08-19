const API = window.HYDRACLAIM_API;

const input = document.getElementById("question");
const suggestionsEl = document.getElementById("suggestions");
const resultEl = document.getElementById("result");
const traceEl = document.getElementById("trace");
const traceEmpty = document.getElementById("trace-empty");

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

// ─── View switching (landing / console) ───
const landingEl = document.getElementById("landing");
const appEl = document.getElementById("app");
const views = ["dashboard", "ask", "graph", "ingest"];

function setView(name) {
  views.forEach((v) => {
    const el = document.getElementById("view-" + v);
    if (el) el.style.display = v === name ? "" : "none";
    const nav = document.getElementById("nav-" + v);
    if (nav) nav.classList.toggle("active", v === name);
  });
  if (name === "dashboard") loadDashboard();
  if (name === "ask" && input) input.focus();
}
window.setView = setView;

window.goView = function (name) {
  landingEl.style.display = "none";
  appEl.style.display = "";
  setView(name);
};
window.goLanding = function () {
  appEl.style.display = "none";
  landingEl.style.display = "";
};

// ─── Dashboard ───
let dashLoaded = false;
async function loadDashboard() {
  try {
    const r = await fetch(API + "/graph", { signal: AbortSignal.timeout(10000) });
    if (!r.ok) throw new Error("http " + r.status);
    const data = await r.json();
    const nodes = data.nodes || [];
    const edges = data.edges || [];
    const entities = nodes.filter((n) => n.kind === "entity").length;
    const claims = nodes.filter((n) => n.kind === "claim").length;
    const supersedes = edges.filter((e) => e.type === "SUPERSEDES").length;
    const conflicts = edges.filter((e) => e.type === "CONTRADICTS").length;

    document.getElementById("dash-entities").textContent = entities;
    document.getElementById("dash-claims").textContent = claims;
    document.getElementById("dash-supersedes").textContent = supersedes;
    document.getElementById("dash-conflicts").textContent = conflicts;

    // Route mix: run themed probe questions and bucket by route.
    let mix = { FAST: 0, DEEP: 0, ABSTAIN: 0 };
    const probes = [
      "What is the current launch deadline?",
      "Where is Casey Brooks located now?",
      "What is Casey Brooks phone number?",
      "Who owns the payments integration?",
      "What is the Q3 marketing budget?",
      "What was the launch deadline before the most recent change?",
    ];
    await Promise.all(probes.map(async (q) => {
      try {
        const rr = await fetch(API + "/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: q }),
        });
        const dd = await rr.json();
        if (dd.route && mix[dd.route] !== undefined) mix[dd.route] += 1;
      } catch (e) { /* skip */ }
    }));

    const total = probes.length;
    const el = document.getElementById("dash-routes");
    const order = [["FAST", "ok"], ["DEEP", "info"], ["ABSTAIN", "warn"]];
    el.innerHTML = order.map(([route, kind]) => {
      const count = mix[route];
      const pct = total ? Math.round((count / total) * 100) : 0;
      const fill = kind === "info" ? "var(--violet)" : kind === "warn" ? "var(--amber)" : "var(--cyan)";
      const note = route === "FAST" ? "single uncontested fact"
        : route === "DEEP" ? "history or conflict"
        : "no claim covers it";
      return (
        '<div class="route-row">' +
          '<span class="route-label">' + route + '</span>' +
          '<div class="route-track"><div class="route-fill" style="width:' + pct + '%;background:' + fill + ';"></div></div>' +
          '<span class="route-count">' + count + " · " + pct + '%</span>' +
        '</div>' +
        '<div class="route-note" style="margin-top:-6px;padding-left:98px;">' + note + '</div>'
      );
    }).join("");
    dashLoaded = true;
  } catch (e) {
    const el = document.getElementById("dash-routes");
    if (el) el.innerHTML = '<div class="err-box">dashboard unavailable: ' + esc(e.message) + "</div>";
  }
}

// ─── Health ───
const statusEl = document.getElementById("topbar-status");
const GRAPH_META = document.getElementById("graph-meta");

async function checkHealth() {
  try {
    const r = await fetch(API + "/health", { signal: AbortSignal.timeout(8000) });
    if (!r.ok) throw new Error("http " + r.status);
    statusEl.classList.add("live");
    statusEl.innerHTML = "<span>API live</span>";
  } catch (e) {
    statusEl.classList.remove("live");
    statusEl.classList.add("off");
    statusEl.innerHTML = "<span>API offline</span>";
  }
}

// ─── Suggestions from live scenarios ───
async function loadSuggestions() {
  try {
    const r = await fetch(API + "/scenarios", { signal: AbortSignal.timeout(8000) });
    if (!r.ok) return;
    const data = await r.json();
    const chosen = [];
    for (const s of data.scenarios || []) {
      const pref = s.questions.find((q) => !/originally|first set|start of/.test(q));
      chosen.push(pref || (s.questions || [])[0]);
    }
    for (const q of chosen) {
      if (!q) continue;
      const b = document.createElement("button");
      b.textContent = q.length > 52 ? q.slice(0, 52) + "\u2026" : q;
      b.title = q;
      b.onclick = () => { input.value = q; runAsk(); };
      suggestionsEl.appendChild(b);
    }
  } catch (e) { /* chips are optional */ }
}

// ─── Verdict pill styling ───
function pillHTML(route, label) {
  return '<span class="pill ' + esc(route) + '">' + esc(label || route) + "</span>";
}

// ─── Router trace: build from the real classification/probe/route ───
function renderTrace(data) {
  const cls = data.classification || {};
  const probe = data.probe;
  traceEl.innerHTML = "";
  traceEmpty.style.display = "none";

  const items = [];

  items.push({
    label: "Classify question",
    value: cls.question_type || "unknown",
    detail: "Resolve the entity, predicate, and temporal intent of the question.",
  });
  items.push({
    label: "Entity matched",
    value: cls.subject || "none",
    detail: cls.subject
      ? "The classifier resolved the entity the question is about."
      : "No tracked entity matched, so nothing can be answered.",
  });
  if (cls.predicate) {
    items.push({
      label: "Predicate",
      value: cls.predicate,
      detail: "The closed-vocabulary fact the question maps onto.",
    });
  }
  if (cls.as_of) {
    items.push({
      label: "Bitemporal filter",
      value: cls.as_of,
      detail: "As-of filter applied to recorded_at / validity window.",
    });
  }

  if (probe) {
    const coverageWarn = probe.coverage === 0;
    items.push({
      label: "Typed coverage",
      value: probe.coverage + " claim" + (probe.coverage === 1 ? "" : "s"),
      detail: "Claims about " + (probe.predicate || "the subject") + " in the graph.",
      isWarn: coverageWarn,
    });
    items.push({
      label: "Contradictions",
      value: probe.conflicts + " edge" + (probe.conflicts === 1 ? "" : "s"),
      detail: probe.distinct_active_values > 1
        ? "Multiple active values disagree even without a typed edge."
        : "Unresolved CONTRADICTS edges among active claims.",
      isWarn: probe.conflicts > 0 || probe.distinct_active_values > 1,
    });
    items.push({
      label: "Supersession depth",
      value: probe.chain_depth + " hop" + (probe.chain_depth === 1 ? "" : "s"),
      detail: "Longest SUPERSEDES chain among matching claims.",
    });
  }

  items.push({ label: "Route", value: data.route || "—", isRoute: true });

  items.forEach((it, i) => {
    const div = document.createElement("div");
    let clsList = "trace-item";
    if (it.isRoute) {
      clsList += " route";
      if (it.value === "FAST") clsList += " badge-fast";
      if (it.value === "ABSTAIN") clsList += " badge-abstain";
    } else if (it.isWarn) {
      clsList += " warn";
    }
    div.className = clsList;
    div.style.animationDelay = (i * 70) + "ms";

    const dotCls = it.isWarn && !it.isRoute ? " trace-dot warn" : " trace-dot";
    const detailId = "tr-" + i;
    div.innerHTML =
      '<div class="trace-head-row" onclick="toggleTrace(' + i + ')">' +
        '<span class="' + dotCls + '"></span>' +
        '<span class="trace-label">' + esc(it.label) + "</span>" +
        '<span class="trace-value">' + esc(it.value) + "</span>" +
        '<span class="trace-caret" id="caret-' + i + '">▾</span>' +
      "</div>" +
      '<div class="trace-detail" id="' + detailId + '" style="display:none;">' +
        "<p>" + esc(it.detail || "") + "</p>" +
      "</div>";
    traceEl.appendChild(div);
  });
}
window.toggleTrace = function (i) {
  const det = document.getElementById("tr-" + i);
  const caret = document.getElementById("caret-" + i);
  if (!det) return;
  const open = det.style.display !== "none";
  det.style.display = open ? "none" : "block";
  caret.style.transform = open ? "" : "rotate(180deg)";
};

// ─── Render answer block ───
function renderAnswer(data) {
  const route = data.route || "";
  const abstain = route === "ABSTAIN";
  const answerCard = abstain
    ? "answer-card abstain"
    : "answer-card";

  let citations = "";
  if (data.citations && data.citations.length) {
    const list = data.citations.map((c) => {
      const cid = esc(c.claim_id != null ? c.claim_id : "");
      const kind = esc(c.source_kind || "");
      const author = esc(c.author != null ? "/" + c.author : "");
      const quote = esc(c.quote || "");
      const at = esc(c.valid_from != null ? c.valid_from.slice(0, 10) : "");
      const warn = /conflict/i.test(route) ? " warn" : "";
      return (
        '<div class="cite' + warn + '">' +
          '<div class="cite-head">' +
            '<span class="cite-tag">' + cid + "</span>" +
            '<span class="cite-source">' + kind + author + "</span>" +
            '<span class="cite-at">' + at + "</span>" +
          "</div>" +
          '<div class="cite-quote">\u201c' + quote + "\u201d</div>" +
        "</div>"
      );
    }).join("");
    citations =
      '<div class="citations"><div class="citations-title">CITED EVIDENCE</div>' +
      '<div class="citation-list">' + list + "</div></div>";
  }

  return (
    pillHTML(route) +
    '<div class="answer-card ' + answerCard + '">' +
      '<div class="step-label" style="margin-bottom:14px;">ANSWER</div>' +
      '<div class="answer-text">' + esc(data.answer || "") + "</div>" +
    "</div>" +
    citations
  );
}

// ─── Ask ───
async function runAsk() {
  const q = input.value.trim();
  if (!q) return;

  resultEl.innerHTML =
    '<div class="answer-card" style="display:flex;align-items:center;gap:10px;color:var(--fg-3);">' +
      '<span class="spinner" style="width:14px;height:14px;border:2px solid var(--border-strong);' +
      'border-top-color:var(--cyan);border-radius:99px;display:inline-block;animation:spin .8s linear infinite;"></span>' +
      "Asking…</div>";
  traceEl.innerHTML = "";
  traceEmpty.style.display = "none";

  const start = performance.now();
  try {
    const r = await fetch(API + "/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });
    const data = await r.json();
    const ms = Math.round(performance.now() - start);

    document.getElementById("cost-queries").textContent =
      data.probe ? String(1 + (data.probe.conflicts > 0 ? 2 : 0)) : "—";
    document.getElementById("cost-latency").textContent =
      ms >= 1000 ? (ms / 1000).toFixed(1) + " s" : ms + " ms";

    if (!r.ok) {
      resultEl.innerHTML =
        '<div class="err-box">' + esc(data.error || "HTTP " + r.status) + "</div>";
      renderTrace({ route: "error" });
      return;
    }
    resultEl.innerHTML = renderAnswer(data);
    renderTrace(data);
  } catch (err) {
    document.getElementById("cost-queries").textContent = "—";
    document.getElementById("cost-latency").textContent = "—";
    resultEl.innerHTML =
      '<div class="err-box">request failed: ' + esc(err.message) + "</div>";
    renderTrace({ route: "error" });
  }
}
window.runAsk = runAsk;
input.addEventListener("keydown", (e) => { if (e.key === "Enter") runAsk(); });

// ─── Graph ───
let network = null;
async function loadGraph() {
  try {
    const r = await fetch(API + "/graph", { signal: AbortSignal.timeout(10000) });
    if (!r.ok) throw new Error("http " + r.status);
    const data = await r.json();

    const nodes = new vis.DataSet((data.nodes || []).map((n) => {
      const isEntity = n.kind === "entity";
      const color = isEntity
        ? "oklch(0.82 0.12 202)"
        : (n.status === "active" ? "oklch(0.78 0.15 148)" : "oklch(0.52 0.03 258)");
      return {
        id: n.id,
        label: isEntity ? n.label
          : (n.label.length > 40 ? n.label.slice(0, 40) + "\u2026" : n.label),
        title: isEntity ? n.label + " (" + (n.type || "entity") + ")"
          : (n.key || n.label) + "\nstatus: " + n.status,
        color: { background: color, border: color },
        shape: isEntity ? "box" : "dot",
        size: isEntity ? 20 : 10,
        font: { color: "oklch(0.97 0.004 258)", size: isEntity ? 13 : 11,
                face: "JetBrains Mono, Consolas, monospace" },
      };
    }));

    const edges = new vis.DataSet((data.edges || []).map((e) => {
      const style = {
        SUPERSEDES: { color: "oklch(0.62 0.09 202)", dashes: [10, 6], arrows: "to" },
        CONTRADICTS: { color: "oklch(0.82 0.13 78)", dashes: [4, 4], arrows: "to;from" },
        ABOUT: { color: "oklch(0.40 0.016 258)", dashes: false, arrows: "" },
      }[e.type] || { color: "oklch(0.40 0.016 258)", dashes: false, arrows: "" };
      return {
        from: e.from, to: e.to,
        color: { color: style.color }, dashes: style.dashes,
        arrows: style.arrows, title: e.type,
      };
    }));

    const container = document.getElementById("graph");
    network = new vis.Network(container, { nodes, edges }, {
      physics: { barnesHut: { gravitationalConstant: -2600, springLength: 130 } },
      interaction: { hover: true },
      nodes: { borderWidth: 1 },
    });
    GRAPH_META.textContent =
      (data.nodes || []).length + " nodes \u00b7 " + (data.edges || []).length + " edges";
  } catch (e) {
    document.getElementById("graph").innerHTML =
      '<p style="color:var(--muted);padding:20px;font-size:13px">graph unavailable: ' +
      esc(e.message) + "</p>";
    GRAPH_META.textContent = "unavailable";
  }
}

// ─── Ingest ───
const ingestTabs = document.querySelectorAll(".ingest-tab");
const tabPanels = document.querySelectorAll(".tab-panel");
let activeTab = "text";
let uploadedFile = null;
let slackFile = null;

ingestTabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    ingestTabs.forEach((t) => t.classList.remove("active"));
    tabPanels.forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    activeTab = tab.dataset.tab;
    document.getElementById("tab-" + activeTab).classList.add("active");
  });
});

function setupDropZone(zoneId, fileInputId, nameId, onFile) {
  const zone = document.getElementById(zoneId);
  const fileInput = document.getElementById(fileInputId);
  if (!zone || !fileInput) return;
  zone.addEventListener("click", () => fileInput.click());
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("dragover"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("dragover");
    if (e.dataTransfer.files.length) onFile(e.dataTransfer.files[0], nameId);
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files.length) onFile(fileInput.files[0], nameId);
  });
}

setupDropZone("drop-zone", "file-input", "file-name", (file, nameId) => {
  uploadedFile = file;
  document.getElementById(nameId).textContent = file.name;
});
setupDropZone("drop-zone-slack", "slack-file-input", "slack-file-name", (file, nameId) => {
  slackFile = file;
  document.getElementById(nameId).textContent = file.name;
});

function getWriteKey() {
  const key = document.getElementById("ingest-key").value.trim();
  if (key) localStorage.setItem("hydraclaim_write_key", key);
  return key || localStorage.getItem("hydraclaim_write_key") || "";
}

function showIngestResult(ok, text) {
  const el = document.getElementById("ingest-result");
  el.textContent = text;
  el.className = "ingest-result show " + (ok ? "ok" : "err");
}

function readFileText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

document.getElementById("ingest-submit").addEventListener("click", async () => {
  const btn = document.getElementById("ingest-submit");
  btn.disabled = true;
  btn.textContent = "Ingesting\u2026";
  const key = getWriteKey();
  const headers = { "Content-Type": "application/json" };
  if (key) headers["Authorization"] = "Bearer " + key;

  try {
    let endpoint, body;

    if (activeTab === "text") {
      const text = document.getElementById("ingest-text").value.trim();
      if (!text) { showIngestResult(false, "no text provided"); btn.disabled = false; btn.textContent = "Ingest"; return; }
      endpoint = "/ingest";
      body = {
        text: text,
        source_kind: document.getElementById("ingest-source").value,
        author: document.getElementById("ingest-author").value.trim() || "unknown",
        channel: document.getElementById("ingest-channel").value.trim() || "adhoc",
      };
    } else if (activeTab === "file") {
      if (!uploadedFile) { showIngestResult(false, "no file selected"); btn.disabled = false; btn.textContent = "Ingest"; return; }
      const content = await readFileText(uploadedFile);
      endpoint = "/ingest";
      if (uploadedFile.name.endsWith(".json")) {
        const parsed = JSON.parse(content);
        if (parsed.sessions) {
          body = parsed;
        } else {
          body = { text: content, source_kind: "meeting", author: "unknown" };
        }
      } else {
        body = { text: content, source_kind: "meeting", author: "unknown" };
      }
    } else if (activeTab === "slack") {
      if (!slackFile) { showIngestResult(false, "no Slack export file selected"); btn.disabled = false; btn.textContent = "Ingest"; return; }
      const content = await readFileText(slackFile);
      const parsed = JSON.parse(content);
      endpoint = "/ingest/slack";
      body = {
        channel: document.getElementById("slack-channel").value.trim() || "general",
        messages: Array.isArray(parsed) ? parsed : (parsed.messages || []),
      };
    }

    const resp = await fetch(API + endpoint, {
      method: "POST",
      headers: headers,
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showIngestResult(false, data.error || "HTTP " + resp.status);
    } else {
      const lines = [
        "Ingestion complete:",
        "  created: " + (data.created || 0),
        "  superseded: " + (data.superseded || 0),
        "  contradicted: " + (data.contradicted || 0),
        "  duplicates: " + (data.duplicates || 0),
      ];
      if (data.sessions_processed) lines.push("  sessions: " + data.sessions_processed);
      if (data.warnings && data.warnings.length) {
        lines.push("", "warnings:", ...data.warnings.map((w) => "  " + w));
      }
      showIngestResult(true, lines.join("\n"));
      loadGraph();
    }
  } catch (err) {
    showIngestResult(false, "error: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Ingest";
  }
});

// Restore saved write key
const savedKey = localStorage.getItem("hydraclaim_write_key");
if (savedKey) document.getElementById("ingest-key").value = savedKey;

// ─── Init ───
checkHealth();
loadSuggestions();
loadGraph();
setInterval(checkHealth, 30000);
