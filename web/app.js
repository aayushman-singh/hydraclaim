const API = window.HYDRACLAIM_API;

const chatEl = document.getElementById("chat");
const form = document.getElementById("ask-form");
const input = document.getElementById("question");
const statusPill = document.getElementById("status-pill");
const samplesEl = document.getElementById("samples");

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

async function checkHealth() {
  try {
    const r = await fetch(API + "/health");
    if (!r.ok) throw new Error("http " + r.status);
    statusPill.textContent = "live";
    statusPill.className = "pill ok";
  } catch (e) {
    statusPill.textContent = "backend offline";
    statusPill.className = "pill err";
  }
}

async function loadSamples() {
  try {
    const r = await fetch(API + "/scenarios");
    const data = await r.json();
    const chosen = [];
    for (const s of data.scenarios) {
      const pref = s.questions.find((q) => !/originally|first set|start of/.test(q));
      chosen.push(pref || s.questions[0]);
    }
    for (const q of chosen) {
      const b = document.createElement("button");
      b.textContent = q.length > 52 ? q.slice(0, 52) + "\u2026" : q;
      b.title = q;
      b.onclick = () => { input.value = q; form.requestSubmit(); };
      samplesEl.appendChild(b);
    }
  } catch (e) { /* chips are optional */ }
}

function addMsg(cls, html) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.innerHTML = html;
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
  return div;
}

function renderAnswer(data) {
  const badge = `<span class="badge ${esc(data.route)}">${esc(data.route)}</span>`;
  let probe = "";
  if (data.probe) {
    const p = data.probe;
    probe = `<div class="probe-row">coverage ${p.coverage} \u00b7 conflicts ${p.conflicts} \u00b7 chain depth ${p.chain_depth}</div>`;
  }
  let cites = "";
  if (data.citations && data.citations.length) {
    cites = '<div class="citations">' + data.citations.map((c) =>
      `<div class="cite"><span class="cite-key">[${esc(c.claim_id)}]</span> ` +
      `${esc(c.source_kind)}/${esc(c.author)}: <span class="quote">\u201c${esc(c.quote)}\u201d</span></div>`
    ).join("") + "</div>";
  }
  return badge + probe + esc(data.answer) + cites;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  addMsg("q", esc(q));
  const pending = addMsg("a", '<span class="badge">\u2026</span>');
  try {
    const r = await fetch(API + "/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });
    const data = await r.json();
    if (!r.ok) {
      pending.className = "msg a err";
      pending.textContent = data.error || ("HTTP " + r.status);
    } else {
      pending.innerHTML = renderAnswer(data);
    }
  } catch (err) {
    pending.className = "msg a err";
    pending.textContent = "request failed: " + err.message;
  }
  chatEl.scrollTop = chatEl.scrollHeight;
});

let network = null;
async function loadGraph() {
  try {
    const r = await fetch(API + "/graph");
    const data = await r.json();
    const nodes = new vis.DataSet(data.nodes.map((n) => {
      const isEntity = n.kind === "entity";
      const color = isEntity ? "#60a5fa" : (n.status === "active" ? "#4ade80" : "#71717a");
      return {
        id: n.id,
        label: isEntity ? n.label : (n.label.length > 34 ? n.label.slice(0, 34) + "\u2026" : n.label),
        title: isEntity ? `${n.label} (${n.type})` : `${n.key}\nstatus: ${n.status}`,
        color: { background: color, border: color },
        shape: isEntity ? "box" : "dot",
        size: isEntity ? 20 : 10,
        font: { color: "#ececf1", size: isEntity ? 13 : 11, face: "JetBrains Mono, Consolas, monospace" },
      };
    }));
    const edges = new vis.DataSet(data.edges.map((e) => {
      const style = {
        SUPERSEDES: { color: "#fbbf24", dashes: true, arrows: "to" },
        CONTRADICTS: { color: "#f87171", dashes: [2, 4], arrows: "to;from" },
        ABOUT: { color: "#23252e", dashes: false, arrows: "" },
      }[e.type] || { color: "#23252e", dashes: false, arrows: "" };
      return { from: e.from, to: e.to, color: { color: style.color }, dashes: style.dashes, arrows: style.arrows, title: e.type };
    }));
    const container = document.getElementById("graph");
    network = new vis.Network(container, { nodes, edges }, {
      physics: { barnesHut: { gravitationalConstant: -2600, springLength: 130 } },
      interaction: { hover: true },
      nodes: { borderWidth: 1 },
    });
    document.getElementById("graph-meta").textContent =
      `${data.nodes.length} nodes \u00b7 ${data.edges.length} edges`;
  } catch (e) {
    document.getElementById("graph").innerHTML =
      '<p style="color:var(--muted);padding:16px;font-size:13px">graph unavailable: ' + esc(e.message) + "</p>";
  }
}

// ─── Ingest section ───

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

async function readFileText(file) {
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
      if (!text) { showIngestResult(false, "no text provided"); return; }
      endpoint = "/ingest";
      body = {
        text: text,
        source_kind: document.getElementById("ingest-source").value,
        author: document.getElementById("ingest-author").value.trim() || "unknown",
        channel: document.getElementById("ingest-channel").value.trim() || "adhoc",
      };
    } else if (activeTab === "file") {
      if (!uploadedFile) { showIngestResult(false, "no file selected"); return; }
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
      if (!slackFile) { showIngestResult(false, "no Slack export file selected"); return; }
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
        `  created: ${data.created || 0}`,
        `  superseded: ${data.superseded || 0}`,
        `  contradicted: ${data.contradicted || 0}`,
        `  duplicates: ${data.duplicates || 0}`,
      ];
      if (data.sessions_processed) lines.push(`  sessions: ${data.sessions_processed}`);
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
loadSamples();
loadGraph();
setInterval(checkHealth, 30000);
