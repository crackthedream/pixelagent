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

const state = { overview: null, items: [], detailSig: "", pollTimer: null, zoom: "fit", pick: null, viewVersion: null,
  area: null, selecting: false };

// ------------------------------------------------------------------ api

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok || data.error) {
    const err = new Error(data.error || `HTTP ${r.status}`);
    err.status = r.status;
    err.notFound = !!data.not_found;
    throw err;
  }
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
  // same rule as the "Needs you" filter: waiting at a gate, paused, or stopped
  const wait = (k) => state.items.filter((i) => i.kind === k && !i.running && (i.gate || i.paused || i.failed)).length;
  for (const [k, id] of [["character", "badge-characters"], ["object", "badge-objects"], ["scene", "badge-scenes"], ["kit", "badge-kits"], ["animation", "badge-animations"]]) {
    const n = wait(k);
    const b = $("#" + id);
    b.hidden = !n;
    b.textContent = n;
  }
}

// ------------------------------------------------------------------ spending

const testMode = () => state.overview && state.overview.settings.backend === "dry_run";
function estimateText(usd) {
  return testMode() ? "Test run: no cost" : `About $${usd.toFixed(2)} (estimate)`;
}
function characterEstimate(height) {
  const e = state.overview.estimates.character;
  return e[String(height)] ?? e["96"];
}
function animationEstimate(frames) {
  const e = state.overview.estimates;
  return e.animation_base + e.animation_per_frame * (Number(frames) || 6);
}
function spendBanner() {
  const o = state.overview;
  if (!o || testMode()) return "";
  const cap = Number(o.settings.monthly_usd) || 0, spent = o.month_spent;
  if (!cap || spent < cap * 0.8) return "";
  const text = spent >= cap
    ? `This month's $${cap.toFixed(0)} limit is used up ($${spent.toFixed(2)} spent). New work won't start until next month, unless you raise the limit in Settings.`
    : `You've used $${spent.toFixed(2)} of this month's $${cap.toFixed(0)} limit. Work stops when the limit is reached.`;
  return `<div class="banner ${spent >= cap ? "bad" : "warn"}"><span>${esc(text)}</span><a class="link" href="#/settings">Settings</a></div>`;
}

// ------------------------------------------------------------------ sidebar

function animationsOn() {
  const f = state.overview && state.overview.features;
  return !!(f && f.animations);
}

function renderSidebar() {
  const o = state.overview;
  $("#project-list").innerHTML = o.projects.map((p) => `
    <div class="side-row"><button class="side-item project-item ${p === o.current ? "current active" : ""}" data-project="${esc(p)}">
      ${icon("folder")}<span>${esc(p)}</span></button>
      <button class="side-more" data-project-more="${esc(p)}" aria-label="Actions for ${esc(p)}">${icon("more")}</button></div>`).join("");
  // Scenes and Kits are experimental (config.toml [app] backgrounds): hidden unless switched on
  for (const el of document.querySelectorAll(".side-item[data-view=scenes], .side-item[data-view=kits]")) el.hidden = !(o.features && o.features.backgrounds);
  // Animations too (config.toml [app] animations)
  for (const el of document.querySelectorAll(".side-item[data-view=animations]")) el.hidden = !animationsOn();
  const dc = $("#count-deleted");
  dc.hidden = !o.trash_count;
  dc.textContent = o.trash_count || "";
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
  const more = e.target.closest("[data-project-more]");
  if (more) { openMenu(more, projectMenu(more.dataset.projectMore)); return; }
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

window.addEventListener("hashchange", () => {
  state.detailSig = ""; state.zoom = "fit"; state.pick = null; state.viewVersion = null; state.area = null; state.selecting = false;
  render();
});

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
  if (r.view === "deleted") return renderDeleted();
  if (!project()) return renderNoProject();
  if (r.view === "item" && r.id) return renderDetail(r.id, fromRefresh);
  if (!fromRefresh) await loadItems();
  sessionStorage.setItem("from", r.view);
  return renderLibrary({ animations: "animation", objects: "object", scenes: "scene", kits: "kit" }[r.view] || "character");
}

function setToolbar(html) {
  // narrow windows: the sidebar collapses behind this button, as in macOS apps
  $("#toolbar").innerHTML = `<button class="btn icon-only plain sidebar-toggle" id="btn-sidebar" aria-label="Show sidebar"
      aria-controls="sidebar">${icon("sidebar")}</button>` + html;
  $("#btn-sidebar").onclick = () => $("#app").classList.toggle("sidebar-open");
}
$("#sidebar-scrim").addEventListener("click", () => $("#app").classList.remove("sidebar-open"));
document.addEventListener("click", (e) => {  // choosing anything in the slid-in sidebar closes it
  if (e.target.closest(".sidebar .side-item")) $("#app").classList.remove("sidebar-open");
});

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
  if (img.naturalWidth > 512) { img.style.width = ""; img.style.imageRendering = "auto"; return; }  // a painting (concept), not pixel art
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
  const plural = { animation: "Animations", object: "Objects", scene: "Scenes", kit: "Kits" }[kind] || "Characters";
  const filter = sessionStorage.getItem("filter-" + kind) || "all";
  setToolbar(`
    <div class="title">${esc(project())}</div><div class="spacer"></div>
    <div class="segmented" role="group" aria-label="Show">
      ${[["all", "All"], ["needs", "Needs you"], ["working", "Working"], ["ready", "Ready"]].map(([v, l]) =>
        `<button data-filter="${v}" aria-pressed="${filter === v}">${l}</button>`).join("")}
    </div>
    ${kind === "character" ? `<button class="btn primary round" id="btn-new">${icon("plus")}New character</button>`
      : kind === "scene" ? `<button class="btn primary round" id="btn-new">${icon("plus")}New scene</button>`
      : kind === "kit" ? `<button class="btn primary round" id="btn-new">${icon("plus")}New kit</button>`
      : kind === "object" ? `${state.items.some((i) => i.kind === "object" && i.status === "complete")
        ? `<button class="btn round" id="btn-sheet" title="Every finished object in one atlas, for a game engine">${icon("export")}Item sheet</button>` : ""}
        <button class="btn primary round" id="btn-new">${icon("plus")}New object</button>` : ""}`);
  document.querySelectorAll("[data-filter]").forEach((b) => b.onclick = () => { sessionStorage.setItem("filter-" + kind, b.dataset.filter); render(); });
  const nb = $("#btn-new");
  if (nb) nb.onclick = () => (kind === "object" ? sheetNewObject() : kind === "scene" ? sheetNewScene()
    : kind === "kit" ? sheetNewKit() : sheetNewCharacter());
  const sb = $("#btn-sheet");
  if (sb) sb.onclick = async () => {
    const r = await act(() => api("/api/itemsheet", { project: project() }));
    if (r) {
      const link = document.createElement("a");
      link.href = r.url; link.download = ""; document.body.appendChild(link); link.click(); link.remove();
      toast(`Item sheet: ${r.items.length} object${r.items.length === 1 ? "" : "s"} in ${r.cell} px cells`);
    }
  };

  let items = state.items.filter((i) => i.kind === kind);
  const all = items.length;
  if (filter === "needs") items = items.filter((i) => !i.running && (i.gate || i.paused || i.failed));
  if (filter === "working") items = items.filter((i) => i.running);
  if (filter === "ready") items = items.filter((i) => i.status === "complete");

  const c = $("#content");
  if (!all) {
    c.innerHTML = kind === "kit" ? emptyState("grid", "No kits yet",
      "Describe the ground of a place: tiles to walk on, a platform and decorations, in pixel art that fits your scene.",
      `<button class="btn primary lg round" id="empty-new">${icon("plus")}New kit</button>`)
      : kind === "scene" ? emptyState("mountain", "No scenes yet",
      "Describe a place: a forest at dusk, a town street, a crystal cave. It becomes parallax layers in pixel art at your characters' scale.",
      `<button class="btn primary lg round" id="empty-new">${icon("plus")}New scene</button>`)
      : kind === "object" ? emptyState("box", "No objects yet",
      "Describe an item or a prop, or drop in a reference image: a potion, a sword, a chest. It is drawn as pixel art at its game size.",
      `<button class="btn primary lg round" id="empty-new">${icon("plus")}New object</button>`)
      : kind === "character" ? emptyState("person", "No characters yet",
      "Describe a character or drop in a reference image. You'll check the plan, pick a concept and approve the sprite along the way.",
      `<button class="btn primary lg round" id="empty-new">${icon("plus")}New character</button>`)
      : emptyState("film", "No animations yet", "Open a finished character and choose New animation. Describe the motion in your own words.", "");
    const en = $("#empty-new");
    if (en) en.onclick = () => (kind === "object" ? sheetNewObject() : kind === "scene" ? sheetNewScene()
      : kind === "kit" ? sheetNewKit() : sheetNewCharacter());
    return;
  }
  c.innerHTML = `${spendBanner()}
    <div class="page-head"><div><h1>${plural}</h1></div></div>
    ${items.length ? `<div class="grid">${items.map((i) => `
      <a class="tile" href="#/item/${encodeURIComponent(i.id)}" data-id="${esc(i.id)}" aria-label="${esc(i.name)}">
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
    setToolbar(`<div class="title">${esc(project())}</div>`);
    $("#content").innerHTML = e.notFound
      ? emptyState("folder", "Not in this project", `It isn't in “${project()}”. It may have been deleted, or it belongs to another project.`,
          `<a class="btn round" href="#/${sessionStorage.getItem("from") || "characters"}">${icon("chevron-left")}Back to the library</a>`)
      : emptyState("x", "This item could not be opened", e.message, "");
    return;
  }
  const sig = JSON.stringify([d.status, d.running, d.steps.map((s) => [s.status, s.version]), d.gate, d.log.length, d.job_error,
    d.live && d.live.url]);
  schedulePoll(d.running ? 1500 : 0);
  if (fromRefresh && sig === state.detailSig) return;   // nothing changed: keep the page (and anything typed) as is
  state.detailSig = sig;
  if (d.live) await new Promise((res) => { const im = new Image(); im.onload = im.onerror = res; im.src = d.live.url; });

  const from = { animation: "animations", object: "objects", scene: "scenes", kit: "kits" }[d.kind] || "characters";
  sessionStorage.setItem("from", from);
  renderSidebar();
  const backLabel = { animation: "Animations", object: "Objects", scene: "Scenes", kit: "Kits" }[d.kind] || "Characters";
  setToolbar(`<div class="title">${esc(d.name)}</div><div class="spacer"></div>
    ${d.failed || d.job_error ? `<button class="btn" id="btn-retry">${icon("retry")}Try again</button>` : ""}
    ${d.exports ? `<button class="btn" id="btn-folder">${icon("folder")}Show in folder</button>
                   <button class="btn primary" id="btn-pl">${icon("export")}Open in PixelLiner</button>` : ""}
    <button class="btn icon-only" id="btn-more" aria-label="More actions" aria-haspopup="menu">${icon("more")}</button>`);

  const gd = d.gate_detail;
  const viewing = !d.running && state.viewVersion !== null
    ? (d.versions.items.find((v) => v.version === state.viewVersion && !v.current) || null) : null;
  const heroUrl = viewing ? viewing.url
    : (d.kind === "animation" && gd && (gd.id === "R" || gd.id === "P")) || (d.kind === "scene" && gd && gd.id === "S") || (d.kind === "kit" && gd && gd.id === "K") ? gd.image
    : gd && gd.candidates && state.pick !== null && gd.candidates[state.pick] ? gd.candidates[state.pick]
    : d.live ? d.live.url : d.hero;
  $("#content").innerHTML = `${spendBanner()}
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
          ${d.live ? `<span class="live-badge"><span class="live-dot"></span>${esc(liveText(d.live))}</span>` : ""}
          ${d.running ? `<span class="working"><span class="spinner"></span>${esc(currentStep(d))}</span>` : ""}
          ${viewing ? `<div class="version-bar"><span>Version ${viewing.version}</span>
              ${viewing.compatible ? "" : `<span class="muted vb-note">Made from an earlier plan or concept</span>`}<div class="spacer"></div>
              <button class="btn" id="vb-back">Show current</button>
              ${viewing.compatible ? `<button class="btn primary" id="vb-use">Use this version</button>` : ""}</div>` : ""}
        </div>
        ${decisionPanel(d)}
      </section>
      <aside class="inspector">${inspector(d)}</aside>
    </div>`;
  wireDetail(d);
}

function reviseEstimate(area) {
  const e = state.overview.estimates;
  return estimateText(area ? e.revise_area : e.revise_whole);
}

function areaLabel(a) { return `x ${a.x}, y ${a.y}, ${a.w} × ${a.h} px`; }

// a note, optionally with an area dragged on the sprite: the model revises the current drawing (only inside the area)
function reviseControls(d) {
  const a = state.area;
  return `<div class="revise">
      <div class="note-row"><input class="input" id="note" placeholder="${a ? "What should change in this area?" : "What should change? For example: make the eyes larger"}">
        <button class="btn" id="btn-revise">${a ? "Fix this area" : "Revise"}</button></div>
      <div class="revise-meta">
        ${a ? `<span class="area-chip">${icon("photo")}${esc(areaLabel(a))}<button class="chip-x" id="area-clear" aria-label="Clear the area">${icon("x")}</button></span>`
          : `<button class="btn plain" id="btn-area">${state.selecting ? "Drag on the sprite to select an area" : "Select an area"}</button>`}
        <span class="muted">${esc(reviseEstimate(!!a))}</span>
        <span class="muted hint">To start over instead, use Redo next to Drawing in Progress.</span>
      </div></div>`;
}

function stopMessage(err) {
  if (err.startsWith("BudgetExceeded:")) {
    const why = err.slice("BudgetExceeded:".length).trim();
    return `<div class="job-error"><strong>Stopped at a spending limit.</strong> ${esc(why.charAt(0).toUpperCase() + why.slice(1))}.
      <div style="margin-top:6px"><a class="link" href="#/settings">Open Settings</a></div></div>`;
  }
  return `<div class="job-error"><strong>Stopped with an error.</strong> Fix the cause, then choose Try again.
    <details><summary>Details</summary><code>${esc(err)}</code></details></div>`;
}

function revisePanel(d) {
  return `<div class="decision quiet-card"><div class="decision-head"><div><h2>Revise</h2>
      <div class="muted">Describe a change. The model adjusts the current sprite (or only the area you select) and you approve the result.</div></div></div>
    ${reviseControls(d)}</div>`;
}

function liveText(l) {
  const what = l.frame !== null && l.frame !== undefined ? `Drawing frame ${l.frame + 1} of ${l.frames}` : "Drawing live";
  return l.turn ? `${what}, round ${l.turn}` : what;
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
    const canRedo = !d.running && (s.status === "done" || s.status === "partial") && d.redo && s.label in d.redo
      && !["Your approval", "Export"].includes(s.label);
    const redo = canRedo ? `<button class="redo" data-redo="${esc(s.label)}" aria-label="Redo from ${esc(s.label)}"
      title="Redo from here">${icon("retry")}</button>` : "";
    return `<li class="${cls}" title="${esc(s.error || "")}"><span class="n">${i + 1}</span><span>${esc(STEP_LABEL(s))}</span>${redo}${tail}</li>`;
  }).join("");
  const facts = [["Spent", `$${(d.cost || 0).toFixed(2)}`]];
  if (d.inputs.height) facts.push(["Height", `${d.inputs.height} px`]);
  if (d.inputs.frames) facts.push(["Frames", d.inputs.frames]);
  if (d.quality && d.quality.overall != null) facts.push(["Quality check", `${esc(String(d.quality.overall))} / 10 <span class="muted">(model critic)</span>${d.quality.issues.length ? `<details class="muted small"><summary>${d.quality.issues.length} note${d.quality.issues.length === 1 ? "" : "s"}</summary>${d.quality.issues.map((i) => `<div>${esc(i)}</div>`).join("")}</details>` : ""}`]);
  if (d.proportions) facts.push(["Proportions", `${Number(d.proportions.heads_tall).toFixed(1)} heads tall${d.proportions.retries ? ` <span class="muted">(concept redone ${d.proportions.retries === 1 ? "once" : d.proportions.retries + " times"} to fit)</span>` : ""}`]);
  if (d.reference) facts.push(["Reference", `<a class="link" href="${esc(d.reference)}" target="_blank" rel="noopener">View image</a>`]);
  if (d.character) facts.push(["Character", `<a class="link" href="#/item/${encodeURIComponent(d.character)}">${esc(d.character_name || "Open")}</a>`]);
  let html = `<div class="card"><h2>Progress</h2><ol class="steps">${steps}</ol>
      ${d.job_error ? stopMessage(d.job_error) : ""}</div>
    <div class="card"><h2>Details</h2><dl class="kv">${facts.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl></div>`;
  const vers = (d.versions && d.versions.items) || [];
  if (vers.length > 1) {
    html += `<div class="card"><h2>Versions</h2><div class="versions">${vers.map((v) => `
      <button class="version ${v.current ? "current" : ""} ${v.compatible ? "" : "old"} ${state.viewVersion === v.version ? "viewing" : ""}" data-version="${v.version}"
        aria-label="Version ${v.version}${v.current ? ", current" : ""}"><span class="stage">${stageImg(v.url, "Version " + v.version)}</span>
        <span class="cap">${v.version}${v.current ? ` <span class="tag">Current</span>` : ""}</span></button>`).join("")}</div>
      ${vers.some((v) => !v.compatible) ? `<p class="muted small">Dimmed versions were made from an earlier plan or concept. You can look at them, but not use them now.</p>` : ""}</div>`;
  }
  if (d.exports) {
    html += `<div class="card"><h2>Exports</h2><div class="list">${d.exports.filter((e) => e.url).map((e) => `
      <div class="row"><span class="grow">${esc(e.label)}</span><a href="${esc(e.url)}" download>Save</a></div>`).join("")}
      ${d.kind === "character" ? `<div class="row"><span class="grow">Game pack (ZIP)<div class="muted small">${animationsOn() ? "Atlas with every animation and face" : "Atlas with the sprite and every face"}, for Godot, Unity, Aseprite JSON</div></span><button class="btn" id="btn-pack">Save</button></div>` : ""}</div></div>`;
  }
  const anims = state.items.filter((i) => i.kind === "animation" && i.character === d.id);
  // switched off: no new ones; animations made earlier stay listed
  if (d.kind === "character" && d.status === "complete" && (animationsOn() || anims.length)) {
    html += `<div class="card"><h2>Animations</h2>
      ${anims.length ? `<div class="list">${anims.map((a) => `
        <a class="row" href="#/item/${encodeURIComponent(a.id)}"><span class="stage thumb">${stageImg(a.thumb, a.name)}</span>
          <span class="grow">${esc(a.name)}</span></a>`).join("")}</div>` : `<p class="muted" style="margin:0 0 10px">None yet.</p>`}
      ${animationsOn() ? `<div class="row-actions" style="margin-top:8px"><button class="btn" id="btn-new-anim">${icon("plus")}New animation</button>
        <button class="btn" id="btn-move-set">${icon("plus")}Move set</button></div>` : ""}</div>`;
  }
  if (d.kind === "character" && d.status === "complete") {
    const ex = d.expressions || [];
    html += `<div class="card"><h2>Expressions</h2>
      ${ex.length ? `<div class="expr-grid">${ex.map((e) => `<figure class="expr"><a class="stage" href="${esc(e.x4)}" target="_blank" rel="noopener" title="${esc(e.note || "Open the whole sprite at 4×")}">${stageImg(e.face, e.name)}</a>
        <figcaption>${esc(e.name)} <a href="${esc(e.url)}" download>PNG</a> <a href="${esc(e.face)}" download>Face</a>${e.passed === false ? `<span class="expr-warn" title="${esc(e.issue || "This face did not pass its check against the original.")}">Check this face</span>` : ""}</figcaption></figure>`).join("")}</div>`
        : `<p class="muted" style="margin:0 0 10px">The same sprite with another face: happy, sad, angry, or any face you describe. Only the face changes, so a game can swap the face alone.</p>`}
      <button class="btn" id="btn-expr" style="margin-top:8px">${icon("plus")}${ex.length ? "Make more" : "Make expressions"}</button></div>`;
  }
  if (d.log.length) {
    html += `<div class="card"><details><summary>Activity</summary><div class="log">${esc(d.log.join("\n"))}</div></details></div>`;
  }
  return html;
}

function decisionPanel(d) {
  const g = d.gate_detail;
  if (d.paused && !d.job_error && !d.failed) {
    const cancelled = d.status === "cancelled";
    return `<div class="decision"><div class="decision-head"><span class="dot"></span><div><h2>${cancelled ? "Cancelled" : "Paused"}</h2>
        <div class="muted">${cancelled ? "Everything finished so far is kept. Continue picks up where it stopped."
          : "The run stopped before it finished, for example because the app was closed. Nothing is lost."}</div></div></div>
      <div class="decision-actions"><div class="spacer"></div><button class="btn primary lg" id="btn-continue">Continue</button></div></div>`;
  }
  if (d.running) {
    return `<div class="decision quiet"><span class="spinner"></span><div class="grow"><strong>${esc(currentStep(d))}</strong>
      <div class="muted">${d.cancelling ? "Stopping after the current step. Everything finished so far is kept."
        : "You can leave this page. The run keeps going and stops when it needs you."}</div></div>
      <button class="btn" id="btn-cancel" ${d.cancelling ? "disabled" : ""}>${d.cancelling ? "Stopping…" : "Cancel"}</button></div>`;
  }
  if (!g && d.can_revise) return revisePanel(d, false);
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
          ${[64, 96, 128, 160].map((h) => `<button data-v="${h}" aria-pressed="${s.height_px === h}">${h} px</button>`).join("")}</div></div>
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
      ${reviseControls(d)}
      <div class="decision-actions"><div class="spacer"></div><button class="btn primary lg" id="btn-approve">Approve and export</button></div></div>`;
  }
  if (g.kind === "poses") {
    return `<div class="decision">${head("Approve the poses", "The pose sheet is shown above. Drawing starts once you approve; rejecting only redraws the sheet.")}
      <div class="frames">${g.frames.map((f, i) => `<div><b>Frame ${i + 1}</b> <span class="ms">${f.ms} ms</span><br>${esc(f.pose)}</div>`).join("")}</div>
      ${noteRow("Not right? For example: longer strides", "New poses")}
      <div class="decision-actions"><div class="spacer"></div><button class="btn primary lg" id="btn-approve">Approve and draw frames</button></div></div>`;
  }
  if (g.kind === "kit") {
    return `<div class="decision">${head("Approve the kit", "A small test level built from its tiles, in front of the matching scene. The tiles and decorations are below.")}
      <img class="wide-img px-wide" src="${esc(g.tileset)}" alt="Tileset">
      <div class="expr-grid">${(g.decorations || []).map((x) => `<figure class="expr"><span class="stage">${stageImg(x.url, x.name)}</span><figcaption>${esc(x.name)}</figcaption></figure>`).join("")}</div>
      ${g.named ? "" : `<p class="muted small">The decorations did not match the plan one to one, so they are numbered instead of named.</p>`}
      ${noteRow("What should change? For example: grassier top edge, smaller rocks", "Repaint kit")}
      <div class="decision-actions"><div class="spacer"></div><button class="btn primary lg" id="btn-approve">Approve and export</button></div></div>`;
  }
  if (g.kind === "scene") {
    const seams = (g.seams || []).filter((s) => s.visible).map((s) => s.layer);
    return `<div class="decision">${head("Approve the backdrop", "It scrolls above as a game camera would see it; each layer is shown below, far to near.")}
      <div class="frame-row" style="--n:${Math.min((g.layers || []).length, 5)}">${(g.layers || []).map((l) => `<figure><span class="stage">${stageImg(l.url, l.name)}</span>
        <figcaption>${esc(l.name)}${l.scroll != null ? ` <span class="ms">scroll ${l.scroll}</span>` : ""}</figcaption></figure>`).join("")}</div>
      ${seams.length ? `<p class="muted small">These layers may show a seam where they repeat: ${seams.map(esc).join(", ")}. Redrawing them can help.</p>` : ""}
      ${noteRow("What should change? For example: fewer trees in front, a warmer sky", "Redraw layers")}
      <div class="decision-actions"><div class="spacer"></div><button class="btn primary lg" id="btn-approve">Approve and export</button></div></div>`;
  }
  if (g.kind === "animation") {
    return `<div class="decision">${head("Approve the animation", "It plays above; every frame is shown below.")}
      ${g.frames && g.frames.length
        ? `<div class="frame-row" style="--n:${Math.min(g.frames.length, 8)}">${g.frames.map((f, i) => `<figure><span class="stage">${stageImg(f.url, "Frame " + (i + 1))}</span>
            <figcaption>Frame ${i + 1}${f.ms ? ` <span class="ms">${f.ms} ms</span>` : ""}</figcaption></figure>`).join("")}</div>`
        : `<img class="wide-img" src="${esc(g.strip)}" alt="All frames">`}
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
      const isPixel = img.naturalWidth <= 512;  // sprites and frames; larger images are paintings (concepts, pose sheets)
      // paintings get an exact fitted size: a percent max-height does not resolve inside the grid stage
      img.style.width = Math.round(img.naturalWidth * (isPixel ? z : Math.min(1, fit))) + "px";
      img.style.maxWidth = "none";
      img.style.maxHeight = "none";
      img.style.imageRendering = isPixel ? "" : "auto";  // nearest-neighbour downscaling would make paintings jagged
      const tools = $(".hero-tools");
      if (tools) tools.hidden = !isPixel;
      drawArea();
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
  on("#btn-move-set", () => sheetMoveSet(d));
  on("#btn-expr", () => sheetExpressions(d));
  on("#btn-pack", async () => {
    const r = await act(() => api("/api/gamepack", { project: p, id: d.id }));
    if (r) {
      const link = document.createElement("a");
      link.href = r.url; link.download = ""; document.body.appendChild(link); link.click(); link.remove();
      toast(`Game pack: ${r.frames} frames, ${r.animations.length} animations`);
    }
  });
  on("#btn-more", (e) => openMenu(e.currentTarget, itemMenu(d)));
  document.querySelectorAll("[data-version]").forEach((b) => b.onclick = () => {
    const v = Number(b.dataset.version);
    const cur = d.versions.items.find((x) => x.current);
    state.viewVersion = cur && cur.version === v ? null : v;
    state.detailSig = "";
    render();
  });
  on("#vb-back", () => { state.viewVersion = null; state.detailSig = ""; render(); });
  on("#vb-use", () => {
    const v = state.viewVersion;
    state.viewVersion = null;
    act(() => api("/api/versions/restore", { project: p, id: d.id, step: d.versions.step, version: v }),
      `Version ${v} restored. Checking it again`);
  });
  document.querySelectorAll("[data-redo]").forEach((b) => b.onclick = () => {
    const label = b.dataset.redo, usd = d.redo[label] || 0;
    showAlert({
      title: `Redo from ${label}?`,
      text: `${label} and every later step run again${usd > 0 ? `, for about $${usd.toFixed(2)} (estimate)` : ""}. `
        + "Nothing is deleted: earlier versions stay in the project, and the run stops whenever it needs you.",
      confirm: "Redo",
      onConfirm: () => act(() => api("/api/redo", { project: p, id: d.id, stage: label }), `Redoing from ${label}`),
    });
  });
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
  on("#btn-cancel", () => act(() => api("/api/cancel", { project: p, id: d.id }), "Stopping after the current step"));
  on("#btn-area", () => { state.selecting = !state.selecting; state.detailSig = ""; render(); });
  on("#area-clear", () => { state.area = null; state.selecting = false; state.detailSig = ""; render(); });
  on("#btn-revise", () => {
    const note = ($("#note") || {}).value || "";
    if (!note.trim()) { toast("Say what should change first", true); $("#note").focus(); return; }
    const a = state.area, box = a ? [a.x, a.y, a.w, a.h] : null;
    state.area = null; state.selecting = false;
    act(() => api("/api/fix", { project: p, id: d.id, note, box }), a ? "Fixing the area" : "Revising the sprite");
  });
  wireAreaSelect();
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

function sheetNewKit() {
  const scenes = state.items.filter((i) => i.kind === "scene" && i.status === "complete");
  openSheet(`<h2>New kit</h2><p class="sub">Tiles to walk on (32 px), a floating platform and decorations, painted as one piece of ground and cut into tiles. You'll approve it as a small test level.</p>
    <div class="stack">
      <div class="field"><label for="nk-prompt">The ground</label>
        <textarea class="textarea" id="nk-prompt" placeholder="Mossy forest ground with roots and stones; a fallen-log platform; ferns, mushrooms, a mossy rock, a wooden signpost."></textarea></div>
      <div class="row-2"><div class="field"><label for="nk-name">Name</label><input class="input" id="nk-name" placeholder="Optional"></div>
        <div class="field"><label for="nk-scene">Matches the scene</label>
          <select class="input" id="nk-scene"><option value="">None</option>${scenes.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")}</select></div></div>
    </div>
    <div class="actions"><span class="estimate" id="nk-est"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="nk-go">Create kit</button></div>`, (s) => {
    $("#nk-est").textContent = estimateText(state.overview.estimates.kit || 0.10);
    s.querySelector("[data-close]").onclick = closeSheet;
    $("#nk-go").onclick = async () => {
      const prompt = $("#nk-prompt").value.trim();
      if (!prompt) return toast("Describe the ground first", true);
      const r = await act(() => api("/api/kits", { project: project(), prompt, name: $("#nk-name").value.trim(), scene: $("#nk-scene").value || null }), "Planning your kit");
      if (r) { closeSheet(); location.hash = `#/item/${encodeURIComponent(r.id)}`; }
    };
  });
}

function sheetNewScene() {
  openSheet(`<h2>New scene</h2><p class="sub">A backdrop of parallax layers (sky, far, middle, near) in pixel art at your characters' scale, repeating sideways. You'll approve it with a scrolling preview.</p>
    <div class="stack">
      <div class="field"><label for="ns-prompt">The place</label>
        <textarea class="textarea" id="ns-prompt" placeholder="A forest at dusk: tall old trees, soft orange light from the left, fireflies over a mossy path."></textarea></div>
      <div class="field"><label for="ns-name">Name</label><input class="input" id="ns-name" placeholder="Optional"></div>
    </div>
    <div class="actions"><span class="estimate" id="ns-est"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="ns-go">Create scene</button></div>`, (s) => {
    $("#ns-est").textContent = estimateText(state.overview.estimates.scene || 0.35);
    s.querySelector("[data-close]").onclick = closeSheet;
    $("#ns-go").onclick = async () => {
      const prompt = $("#ns-prompt").value.trim();
      if (!prompt) return toast("Describe the place first", true);
      const r = await act(() => api("/api/scenes", { project: project(), prompt, name: $("#ns-name").value.trim() }), "Planning your scene");
      if (r) { closeSheet(); location.hash = `#/item/${encodeURIComponent(r.id)}`; }
    };
  });
}

function sheetNewObject() {
  let reference = null;
  openSheet(`<h2>New object</h2><p class="sub">An item or a prop, drawn as pixel art at its game size. You'll confirm the plan and pick a drawing first.</p>
    <div class="stack">
      <div class="segmented" id="no-mode" role="group" aria-label="Start from">
        <button data-v="text" aria-pressed="true">${icon("text")} Describe</button>
        <button data-v="ref" aria-pressed="false">${icon("photo")} Reference image</button></div>
      <div class="field" id="no-text"><label for="no-prompt">Description</label>
        <textarea class="textarea" id="no-prompt" placeholder="A small round healing potion: red liquid in clear glass, a cork stopper tied with string."></textarea></div>
      <div class="field" id="no-ref" hidden><span class="label">Reference image</span>
        <label class="drop" id="no-drop">${icon("photo")}<span>Drop an image here, or click to choose</span>
          <input type="file" accept="image/png,image/jpeg,image/webp" hidden id="no-file"></label>
        <textarea class="textarea" id="no-prompt2" style="min-height:60px" placeholder="Optional notes, for example: make it gold"></textarea></div>
      <div class="row-2"><div class="field"><label for="no-name">Name</label><input class="input" id="no-name" placeholder="Optional"></div>
        <div class="field"><span class="label">Size (longest side)</span><div class="segmented" id="no-h">
          ${[24, 32, 48, 64, 96].map((v) => `<button data-v="${v}" aria-pressed="${v === 48}">${v} px</button>`).join("")}</div></div></div>
    </div>
    <div class="actions"><span class="estimate" id="no-est"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="no-go">Create object</button></div>`, (s) => {
    wireSegmented(s);
    $("#no-est").textContent = estimateText(state.overview.estimates.object || 0.15);
    s.querySelector("[data-close]").onclick = closeSheet;
    s.querySelectorAll("#no-mode button").forEach((b) => b.addEventListener("click", () => {
      const ref = segmentedValue($("#no-mode")) === "ref";
      $("#no-text").hidden = ref;
      $("#no-ref").hidden = !ref;
    }));
    const drop = $("#no-drop");
    const take = (file) => {
      if (!file || !/^image\/(png|jpeg|webp)$/.test(file.type)) return toast("Use a PNG, JPG or WEBP image", true);
      const rd = new FileReader();
      rd.onload = () => {
        reference = rd.result;
        drop.innerHTML = `<img src="${reference}" alt="Reference">`;
        drop.classList.add("has-image");
        const img = drop.querySelector("img");
        img.onload = () => {  // pixel art: crisp whole-number zoom; photos and paintings: smooth fit
          if (img.naturalWidth <= 256 && img.naturalHeight <= 256) {
            const k = Math.max(1, Math.floor(Math.min(320 / img.naturalWidth, 220 / img.naturalHeight)));
            img.style.width = img.naturalWidth * k + "px";
            img.style.maxHeight = "none";
            img.style.imageRendering = "pixelated";
          }
        };
      };
      rd.readAsDataURL(file);
    };
    $("#no-file").onchange = (e) => take(e.target.files[0]);
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); take(e.dataTransfer.files[0]); };
    $("#no-go").onclick = async () => {
      const ref = segmentedValue($("#no-mode")) === "ref";
      const body = { project: project(), type: "object", name: $("#no-name").value.trim(), height: Number(segmentedValue($("#no-h"))),
        prompt: (ref ? $("#no-prompt2") : $("#no-prompt")).value.trim(), reference: ref ? reference : null };
      if (ref && !reference) return toast("Add a reference image first", true);
      if (!ref && !body.prompt) return toast("Describe the object first", true);
      const r = await act(() => api("/api/characters", body), "Planning your object");
      if (r) { closeSheet(); location.hash = `#/item/${encodeURIComponent(r.id)}`; }
    };
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
          ${[64, 96, 128, 160].map((v) => `<button data-v="${v}" aria-pressed="${v === h}">${v} px</button>`).join("")}</div></div></div>
    </div>
    <div class="actions"><span class="estimate" id="nc-est"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="nc-go">Create character</button></div>`, (s) => {
    wireSegmented(s);
    const est = () => { $("#nc-est").textContent = estimateText(characterEstimate(segmentedValue($("#nc-h")))); };
    s.querySelectorAll("#nc-h button").forEach((b) => b.addEventListener("click", est));
    est();
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
      rd.onload = () => {
        reference = rd.result;
        drop.innerHTML = `<img src="${reference}" alt="Reference">`;
        drop.classList.add("has-image");
        const img = drop.querySelector("img");
        img.onload = () => {  // pixel art: crisp whole-number zoom; photos and paintings: smooth fit
          if (img.naturalWidth <= 256 && img.naturalHeight <= 256) {
            const k = Math.max(1, Math.floor(Math.min(320 / img.naturalWidth, 220 / img.naturalHeight)));
            img.style.width = img.naturalWidth * k + "px";
            img.style.maxHeight = "none";
            img.style.imageRendering = "pixelated";
          }
        };
      };
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
    <div class="actions"><span class="estimate" id="na-est"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="na-go">Create animation</button></div>`, (s) => {
    wireSegmented(s);
    const est = () => {
      const f = segmentedValue($("#na-f"));
      $("#na-est").textContent = estimateText(animationEstimate(f)) + (f || testMode() ? "" : ", for about 6 frames");
    };
    s.querySelectorAll("#na-f button").forEach((b) => b.addEventListener("click", est));
    est();
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

function sheetExpressions(d) {
  const names = [["happy", "Happy"], ["sad", "Sad"], ["angry", "Angry"], ["surprised", "Surprised"], ["hurt", "Hurt"], ["blink", "Blink"]];
  const have = new Set((d.expressions || []).map((e) => e.name));
  openSheet(`<h2>Expressions</h2><p class="sub">New faces for ${esc(d.name)}. Only the face is redrawn; the rest of the sprite stays pixel for pixel. Making one again replaces it.</p>
    <div class="field"><span class="label">Faces</span><div class="chips" id="ex-names">${names.map(([v, l]) =>
      `<button class="chip" data-v="${v}" aria-pressed="${!have.size && !have.has(v)}">${l}</button>`).join("")}</div></div>
    <div class="field"><label for="ex-own">Your own</label>
      <textarea class="textarea" id="ex-own" rows="2" placeholder="Describe a face, for example: a smug half-smile with one brow raised"></textarea></div>
    <div class="field"><label for="ex-own-name">Its name</label>
      <input class="input" id="ex-own-name" maxlength="24" placeholder="smug"></div>
    <div class="actions"><span class="estimate" id="ex-est"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="ex-go">Make expressions</button></div>`, (s) => {
    const picked = () => [...s.querySelectorAll("#ex-names .chip[aria-pressed=true]")];
    const own = () => $("#ex-own").value.trim();
    const est = () => {
      const n = picked().length + (own() ? 1 : 0);
      $("#ex-est").textContent = n ? estimateText(n * (state.overview.estimates.expression || 0.06)) : "Pick a face or describe one";
      $("#ex-go").disabled = !n;
    };
    $("#ex-own").oninput = est;
    s.querySelectorAll("#ex-names .chip").forEach((b) => b.onclick = () => {
      b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") === "true" ? "false" : "true"); est();
    });
    est();
    s.querySelector("[data-close]").onclick = closeSheet;
    $("#ex-go").onclick = async () => {
      const body = { project: project(), id: d.id, names: picked().map((b) => b.dataset.v) };
      if (own()) body.custom = { prompt: own(), name: $("#ex-own-name").value.trim() };
      const r = await act(() => api("/api/expressions", body), "Drawing the expressions");
      if (r) closeSheet();
    };
  });
}

function sheetMoveSet(d) {
  const moves = [["idle", "Idle", 4], ["walk", "Walk", 6], ["run", "Run", 6], ["jump", "Jump", 6], ["attack", "Attack", 5], ["hurt", "Hurt", 3]];
  openSheet(`<h2>Move set</h2><p class="sub">The usual moves of a side-scrolling game character, for ${esc(d.name)}. Each one is its own animation: you'll approve its poses before it is finished.</p>
    <div class="field"><span class="label">Moves</span><div class="chips" id="ms-moves">${moves.map(([v, l, f]) =>
      `<button class="chip" data-v="${v}" data-f="${f}" aria-pressed="true">${l} · ${f}</button>`).join("")}</div></div>
    <div class="actions"><span class="estimate" id="ms-est"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="ms-go">Create animations</button></div>`, (s) => {
    const picked = () => [...s.querySelectorAll("#ms-moves .chip[aria-pressed=true]")];
    const est = () => {
      const p = picked();
      $("#ms-est").textContent = p.length ? estimateText(p.reduce((t, b) => t + animationEstimate(b.dataset.f), 0)) : "Pick at least one move";
      $("#ms-go").disabled = !p.length;
    };
    s.querySelectorAll("#ms-moves .chip").forEach((b) => b.onclick = () => {
      b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") === "true" ? "false" : "true"); est();
    });
    est();
    s.querySelector("[data-close]").onclick = closeSheet;
    $("#ms-go").onclick = async () => {
      const r = await act(() => api("/api/movesets", { project: project(), character: d.id, moves: picked().map((b) => b.dataset.v) }),
        "Planning the animations");
      if (r) { closeSheet(); toast(`${r.ids.length} animations started: approve each one's poses when they are ready`); }
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
      <div class="setting"><div class="grow">Look<div class="muted">Soft: hand-pixelled anime game sprites, drawn as pixel art from your reference or description (about $0.20 a character). Classic: strict palette and outlines, drawn by the model (about $1.50).</div></div>
        <div class="segmented" id="st-look">${[["soft", "Soft"], ["classic", "Classic"]].map(([v, l]) => `<button data-v="${v}" aria-pressed="${(s.look || "soft") === v}">${l}</button>`).join("")}</div></div>
      <div class="setting"><div class="grow">Colours<div class="muted">Soft look only. Mutes the colours and lifts the shadows toward a dusty, light game palette; the face keeps its colours. None keeps them as drawn.</div></div>
        <div class="segmented" id="st-grade">${[["none", "None"], ["soft", "Light"], ["medium", "Medium"], ["strong", "Strong"]].map(([v, l]) => `<button data-v="${v}" aria-pressed="${(s.grade || "medium") === v}">${l}</button>`).join("")}</div></div>
      <div class="setting"><div class="grow">Object colours<div class="muted">The same for objects and props. Light keeps gold and potion colours readable while they still fit the world.</div></div>
        <div class="segmented" id="st-prop-grade">${[["none", "None"], ["soft", "Light"], ["medium", "Medium"], ["strong", "Strong"]].map(([v, l]) => `<button data-v="${v}" aria-pressed="${(s.prop_grade || "soft") === v}">${l}</button>`).join("")}</div></div>
      <div class="setting"><div class="grow">Default height<div class="muted">160 px gives faces enough pixels.</div></div>
        <div class="segmented" id="st-h">${[64, 96, 128, 160].map((v) => `<button data-v="${v}" aria-pressed="${Number(s.sprite_height) === v}">${v} px</button>`).join("")}</div></div>
      <div class="setting"><div class="grow">Monthly limit<div class="muted">For all projects together. Work stops when it is reached (estimated spend).</div></div>
        <label class="affix"><span>US$</span><input class="input" id="st-cap" type="number" min="1" step="1" value="${esc(s.monthly_usd)}" aria-label="Monthly limit in US dollars"></label></div>
      <div class="setting"><div class="grow">Limit per item<div class="muted">One character or animation, including redraws. A character costs about $${characterEstimate(s.sprite_height).toFixed(2)}.</div></div>
        <label class="affix"><span>US$</span><input class="input" id="st-item" type="number" min="0.5" step="0.5" value="${esc(s.asset_usd)}" aria-label="Limit per item in US dollars"></label></div>
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
    backend: segmentedValue($("#st-mode")), sprite_height: Number(segmentedValue($("#st-h"))), look: segmentedValue($("#st-look")),
    grade: segmentedValue($("#st-grade")), prop_grade: segmentedValue($("#st-prop-grade")),
    monthly_usd: Number($("#st-cap").value), asset_usd: Number($("#st-item").value), projects_dir: $("#st-dir").value.trim(),
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

// ------------------------------------------------------------------ menus (macOS-style pull-down / context menus)

function openMenu(anchor, items, at) {
  const m = $("#menu");
  m.innerHTML = items.map((it, i) => it === "-" ? `<div class="menu-sep" role="separator"></div>`
    : `<button class="menu-item ${it.danger ? "danger" : ""}" role="menuitem" data-i="${i}" ${it.disabled ? "disabled" : ""}>
         ${it.icon ? icon(it.icon) : `<span class="ic"></span>`}<span>${esc(it.label)}</span></button>`).join("");
  m.hidden = false;
  const r = at || anchor.getBoundingClientRect();
  const w = m.offsetWidth, h = m.offsetHeight;
  let x = at ? at.x : r.right - w, y = at ? at.y : r.bottom + 6;
  x = Math.max(8, Math.min(x, innerWidth - w - 8));
  if (y + h > innerHeight - 8) y = Math.max(8, (at ? at.y : r.top) - h - 6);
  m.style.left = x + "px";
  m.style.top = y + "px";
  m.querySelectorAll(".menu-item").forEach((b) => b.onclick = () => { closeMenu(); items[Number(b.dataset.i)].action(); });
  const first = m.querySelector(".menu-item:not([disabled])");
  if (first) first.focus();
}
function closeMenu() { $("#menu").hidden = true; }
document.addEventListener("mousedown", (e) => { if (!$("#menu").hidden && !e.target.closest("#menu")) closeMenu(); }, true);
document.addEventListener("keydown", (e) => {
  const m = $("#menu");
  if (m.hidden) return;
  if (e.key === "Escape") { closeMenu(); e.stopPropagation(); return; }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    const all = [...m.querySelectorAll(".menu-item:not([disabled])")];
    const i = all.indexOf(document.activeElement);
    all[(i + (e.key === "ArrowDown" ? 1 : all.length - 1)) % all.length].focus();
    e.preventDefault();
  }
}, true);
window.addEventListener("blur", closeMenu);
window.addEventListener("resize", closeMenu);

function itemMenu(it) {
  const p = project(), busy = !!it.running;
  return [
    { label: "Rename…", icon: "pencil", disabled: busy, action: () => sheetRename(it.kind === "animation" ? "Rename animation" : "Rename character",
      it.name, (name) => api("/api/item/rename", { project: p, id: it.id, name }), "Renamed") },
    { label: "Duplicate", icon: "copy", disabled: busy, action: async () => {
      const r = await act(() => api("/api/item/duplicate", { project: p, id: it.id }), "Duplicated");
      if (r) location.hash = `#/item/${encodeURIComponent(r.id)}`;
    } },
    { label: "Show in folder", icon: "folder", action: () => act(() => api("/api/open", { project: p, id: it.id, what: "folder" })) },
    "-",
    { label: "Delete…", icon: "trash", danger: true, disabled: busy, action: () => alertDeleteItem(it) },
  ];
}

function projectMenu(pid) {
  return [
    { label: "Rename…", icon: "pencil", action: () => sheetRename("Rename project", pid, async (name) => {
      await api("/api/project/rename", { project: pid, name });
    }, "Renamed") },
    { label: "Show in folder", icon: "folder", action: () => act(() => api("/api/project/open", { project: pid })) },
    "-",
    { label: "Delete project…", icon: "trash", danger: true, action: () => alertDeleteProject(pid) },
  ];
}

// right-click on a library tile or a project row: the same menu as its More button
document.addEventListener("contextmenu", (e) => {
  const tile = e.target.closest(".tile[data-id]");
  const proj = e.target.closest("[data-project]");
  if (!tile && !proj) return;
  e.preventDefault();
  if (tile) {
    const it = state.items.find((i) => i.id === tile.dataset.id);
    if (it) openMenu(tile, itemMenu(it), { x: e.clientX, y: e.clientY });
  } else {
    openMenu(proj, projectMenu(proj.dataset.project), { x: e.clientX, y: e.clientY });
  }
});

// ------------------------------------------------------------------ rename sheet and delete alert

function sheetRename(title, current, save, okMsg) {
  openSheet(`<h2>${esc(title)}</h2>
    <div class="stack" style="margin-top:14px"><div class="field"><label for="rn-name">Name</label>
      <input class="input" id="rn-name" value="${esc(current)}" maxlength="80"></div></div>
    <div class="actions"><button class="btn" id="rn-cancel">Cancel</button><button class="btn primary" id="rn-ok">Rename</button></div>`, (s) => {
    const inp = $("#rn-name", s);
    setTimeout(() => inp.select(), 40);
    const go = async () => {
      const name = inp.value.trim();
      if (!name) { inp.focus(); return; }
      if (name === current) { closeSheet(); return; }
      const ok = await act(async () => { await save(name); return true; }, okMsg);
      if (ok) closeSheet();
    };
    $("#rn-ok", s).onclick = go;
    $("#rn-cancel", s).onclick = closeSheet;
    inp.onkeydown = (e) => { if (e.key === "Enter") go(); };
  });
}

function showAlert({ title, text, confirm, danger, onConfirm }) {
  const back = $("#alert-backdrop");
  $("#alert").innerHTML = `<span class="brand-mark alert-icon" aria-hidden="true"></span>
    <h2 id="alert-title">${esc(title)}</h2><p>${esc(text)}</p>
    <div class="alert-actions"><button class="btn" id="al-cancel">Cancel</button>
      <button class="btn ${danger ? "destructive" : "primary"}" id="al-ok">${esc(confirm)}</button></div>`;
  $("#alert").setAttribute("aria-labelledby", "alert-title");
  back.hidden = false;
  const close = () => { back.hidden = true; document.removeEventListener("keydown", key, true); };
  const key = (e) => { if (e.key === "Escape") { close(); e.stopPropagation(); } };
  document.addEventListener("keydown", key, true);
  $("#al-cancel").onclick = close;
  $("#al-ok").onclick = async () => { $("#al-ok").disabled = true; await onConfirm(); close(); };
  setTimeout(() => $("#al-cancel").focus(), 30);  // like macOS: the safe choice has the focus
}

function alertDeleteItem(it) {
  const anims = it.kind === "animation" ? [] : state.items.filter((i) => i.kind === "animation" && i.character === it.id);
  const extra = anims.length ? ` Its ${anims.length === 1 ? "animation" : anims.length + " animations"} will be deleted too.` : "";
  showAlert({
    title: `Delete “${it.name}”?`,
    text: `It moves to Recently Deleted with all its versions and exports, where you can restore it.${extra}`,
    confirm: "Delete", danger: true,
    onConfirm: async () => {
      const r = await act(() => api("/api/item/delete", { project: project(), id: it.id, with_animations: anims.length > 0 }),
        "Moved to Recently Deleted");
      if (r && route().view === "item") location.hash = `#/${{ animation: "animations", object: "objects", scene: "scenes", kit: "kits" }[it.kind] || "characters"}`;
    },
  });
}

function alertDeleteProject(pid) {
  showAlert({
    title: `Delete the project “${pid}”?`,
    text: "The project and everything in it move to Recently Deleted, where you can restore them.",
    confirm: "Delete project", danger: true,
    onConfirm: async () => {
      const r = await act(() => api("/api/project/delete", { project: pid }), "Moved to Recently Deleted");
      if (r) location.hash = "#/characters";
    },
  });
}


// ------------------------------------------------------------------ Recently Deleted

function deletedWhen(t) {
  const days = Math.floor((Date.now() / 1000 - t) / 86400);
  if (days <= 0) return "Deleted today";
  if (days === 1) return "Deleted yesterday";
  return `Deleted ${days} days ago`;
}

function deletedLabel(e) {
  if (e.kind === "project") return `Project, ${e.items || 0} item${e.items === 1 ? "" : "s"}`;
  const what = e.item_kind === "animation" ? `Animation${e.character_name ? ` of ${e.character_name}` : ""}`
    : { object: "Object", scene: "Scene", kit: "Kit" }[e.item_kind] || "Character";
  const anims = e.animations ? ` and ${e.animations} animation${e.animations === 1 ? "" : "s"}` : "";
  return `${what}${anims}, in ${e.project}`;
}

async function renderDeleted() {
  sessionStorage.setItem("from", "deleted");
  const items = (await api("/api/trash")).items;
  setToolbar(`<div class="title">Recently Deleted</div><div class="spacer"></div>
    ${items.length ? `<button class="btn" id="btn-empty">${icon("trash")}Delete All</button>` : ""}`);
  schedulePoll(0);
  const c = $("#content");
  if (!items.length) {
    c.innerHTML = emptyState("trash", "Nothing here", "Characters, animations and projects you delete wait here until you restore them or delete them for good.", "");
    return;
  }
  c.innerHTML = `<div class="page-head"><div><h1>Recently Deleted</h1>
      <div class="sub">Deleted work stays here, untouched, until you restore it or delete it for good.</div></div></div>
    <div class="grid">${items.map((e) => `
      <div class="tile deleted" data-entry="${esc(e.entry)}">
        <div class="stage ${e.thumb ? "" : "empty"}">${stageImg(e.thumb, e.name)}
          ${e.kind === "project" ? `<span class="kind-badge">${icon("folder")}Project</span>` : ""}</div>
        <div class="tile-name">${esc(e.name || "Untitled")}</div>
        <div class="tile-status">${esc(deletedLabel(e))}</div>
        <div class="tile-status">${esc(deletedWhen(e.deleted_at))}</div>
        <div class="tile-actions"><button class="btn" data-restore="${esc(e.entry)}">Restore</button>
          <button class="btn plain danger" data-purge="${esc(e.entry)}">Delete Now</button></div>
      </div>`).join("")}</div>`;
  c.querySelectorAll("[data-restore]").forEach((b) => b.onclick = async () => {
    const r = await act(() => api("/api/trash/restore", { entry: b.dataset.restore }), "Restored");
    if (r) render();
  });
  c.querySelectorAll("[data-purge]").forEach((b) => b.onclick = () => {
    const e = items.find((x) => x.entry === b.dataset.purge);
    showAlert({
      title: `Delete “${e.name || "Untitled"}” now?`,
      text: "It will be removed from your computer for good. This can't be undone.",
      confirm: "Delete Now", danger: true,
      onConfirm: async () => { await act(() => api("/api/trash/delete", { entry: e.entry }), "Deleted"); render(); },
    });
  });
  const eb = $("#btn-empty");
  if (eb) eb.onclick = () => showAlert({
    title: `Delete all ${items.length} item${items.length === 1 ? "" : "s"} for good?`,
    text: "Everything in Recently Deleted will be removed from your computer. This can't be undone.",
    confirm: "Delete All", danger: true,
    onConfirm: async () => { await act(() => api("/api/trash/empty", {}), "Everything in Recently Deleted was deleted"); render(); },
  });
}


// ------------------------------------------------------------------ selecting an area on the sprite

function spritePixel(img, e) {
  const r = img.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(img.naturalWidth - 1, Math.floor((e.clientX - r.left) / r.width * img.naturalWidth))),
    y: Math.max(0, Math.min(img.naturalHeight - 1, Math.floor((e.clientY - r.top) / r.height * img.naturalHeight))),
  };
}

function drawArea() {
  const hero = $("#hero"), img = $("#hero-img");
  let box = $("#area-box");
  if (!hero || !img || !state.area) { if (box) box.remove(); return; }
  if (!box) { box = document.createElement("div"); box.id = "area-box"; box.className = "area-box"; hero.appendChild(box); }
  const r = img.getBoundingClientRect(), h = hero.getBoundingClientRect(), k = r.width / img.naturalWidth;
  box.style.left = (r.left - h.left + state.area.x * k) + "px";
  box.style.top = (r.top - h.top + state.area.y * k) + "px";
  box.style.width = (state.area.w * k) + "px";
  box.style.height = (state.area.h * k) + "px";
}

function wireAreaSelect() {
  const hero = $("#hero"), img = $("#hero-img");
  if (!hero || !img) return;
  hero.classList.toggle("selecting", state.selecting);
  drawArea();
  if (!state.selecting) return;
  let start = null;
  img.ondragstart = (e) => e.preventDefault();
  hero.onpointerdown = (e) => {
    if (e.target.closest(".hero-tools")) return;
    start = spritePixel(img, e);
    state.area = { x: start.x, y: start.y, w: 1, h: 1 };
    hero.setPointerCapture(e.pointerId);
    drawArea();
  };
  hero.onpointermove = (e) => {
    if (!start) return;
    const q = spritePixel(img, e);
    state.area = { x: Math.min(start.x, q.x), y: Math.min(start.y, q.y), w: Math.abs(q.x - start.x) + 1, h: Math.abs(q.y - start.y) + 1 };
    drawArea();
  };
  hero.onpointerup = () => {
    if (!start) return;
    start = null;
    state.selecting = false;
    state.detailSig = "";
    render();
    setTimeout(() => { const n = $("#note"); if (n) n.focus(); }, 50);
  };
}
