/* PixelAgent UI: hash-routed single page over the local JSON API. No framework, no build step. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (name, cls = "ic") => `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

const GATE_TEXT = {
  intake: "Check the plan", concept: "Pick a concept", review: "Approve the sprite",
  anim_poses: "Approve the poses", anim_review: "Approve the animation",
};
const STEP_LABEL = (s) => s.label;

const state = { overview: null, items: [], detailSig: "", pollTimer: null, zoom: "fit", pick: null };

// ------------------------------------------------------------------ api

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok || data.error) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", error);
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), error ? 5200 : 2600);
}

async function act(fn, okMsg) {
  try {
    const r = await fn();
    if (okMsg) toast(okMsg);
    await refresh(true);
    return r;
  } catch (e) {
    toast(e.message, true);
  }
}

// ------------------------------------------------------------------ data

const project = () => state.overview && state.overview.current;

async function loadOverview() {
  state.overview = await api("/api/overview");
  renderSidebar();
}

async function loadItems() {
  if (!project()) { state.items = []; return; }
  state.items = (await api(`/api/assets?project=${encodeURIComponent(project())}`)).items;
  const wait = (k) => state.items.filter((i) => i.kind === k && i.gate && !i.running).length;
  for (const [k, id] of [["character", "badge-characters"], ["animation", "badge-animations"]]) {
    const n = wait(k);
    const b = $("#" + id);
    b.hidden = !n;
    b.textContent = n;
  }
}

// ------------------------------------------------------------------ sidebar

function renderSidebar() {
  const o = state.overview;
  $("#project-list").innerHTML = o.projects.map((p) => `
    <button class="side-item project-item ${p === o.current ? "current active" : ""}" data-project="${esc(p)}">
      ${icon("folder")}<span>${esc(p)}</span></button>`).join("");
  const cap = Number(o.settings.monthly_usd) || 1;
  const pct = Math.min(100, (o.month_spent / cap) * 100);
  $("#spend-text").textContent = `$${o.month_spent.toFixed(2)} of $${cap.toFixed(0)}`;
  $("#spend-fill").style.width = pct + "%";
  $("#spend-fill").classList.toggle("high", pct > 80);
  const view = route().view;
  document.querySelectorAll(".side-item[data-view]").forEach((a) => {
    const v = a.dataset.view;
    a.classList.toggle("active", v === view || (view === "item" && v === route().from));
  });
}

document.addEventListener("click", async (e) => {
  const p = e.target.closest("[data-project]");
  if (p) {
    await act(() => api("/api/current", { project: p.dataset.project }));
    location.hash = "#/characters";
  }
});
$("#btn-new-project").addEventListener("click", () => sheetNewProject());

// ------------------------------------------------------------------ routing

function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  const view = parts[0] || "characters";
  return { view, id: parts[1] ? decodeURIComponent(parts[1]) : null, from: sessionStorage.getItem("from") || "characters" };
}

window.addEventListener("hashchange", () => { state.detailSig = ""; state.zoom = "fit"; state.pick = null; render(); });

async function refresh(force) {
  await loadOverview();
  await loadItems();
  if (force) state.detailSig = "";
  await render(true);
}

async function render(fromRefresh) {
  const r = route();
  renderSidebar();
  if (!state.overview) return;
  if (r.view === "settings") return renderSettings();
  if (!project()) return renderNoProject();
  if (r.view === "item" && r.id) return renderDetail(r.id, fromRefresh);
  if (!fromRefresh) await loadItems();
  sessionStorage.setItem("from", r.view);
  return renderLibrary(r.view === "animations" ? "animation" : "character");
}

function setToolbar(html) { $("#toolbar").innerHTML = html; }

// ------------------------------------------------------------------ library

function statusLine(i) {
  if (i.running) return `<span class="spinner"></span>Working`;
  if (i.failed) return `<span class="dot fail"></span>Stopped`;
  if (i.gate) return `<span class="dot wait"></span>${esc(GATE_TEXT[i.gate] || "Needs you")}`;
  if (i.status === "complete") return `<span class="dot done"></span>Ready`;
  if (i.paused) return `<span class="dot wait"></span>Paused`;
  return `<span class="dot"></span>Not started`;
}

function stageImg(url, alt) {
  return url ? `<img class="px" src="${esc(url)}" alt="${esc(alt)}" loading="lazy" onload="fitPixel(this)">` : "";
}

// Pixel art is always shown at a whole-number zoom: the largest that fits its stage (crisp, never blurred).
function fitPixel(img, fill = 0.84) {
  const box = img.parentElement;
  if (!box || !img.naturalWidth) return;
  if (img.naturalWidth > 512) { img.style.width = ""; return; }   // a painting (concept), not pixel art
  const fit = Math.min((box.clientWidth * fill) / img.naturalWidth, (box.clientHeight * fill) / img.naturalHeight);
  // whole-number zoom; when too big, a whole-number divisor (1/2, 1/3...) keeps pixels evenly sized too
  const k = fit >= 1 ? Math.floor(fit) : 1 / Math.ceil(1 / fit);
  img.style.width = Math.round(img.naturalWidth * k) + "px";
  img.style.maxWidth = "none";
  img.style.maxHeight = "none";
}
window.fitPixel = fitPixel;
window.addEventListener("resize", () => document.querySelectorAll("img.px").forEach((i) => fitPixel(i)));

function renderLibrary(kind) {
  const plural = kind === "animation" ? "Animations" : "Characters";
  const filter = sessionStorage.getItem("filter-" + kind) || "all";
  setToolbar(`
    <div class="title">${esc(project())}</div><div class="spacer"></div>
    <div class="segmented" role="group" aria-label="Show">
      ${[["all", "All"], ["needs", "Needs you"], ["working", "Working"], ["ready", "Ready"]].map(([v, l]) =>
        `<button data-filter="${v}" aria-pressed="${filter === v}">${l}</button>`).join("")}
    </div>
    ${kind === "character" ? `<button class="btn primary round" id="btn-new">${icon("plus")}New character</button>` : ""}`);
  document.querySelectorAll("[data-filter]").forEach((b) => b.onclick = () => { sessionStorage.setItem("filter-" + kind, b.dataset.filter); render(); });
  const nb = $("#btn-new");
  if (nb) nb.onclick = () => sheetNewCharacter();

  let items = state.items.filter((i) => i.kind === kind);
  const all = items.length;
  if (filter === "needs") items = items.filter((i) => !i.running && (i.gate || i.paused || i.failed));
  if (filter === "working") items = items.filter((i) => i.running);
  if (filter === "ready") items = items.filter((i) => i.status === "complete");

  const c = $("#content");
  if (!all) {
    c.innerHTML = kind === "character" ? emptyState("person", "No characters yet",
      "Describe a character or drop in a reference image. You'll check the plan, pick a concept and approve the sprite along the way.",
      `<button class="btn primary lg round" id="empty-new">${icon("plus")}New character</button>`)
      : emptyState("film", "No animations yet", "Open a finished character and choose New animation. Describe the motion in your own words.", "");
    const en = $("#empty-new");
    if (en) en.onclick = () => sheetNewCharacter();
    return;
  }
  c.innerHTML = `
    <div class="page-head"><div><h1>${plural}</h1></div></div>
    ${items.length ? `<div class="grid">${items.map((i) => `
      <a class="tile" href="#/item/${encodeURIComponent(i.id)}" aria-label="${esc(i.name)}">
        <div class="stage ${i.thumb ? "" : "empty"}">${stageImg(i.thumb, i.name)}
          ${i.running ? `<span class="working"><span class="spinner"></span>Working</span>` : ""}</div>
        <div class="tile-name">${esc(i.name)}${i.character_name ? `<span class="tile-of">${esc(i.character_name)}</span>` : ""}</div>
        <div class="tile-status">${statusLine(i)}</div>
      </a>`).join("")}</div>` : `<p class="muted">Nothing here right now.</p>`}`;
  schedulePoll(state.items.some((i) => i.running) ? 2000 : 0);
}

function emptyState(ic, title, text, action) {
  return `<div class="empty-state"><div class="stage art">${icon(ic, "ic")}</div><h2>${esc(title)}</h2><p>${esc(text)}</p>${action}</div>`;
}

function renderNoProject() {
  setToolbar(`<div class="title">PixelAgent</div>`);
  $("#content").innerHTML = emptyState("folder", "Create a project",
    "A project holds the characters and animations of one game. They share settings and a spending total.",
    `<button class="btn primary lg round" id="np">${icon("plus")}New project</button>`);
  $("#np").onclick = () => sheetNewProject();
}

// ------------------------------------------------------------------ detail

async function renderDetail(id, fromRefresh) {
  let d;
  try {
    d = await api(`/api/asset?project=${encodeURIComponent(project())}&id=${encodeURIComponent(id)}`);
  } catch (e) {
    $("#content").innerHTML = emptyState("x", "This item could not be opened", e.message, "");
    return;
  }
  const sig = JSON.stringify([d.status, d.running, d.steps.map((s) => [s.status, s.version]), d.gate, d.log.length, d.job_error]);
  schedulePoll(d.running ? 1500 : 0);
  if (fromRefresh && sig === state.detailSig) return;   // nothing changed: keep the page (and anything typed) as is
  state.detailSig = sig;

  const from = d.kind === "animation" ? "animations" : "characters";
  sessionStorage.setItem("from", from);
  renderSidebar();
  const backLabel = d.kind === "animation" ? "Animations" : "Characters";
  setToolbar(`<div class="title">${esc(d.name)}</div><div class="spacer"></div>
    ${d.failed || d.job_error ? `<button class="btn" id="btn-retry">${icon("retry")}Try again</button>` : ""}
    ${d.exports ? `<button class="btn" id="btn-folder">${icon("folder")}Show in folder</button>
                   <button class="btn primary" id="btn-pl">${icon("export")}Open in PixelLiner</button>` : ""}`);

  const gd = d.gate_detail;
  const heroUrl = d.kind === "animation" && gd && gd.id === "R" ? gd.image
    : gd && gd.candidates && state.pick !== null && gd.candidates[state.pick] ? gd.candidates[state.pick] : d.hero;
  $("#content").innerHTML = `
    <a class="back" href="#/${from}">${icon("chevron-left")}${backLabel}</a>
    <div class="page-head"><div><h1>${esc(d.name)}</h1>
      ${d.kind === "animation" && d.character_name ? `<div class="sub">${esc(d.character_name)}</div>` : ""}
      ${d.inputs.prompt ? `<div class="sub">${esc(d.inputs.prompt)}</div>` : ""}</div></div>
    <div class="detail">
      <section>
        <div class="stage hero ${heroUrl ? "" : "empty"}" id="hero">
          ${heroUrl ? `<div class="hero-tools"><div class="segmented" role="group" aria-label="Zoom">
              ${["fit", "1", "2", "4", "8"].map((z) => `<button data-zoom="${z}" aria-pressed="${state.zoom === z}">${z === "fit" ? "Fit" : z + "×"}</button>`).join("")}
            </div></div><img id="hero-img" src="${esc(heroUrl)}" alt="${esc(d.name)}">`
          : `<div class="hero-empty">${icon(d.kind === "animation" ? "film" : "sparkle")}<span>${d.kind === "animation"
              ? "Frames appear here once the poses are approved." : "The sprite appears here once it has been drawn."}</span></div>`}
          ${d.running ? `<span class="working"><span class="spinner"></span>${esc(currentStep(d))}</span>` : ""}
        </div>
        ${decisionPanel(d)}
      </section>
      <aside class="inspector">${inspector(d)}</aside>
    </div>`;
  wireDetail(d);
}

function currentStep(d) {
  const s = d.steps.find((s) => s.status === "partial" || s.status === "pending" || s.status === "failed");
  return s ? s.label : "Working";
}

function inspector(d) {
  const steps = d.steps.map((s, i) => {
    const running = d.running && s === d.steps.find((x) => x.status === "partial" || x.status === "pending");
    const cls = running ? "running" : s.status;
    const tail = s.status === "done" ? icon("check") : running ? `<span class="spinner"></span>` : s.status === "awaiting_gate" ? `<span class="dot wait"></span>` : "";
    return `<li class="${cls}" title="${esc(s.error || "")}"><span class="n">${i + 1}</span><span>${esc(STEP_LABEL(s))}</span>${tail}</li>`;
  }).join("");
  const facts = [["Spent", `$${(d.cost || 0).toFixed(2)}`]];
  if (d.inputs.height) facts.push(["Height", `${d.inputs.height} px`]);
  if (d.inputs.frames) facts.push(["Frames", d.inputs.frames]);
  if (d.character) facts.push(["Character", `<a class="link" href="#/item/${encodeURIComponent(d.character)}">${esc(d.character_name || "Open")}</a>`]);
  let html = `<div class="card"><h2>Progress</h2><ol class="steps">${steps}</ol>
      ${d.job_error ? `<div class="job-error"><strong>Stopped with an error.</strong> Fix the cause, then choose Try again.
        <details><summary>Details</summary><code>${esc(d.job_error)}</code></details></div>` : ""}</div>
    <div class="card"><h2>Details</h2><dl class="kv">${facts.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl></div>`;
  if (d.exports) {
    html += `<div class="card"><h2>Exports</h2><div class="list">${d.exports.filter((e) => e.url).map((e) => `
      <div class="row"><span class="grow">${esc(e.label)}</span><a href="${esc(e.url)}" download>Save</a></div>`).join("")}</div></div>`;
  }
  if (d.kind !== "animation" && d.status === "complete") {
    const anims = state.items.filter((i) => i.kind === "animation" && i.character === d.id);
    html += `<div class="card"><h2>Animations</h2>
      ${anims.length ? `<div class="list">${anims.map((a) => `
        <a class="row" href="#/item/${encodeURIComponent(a.id)}"><span class="stage thumb">${stageImg(a.thumb, a.name)}</span>
          <span class="grow">${esc(a.name)}</span></a>`).join("")}</div>` : `<p class="muted" style="margin:0 0 10px">None yet.</p>`}
      <button class="btn" id="btn-new-anim" style="margin-top:8px">${icon("plus")}New animation</button></div>`;
  }
  if (d.log.length) {
    html += `<div class="card"><details><summary>Activity</summary><div class="log">${esc(d.log.join("\n"))}</div></details></div>`;
  }
  return html;
}

function decisionPanel(d) {
  const g = d.gate_detail;
  if (d.paused && !d.job_error && !d.failed) {
    return `<div class="decision"><div class="decision-head"><span class="dot"></span><div><h2>Paused</h2>
        <div class="muted">The run stopped before it finished, for example because the app was closed. Nothing is lost.</div></div></div>
      <div class="decision-actions"><div class="spacer"></div><button class="btn primary lg" id="btn-continue">Continue</button></div></div>`;
  }
  if (d.running) {
    return `<div class="decision quiet"><span class="spinner"></span><div><strong>${esc(currentStep(d))}</strong>
      <div class="muted">You can leave this page. The run keeps going and stops when it needs you.</div></div></div>`;
  }
  if (!g) return "";
  const head = (title, sub) => `<div class="decision-head"><span class="dot wait"></span><div><h2>${esc(title)}</h2>
      ${sub ? `<div class="muted">${esc(sub)}</div>` : ""}</div></div>`;
  const noteRow = (placeholder, label) => `<div class="note-row"><input class="input" id="note" placeholder="${esc(placeholder)}">
      <button class="btn" id="btn-reject">${esc(label)}</button></div>`;

  if (g.kind === "spec") {
    const s = g.spec;
    return `<div class="decision">${head("Check the plan", "This is what will be drawn. Change anything, then continue.")}
      <div class="spec-grid">
        <div class="field"><label for="sp-name">Name</label><input class="input" id="sp-name" value="${esc(s.name)}"></div>
        <div class="field"><span class="label">Height</span><div class="segmented" id="sp-h">
          ${[64, 96, 128].map((h) => `<button data-v="${h}" aria-pressed="${s.height_px === h}">${h} px</button>`).join("")}</div></div>
        <div class="field full"><label for="sp-sum">Description</label><textarea class="textarea" id="sp-sum">${esc(s.summary)}</textarea></div>
        <div class="field"><span class="label">View</span><div class="segmented" id="sp-view">
          ${[["front", "Front"], ["three_quarter", "Three-quarter"], ["side", "Side"], ["back", "Back"]].map(([v, l]) =>
            `<button data-v="${v}" aria-pressed="${s.view === v}">${l}</button>`).join("")}</div></div>
        <div class="field full"><label for="sp-keep">Must keep</label>
          <textarea class="textarea" id="sp-keep" style="min-height:72px" placeholder="One feature per line">${esc((s.must_keep || []).join("\n"))}</textarea>
          <span class="hint">Identity details that have to survive at pixel size.</span></div>
      </div>
      <div class="decision-actions"><div class="spacer"></div><button class="btn primary lg" id="btn-approve">Continue</button></div></div>`;
  }
  if (g.kind === "concept") {
    return `<div class="decision">${head("Pick a concept", "The sprite will be drawn from the one you choose.")}
      <div class="picker" style="--n:${Math.min(4, g.candidates.length)}">${g.candidates.map((u, i) => `
        <button class="pick" data-pick="${i}" aria-pressed="${state.pick === i}"><span class="stage">${stageImg(u, "Concept " + (i + 1))}</span>
          <span>Concept ${i + 1}</span></button>`).join("")}</div>
      <div class="decision-actions"><input class="input" id="note" placeholder="Not right? Say what to change" style="flex:1">
        <button class="btn" id="btn-reject">New concepts</button>
        <button class="btn primary lg" id="btn-approve" ${state.pick === null ? "disabled" : ""}>Use this concept</button></div></div>`;
  }
  if (g.kind === "sprite") {
    const c = g.critique;
    return `<div class="decision">${head("Approve the sprite", c ? `Quality check: ${c.overall} out of 10` : "")}
      ${c && c.issues.length ? `<ul class="issues">${c.issues.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      ${noteRow("What should change? For example: make the eyes larger", "Redraw")}
      <div class="decision-actions"><div class="spacer"></div><button class="btn primary lg" id="btn-approve">Approve and export</button></div></div>`;
  }
  if (g.kind === "poses") {
    return `<div class="decision">${head("Approve the poses", "Drawing starts once you approve. Rejecting only redraws this sheet.")}
      <img class="wide-img" src="${esc(g.image)}" alt="Pose sheet">
      <div class="frames">${g.frames.map((f, i) => `<div><b>Frame ${i + 1}</b> <span class="ms">${f.ms} ms</span><br>${esc(f.pose)}</div>`).join("")}</div>
      ${noteRow("Not right? For example: longer strides", "New poses")}
      <div class="decision-actions"><div class="spacer"></div><button class="btn primary lg" id="btn-approve">Approve and draw frames</button></div></div>`;
  }
  if (g.kind === "animation") {
    return `<div class="decision">${head("Approve the animation", "Playing above at 4×.")}
      <img class="wide-img" src="${esc(g.strip)}" alt="All frames">
      ${g.issues && g.issues.length ? `<ul class="issues">${g.issues.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      ${noteRow("What should change? For example: more bounce in the body", "Redraw frames")}
      <div class="decision-actions"><div class="spacer"></div><button class="btn primary lg" id="btn-approve">Approve and export</button></div></div>`;
  }
  return "";
}

function wireDetail(d) {
  const img = $("#hero-img");
  if (img) {
    const apply = () => {
      const hero = $("#hero");
      const fit = Math.min((hero.clientWidth - 40) / img.naturalWidth, (hero.clientHeight - 40) / img.naturalHeight);
      const z = state.zoom === "fit" ? (fit >= 1 ? Math.floor(fit) : 1 / Math.ceil(1 / fit)) : Number(state.zoom);
      const isPixel = img.naturalWidth <= 512;
      img.style.width = isPixel ? Math.round(img.naturalWidth * z) + "px" : "auto";
      img.style.maxWidth = isPixel ? "none" : "92%";
      img.style.maxHeight = isPixel ? "none" : "92%";
    };
    img.complete ? apply() : img.addEventListener("load", apply);
    window.onresize = apply;
    document.querySelectorAll("[data-zoom]").forEach((b) => b.onclick = () => {
      state.zoom = b.dataset.zoom;
      document.querySelectorAll("[data-zoom]").forEach((x) => x.setAttribute("aria-pressed", x === b));
      apply();
    });
  }
  const p = project();
  const g = d.gate_detail;
  const on = (sel, fn) => { const el = $(sel); if (el) el.onclick = fn; };
  on("#btn-retry", () => act(() => api("/api/run", { project: p, id: d.id }), "Started again"));
  on("#btn-continue", () => act(() => api("/api/run", { project: p, id: d.id }), "Continuing"));
  on("#btn-pl", () => act(() => api("/api/open", { project: p, id: d.id, what: "pixelliner" }), "Opening in PixelLiner"));
  on("#btn-folder", () => act(() => api("/api/open", { project: p, id: d.id, what: "folder" })));
  on("#btn-new-anim", () => sheetNewAnimation(d));
  document.querySelectorAll("#sp-h button, #sp-view button").forEach((b) => b.onclick = () => {
    b.parentElement.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
  });
  document.querySelectorAll("[data-pick]").forEach((b) => b.onclick = () => {
    state.pick = Number(b.dataset.pick);
    document.querySelectorAll("[data-pick]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    $("#btn-approve").disabled = false;
    const hi = $("#hero-img"), src = d.gate_detail && d.gate_detail.candidates && d.gate_detail.candidates[state.pick];
    if (hi && src) hi.src = src;  // the big stage previews the concept being chosen
  });
  on("#btn-approve", () => {
    const body = { project: p, id: d.id, approve: true };
    if (g.kind === "concept") body.pick = state.pick;
    if (g.kind === "spec") {
      body.spec = Object.assign({}, g.spec, {
        name: $("#sp-name").value.trim() || g.spec.name,
        summary: $("#sp-sum").value.trim(),
        height_px: Number($("#sp-h [aria-pressed=true]").dataset.v),
        view: $("#sp-view [aria-pressed=true]").dataset.v,
        must_keep: $("#sp-keep").value.split("\n").map((x) => x.trim()).filter(Boolean),
      });
    }
    const msg = { spec: "Plan confirmed. Drawing concepts", concept: "Concept chosen. Drawing the sprite", sprite: "Approved and exported",
      poses: "Poses approved. Drawing frames", animation: "Approved and exported" }[g.kind];
    state.pick = null;
    act(() => api("/api/decide", body), msg);
  });
  on("#btn-reject", () => {
    const note = ($("#note") || {}).value || "";
    if (!note.trim()) { toast("Say what should change first", true); $("#note").focus(); return; }
    act(() => api("/api/decide", { project: p, id: d.id, approve: false, note }), "Got it. Working on it");
  });
}

// ------------------------------------------------------------------ polling

// heartbeat: the packaged app quits a while after its last window closes
setInterval(() => fetch("/api/ping").catch(() => {}), 30000);

function schedulePoll(ms) {
  clearTimeout(state.pollTimer);
  if (ms) state.pollTimer = setTimeout(() => refresh(false), ms);
}

// ------------------------------------------------------------------ sheets

function openSheet(html, wire) {
  $("#sheet").innerHTML = html;
  $("#sheet-backdrop").hidden = false;
  const first = $("#sheet input, #sheet textarea");
  if (first) setTimeout(() => first.focus(), 30);
  wire($("#sheet"));
}
function closeSheet() { $("#sheet-backdrop").hidden = true; $("#sheet").innerHTML = ""; }
function sheetDirty() {
  return [...document.querySelectorAll("#sheet input, #sheet textarea")].some((el) => el.type !== "file" && el.value.trim())
    || !!document.querySelector("#sheet .drop.has-image");
}
// Like a macOS sheet: an outside click never throws away what was typed; it only nudges the sheet.
$("#sheet-backdrop").addEventListener("mousedown", (e) => {
  if (e.target.id !== "sheet-backdrop") return;
  if (!sheetDirty()) return closeSheet();
  const s = $("#sheet"); s.classList.remove("nudge"); void s.offsetWidth; s.classList.add("nudge");
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#sheet-backdrop").hidden) closeSheet(); });

function segmentedValue(el) { return el.querySelector("[aria-pressed=true]").dataset.v; }
function wireSegmented(root) {
  root.querySelectorAll(".segmented button").forEach((b) => b.addEventListener("click", () => {
    b.parentElement.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
  }));
}

function sheetNewProject() {
  openSheet(`<h2>New project</h2><p class="sub">One project per game. Characters in a project share its settings.</p>
    <div class="field"><label for="pn">Name</label><input class="input" id="pn" placeholder="My game"></div>
    <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="pc">Create project</button></div>`, (s) => {
    s.querySelector("[data-close]").onclick = closeSheet;
    const go = async () => {
      const name = $("#pn").value.trim();
      if (!name) return toast("Give the project a name", true);
      await act(() => api("/api/projects", { name }), "Project created");
      closeSheet();
      location.hash = "#/characters";
    };
    $("#pc").onclick = go;
    $("#pn").onkeydown = (e) => { if (e.key === "Enter") go(); };
  });
}

function sheetNewCharacter() {
  const h = state.overview.settings.sprite_height;
  let reference = null;
  openSheet(`<h2>New character</h2><p class="sub">You'll confirm the plan and pick a concept before anything is drawn.</p>
    <div class="stack">
      <div class="segmented" id="nc-mode" role="group" aria-label="Start from">
        <button data-v="text" aria-pressed="true">${icon("text")} Describe</button>
        <button data-v="ref" aria-pressed="false">${icon("photo")} Reference image</button></div>
      <div class="field" id="nc-text"><label for="nc-prompt">Description</label>
        <textarea class="textarea" id="nc-prompt" placeholder="A young forest ranger with short green hair, a hooded brown cloak, a wooden bow and a small quiver. Calm expression."></textarea></div>
      <div class="field" id="nc-ref" hidden><span class="label">Reference image</span>
        <label class="drop" id="nc-drop">${icon("photo")}<span>Drop an image here, or click to choose</span>
          <input type="file" accept="image/png,image/jpeg,image/webp" hidden id="nc-file"></label>
        <textarea class="textarea" id="nc-prompt2" style="min-height:60px" placeholder="Optional notes, for example: keep the red scarf"></textarea></div>
      <div class="row-2"><div class="field"><label for="nc-name">Name</label><input class="input" id="nc-name" placeholder="Optional"></div>
        <div class="field"><span class="label">Height</span><div class="segmented" id="nc-h">
          ${[64, 96, 128].map((v) => `<button data-v="${v}" aria-pressed="${v === h}">${v} px</button>`).join("")}</div></div></div>
    </div>
    <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="nc-go">Create character</button></div>`, (s) => {
    wireSegmented(s);
    s.querySelector("[data-close]").onclick = closeSheet;
    s.querySelectorAll("#nc-mode button").forEach((b) => b.addEventListener("click", () => {
      const ref = segmentedValue($("#nc-mode")) === "ref";
      $("#nc-text").hidden = ref;
      $("#nc-ref").hidden = !ref;
    }));
    const drop = $("#nc-drop");
    const take = (file) => {
      if (!file || !/^image\/(png|jpeg|webp)$/.test(file.type)) return toast("Use a PNG, JPG or WEBP image", true);
      const rd = new FileReader();
      rd.onload = () => { reference = rd.result; drop.innerHTML = `<img src="${reference}" alt="Reference">`; drop.classList.add("has-image"); };
      rd.readAsDataURL(file);
    };
    $("#nc-file").onchange = (e) => take(e.target.files[0]);
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); take(e.dataTransfer.files[0]); };
    $("#nc-go").onclick = async () => {
      const ref = segmentedValue($("#nc-mode")) === "ref";
      const body = { project: project(), name: $("#nc-name").value.trim(), height: Number(segmentedValue($("#nc-h"))),
        prompt: (ref ? $("#nc-prompt2") : $("#nc-prompt")).value.trim(), reference: ref ? reference : null };
      if (ref && !reference) return toast("Add a reference image first", true);
      if (!ref && !body.prompt) return toast("Describe the character first", true);
      const r = await act(() => api("/api/characters", body), "Planning your character");
      if (r) { closeSheet(); location.hash = `#/item/${encodeURIComponent(r.id)}`; }
    };
  });
}

function sheetNewAnimation(d) {
  const presets = [["Idle", "4-frame idle: gentle breathing, the body rises and falls by one pixel"],
    ["Walk cycle", "6-frame walk cycle in place"], ["Run cycle", "6-frame run cycle in place, leaning forward"],
    ["Attack", "5-frame attack: wind up, strike, follow through, recover"], ["Jump", "6-frame jump: crouch, take off, rise, peak, fall, land"],
    ["Hurt", "3-frame hurt reaction: recoil backwards and recover"]];
  openSheet(`<h2>New animation</h2><p class="sub">For ${esc(d.name)}. Describe the motion in your own words; you'll approve the poses before frames are drawn.</p>
    <div class="stack">
      <div class="field"><label for="na-prompt">Motion</label>
        <textarea class="textarea" id="na-prompt" placeholder="6-frame walk cycle in place, the backpack bobs slightly"></textarea>
        <div class="chips">${presets.map(([l, t]) => `<button class="chip" data-text="${esc(t)}">${l}</button>`).join("")}</div></div>
      <div class="row-2"><div class="field"><label for="na-name">Name</label><input class="input" id="na-name" placeholder="walk"></div>
        <div class="field"><span class="label">Frames</span><div class="segmented" id="na-f">
          ${[["", "Auto"], ["4", "4"], ["6", "6"], ["8", "8"]].map(([v, l]) => `<button data-v="${v}" aria-pressed="${v === ""}">${l}</button>`).join("")}</div></div></div>
    </div>
    <div class="actions"><button class="btn" data-close>Cancel</button><button class="btn primary" id="na-go">Create animation</button></div>`, (s) => {
    wireSegmented(s);
    s.querySelector("[data-close]").onclick = closeSheet;
    s.querySelectorAll("[data-text]").forEach((b) => b.onclick = () => {
      $("#na-prompt").value = b.dataset.text;
      if (!$("#na-name").value) $("#na-name").value = b.textContent.toLowerCase().replace(/ cycle/, "");
    });
    $("#na-go").onclick = async () => {
      const prompt = $("#na-prompt").value.trim();
      if (!prompt) return toast("Describe the motion first", true);
      const r = await act(() => api("/api/animations", { project: project(), character: d.id, prompt,
        name: $("#na-name").value.trim(), frames: segmentedValue($("#na-f")) || null }), "Planning the animation");
      if (r) { closeSheet(); location.hash = `#/item/${encodeURIComponent(r.id)}`; }
    };
  });
}

// ------------------------------------------------------------------ settings

function renderSettings() {
  const s = state.overview.settings;
  setToolbar(`<div class="title">Settings</div>`);
  schedulePoll(0);
  $("#content").innerHTML = `<div class="settings">
    <div class="page-head"><div><h1>Settings</h1></div></div>
    <div class="group-title">OpenAI</div>
    <div class="group">
      <div class="setting"><div class="grow">API key<div class="muted">Stored in your Windows user environment, never in project files.</div></div>
        ${s.key_found ? `<span class="status-ok">${icon("check")}Found</span>` : `<span class="status-bad">Not set</span>`}</div>
      <div class="setting"><div class="grow"><input class="input" id="st-key" type="password" placeholder="${s.key_found ? "Replace the key" : "Paste your API key"}" autocomplete="off" style="width:100%"></div>
        <button class="btn" id="st-key-save">${icon("key")}Save key</button></div>
    </div>
    <div class="group-title">Generation</div>
    <div class="group">
      <div class="setting"><div class="grow">Mode<div class="muted">Test runs use stand-in models: no cost, placeholder pictures.</div></div>
        <div class="segmented" id="st-mode">${[["openai", "OpenAI"], ["dry_run", "Test run"]].map(([v, l]) => `<button data-v="${v}" aria-pressed="${s.backend === v}">${l}</button>`).join("")}</div></div>
      <div class="setting"><div class="grow">Default height<div class="muted">96 px keeps faces readable.</div></div>
        <div class="segmented" id="st-h">${[64, 96, 128].map((v) => `<button data-v="${v}" aria-pressed="${Number(s.sprite_height) === v}">${v} px</button>`).join("")}</div></div>
      <div class="setting"><div class="grow">Monthly limit<div class="muted">Work stops before spending more than this (estimated).</div></div>
        <label class="affix"><span>US$</span><input class="input" id="st-cap" type="number" min="1" step="1" value="${esc(s.monthly_usd)}" aria-label="Monthly limit in US dollars"></label></div>
    </div>
    <div class="group-title">Storage</div>
    <div class="group">
      <div class="setting"><div class="grow">Projects folder<div class="muted">Each project is a folder inside it.</div></div>
        <input class="input" id="st-dir" value="${esc(s.projects_dir)}" style="width:300px"></div>
    </div>
    <div class="decision-actions"><div class="spacer"></div><button class="btn primary lg" id="st-save">Save settings</button></div>
  </div>`;
  wireSegmented($("#content"));
  $("#st-save").onclick = () => act(() => api("/api/settings", {
    backend: segmentedValue($("#st-mode")), sprite_height: Number(segmentedValue($("#st-h"))),
    monthly_usd: Number($("#st-cap").value), projects_dir: $("#st-dir").value.trim(),
  }), "Settings saved");
  $("#st-key-save").onclick = async () => {
    const key = $("#st-key").value.trim();
    if (!key) return toast("Paste the key first", true);
    await act(() => api("/api/settings", { api_key: key }), "Key saved");
    $("#st-key").value = "";
  };
}

// ------------------------------------------------------------------ start

(async () => {
  try {
    await refresh(true);
  } catch (e) {
    $("#content").innerHTML = emptyState("x", "PixelAgent could not start", e.message, "");
  }
})();
