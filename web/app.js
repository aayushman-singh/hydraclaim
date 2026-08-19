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

checkHealth();
loadSamples();
loadGraph();
setInterval(checkHealth, 30000);
