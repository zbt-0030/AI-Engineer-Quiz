/* Keepstack single-page client. Vanilla JS, no build step. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const state = { token: localStorage.getItem("keepstack_token"), user: null,
  q: "", mode: "keyword", media: null, sort: "newest", collection: null,
  tags: [], excludeTags: [], page: 1, pageSize: 24, total: 0, pages: 1 };

// ---------- API helper ----------
async function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (state.token) headers["Authorization"] = "Bearer " + state.token;
  // A JSON body requires a method with a body; defaulting to POST keeps
  // callers from sending GET with a body (which fetch rejects outright).
  if (opts.json) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); opts.method = opts.method || "POST"; }
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) { logout(); throw new Error("unauthorised"); }
  if (!res.ok) { const t = await res.text(); throw new Error(t || res.statusText); }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res;
}

function toast(msg, kind = "ok") {
  const t = $("#toast"); t.textContent = msg; t.className = "toast " + kind;
  setTimeout(() => t.classList.add("hidden"), 2600);
}
const fmtBytes = (b) => { if (!b) return "0 B"; const u = ["B","KB","MB","GB","TB"]; const i = Math.floor(Math.log(b)/Math.log(1024)); return (b/Math.pow(1024,i)).toFixed(i?1:0)+" "+u[i]; };
const esc = (s) => (s ?? "").toString().replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

// ---------- auth ----------
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await api("/api/auth/login", { json: { username: $("#login-user").value, password: $("#login-pass").value } });
    state.token = r.token; localStorage.setItem("keepstack_token", r.token);
    state.user = r.user; enterApp();
  } catch { $("#login-error").textContent = "Invalid credentials"; $("#login-error").classList.remove("hidden"); }
});
$("#btn-logout").addEventListener("click", logout);
function logout() { localStorage.removeItem("keepstack_token"); state.token = null; state.user = null;
  $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); }

function enterApp() {
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  $("#user-label").textContent = `${state.user.username} (${state.user.role})`;
  const isAdmin = state.user.role === "admin";
  $$(".admin-only").forEach(el => el.classList.toggle("hidden", !isAdmin));
  loadFilterOptions();
  loadLibrary();
}

// ---------- navigation ----------
$$(".nav-item").forEach(btn => btn.addEventListener("click", () => {
  $$(".nav-item").forEach(b => b.classList.remove("active")); btn.classList.add("active");
  const view = btn.dataset.view;
  $$(".view").forEach(v => v.classList.add("hidden"));
  $("#view-" + (view === "library" ? "library" : view)).classList.remove("hidden");
  if (view === "library") loadLibrary();
  if (view === "collections") loadCollections();
  if (view === "dashboard") loadDashboard();
  if (view === "users") loadUsers();
  if (view === "audit") loadAudit();
}));

// ---------- library / search / filters ----------
let searchTimer;
let requestSeq = 0;          // newest request wins; older responses are dropped

$("#search").addEventListener("input", (e) => {
  state.q = e.target.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.page = 1; loadLibrary(); }, 250);
});
// Semantic ranking is still available on the API (mode=semantic) but is not
// offered here: it ranks by similarity, so it would ignore the sort control.
$("#sort").addEventListener("change", (e) => { state.sort = e.target.value; state.page = 1; loadLibrary(); });
$("#page-size").addEventListener("change", (e) => { state.pageSize = +e.target.value; state.page = 1; loadLibrary(); });
$("#page-prev").addEventListener("click", () => { if (state.page > 1) { state.page -= 1; loadLibrary(); } });
$("#page-next").addEventListener("click", () => { if (state.page < state.pages) { state.page += 1; loadLibrary(); } });
$("#f-collection").addEventListener("change", (e) => {
  state.collection = e.target.value || null; state.page = 1; loadLibrary();
});

function wireTagInput(selector, key) {
  const input = $(selector);
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const name = input.value.trim().toLowerCase();
    if (!name) return;
    if (!state[key].includes(name)) state[key].push(name);
    input.value = "";
    state.page = 1;
    loadLibrary();
  });
}
wireTagInput("#f-include", "tags");
wireTagInput("#f-exclude", "excludeTags");

async function loadFilterOptions() {
  try {
    const [cols, tags] = await Promise.all([api("/api/collections"), api("/api/tags")]);
    const sel = $("#f-collection");
    sel.innerHTML = '<option value="">Any collection</option>' + cols.collections
      .map(c => `<option value="${c.id}">${esc(c.name)} (${c.asset_count})</option>`).join("");
    sel.value = state.collection || "";
    $("#tag-options").innerHTML = tags.tags
      .map(t => `<option value="${esc(t.name)}"></option>`).join("");
  } catch { /* the filter widgets degrade to free-text input */ }
}

// Filters that shape the result set, shared by the list request and the export.
function queryParams(withPaging = true) {
  const p = new URLSearchParams({ q: state.q, sort: state.sort, mode: state.mode });
  if (state.media) p.set("media_type", state.media);
  if (state.collection) p.set("collection_id", state.collection);
  state.tags.forEach(t => p.append("tags", t));
  state.excludeTags.forEach(t => p.append("exclude_tags", t));
  if (withPaging) { p.set("page", String(state.page)); p.set("page_size", String(state.pageSize)); }
  return p;
}

async function loadLibrary() {
  const seq = ++requestSeq;
  try {
    const data = await api("/api/assets?" + queryParams().toString());
    if (seq !== requestSeq) return;                       // a newer query already won
    state.total = data.total;
    state.pages = Math.max(1, Math.ceil(data.total / state.pageSize));
    if (state.page > state.pages) {                       // filters shrank the result set
      state.page = state.pages;
      loadLibrary();
      return;
    }
    $("#load-error").classList.add("hidden");
    renderFacets(data.facets);
    renderGrid(data.items, data.total);
    renderConditions();
    renderPager();
  } catch (err) {
    if (seq !== requestSeq) return;
    $("#load-error").textContent = "Could not load results: " + (err.message || err);
    $("#load-error").classList.remove("hidden");
    $("#grid").innerHTML = "";
    $("#empty").classList.add("hidden");
    $("#page-info").textContent = "";
    $("#page-prev").disabled = true;
    $("#page-next").disabled = true;
  }
}

function clearFilters() {
  // Everything listed as a condition gets cleared, keyword included: the chip
  // list is the single source of truth for what is currently narrowing results.
  state.media = null; state.collection = null; state.tags = []; state.excludeTags = [];
  state.q = "";
  $("#search").value = "";
  $("#f-collection").value = "";
  $("#f-include").value = ""; $("#f-exclude").value = "";
  state.page = 1;
  loadLibrary();
}

function renderConditions() {
  const box = $("#active-conditions");
  const chips = [];
  if (state.q.trim()) chips.push([`"${state.q.trim()}"`, () => { state.q = ""; $("#search").value = ""; }]);
  if (state.collection) {
    const opt = $("#f-collection").selectedOptions[0];
    chips.push(["collection: " + (opt ? opt.textContent.replace(/\s*\(\d+\)$/, "") : state.collection),
      () => { state.collection = null; $("#f-collection").value = ""; }]);
  }
  if (state.media) chips.push(["type: " + state.media, () => { state.media = null; }]);
  state.tags.forEach(t => chips.push(["+ " + t, () => { state.tags = state.tags.filter(x => x !== t); }]));
  state.excludeTags.forEach(t => chips.push(["− " + t, () => { state.excludeTags = state.excludeTags.filter(x => x !== t); }]));

  box.innerHTML = "";
  if (!chips.length) {
    box.innerHTML = '<span class="muted">No filters — all active assets.</span>';
    return;
  }
  const label = document.createElement("span");
  label.textContent = "Matching:";
  box.appendChild(label);
  chips.forEach(([text, remove]) => {
    const el = document.createElement("button");
    el.type = "button"; el.className = "chip"; el.textContent = text;
    el.title = "Remove this condition";
    el.addEventListener("click", () => { remove(); state.page = 1; loadLibrary(); });
    box.appendChild(el);
  });
  const clear = document.createElement("button");
  clear.type = "button"; clear.className = "btn btn-ghost"; clear.textContent = "Clear all";
  clear.addEventListener("click", clearFilters);
  box.appendChild(clear);
}

function renderPager() {
  $("#page-info").textContent = state.total
    ? `Page ${state.page} / ${state.pages} · ${state.total} matching asset${state.total === 1 ? "" : "s"}`
    : "0 matching assets";
  $("#page-prev").disabled = state.page <= 1;
  $("#page-next").disabled = state.page >= state.pages;
}

function renderGrid(items, total) {
  $("#result-title").textContent = state.q.trim() ? `Results (${total})` : `Library (${total})`;
  $("#empty").classList.toggle("hidden", items.length > 0);
  $("#grid").innerHTML = items.map(a => `
    <div class="card" data-uuid="${esc(a.uuid)}">
      <img class="thumb" loading="lazy" src="/api/assets/${a.uuid}/thumb" alt="${esc(a.alt_text || a.title)}">
      <div class="meta">
        <span class="title" title="${esc(a.title)}">${esc(a.title)}</span>
        <span class="sub"><span class="badge">${esc(a.media_type)}</span><span>${fmtBytes(a.size)}</span></span>
      </div>
    </div>`).join("");
  $$("#grid .card").forEach(c => c.addEventListener("click", () => openAsset(c.dataset.uuid)));
}

function renderFacets(facets) {
  $("#facet-media").innerHTML = (facets.media_types || []).map(m =>
    `<li class="${state.media===m.value?'active':''}" data-media="${esc(m.value)}"><span>${esc(m.value)}</span><span class="count">${m.count}</span></li>`).join("");
  $("#facet-tags").innerHTML = (facets.tags || []).slice(0, 18).map(t =>
    `<li class="${state.tags.includes(t.value)?'active':''}" data-tag="${esc(t.value)}"><span>${esc(t.value)}</span><span class="count">${t.count}</span></li>`).join("");
  $$("#facet-media li").forEach(li => li.addEventListener("click", () => {
    state.media = state.media === li.dataset.media ? null : li.dataset.media;
    state.page = 1; loadLibrary();
  }));
  $$("#facet-tags li").forEach(li => li.addEventListener("click", () => {
    const name = li.dataset.tag;
    state.tags = state.tags.includes(name)
      ? state.tags.filter(t => t !== name)
      : [...state.tags, name];
    state.excludeTags = state.excludeTags.filter(t => t !== name);
    state.page = 1; loadLibrary();
  }));
}

// ---------- CSV export ----------
$("#btn-export").addEventListener("click", async () => {
  const btn = $("#btn-export");
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "Preparing...";
  try {
    // Same filters and sort as the list, no paging: the whole match set.
    const res = await fetch("/api/assets/export.csv?" + queryParams(false).toString(),
      { headers: state.token ? { Authorization: "Bearer " + state.token } : {} });
    if (res.status === 401) { logout(); throw new Error("session expired"); }
    if (!res.ok) throw new Error((await res.text()).slice(0, 200) || res.statusText);
    const blob = await res.blob();
    const match = (res.headers.get("content-disposition") || "").match(/filename="?([^";]+)"?/);
    const name = match ? match[1] : "assets.csv";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("Exported " + name);
  } catch (err) {
    toast("Export failed: " + (err.message || err), "err");
  } finally {
    btn.disabled = false; btn.textContent = label;
  }
});

// ---------- asset detail drawer ----------
const drawer = $("#drawer");
let assetSeq = 0;                       // stale-response guard, like requestSeq
$("#drawer-close").addEventListener("click", closeDrawer);
$(".drawer-backdrop").addEventListener("click", closeDrawer);
function closeDrawer() { assetSeq++; drawer.classList.add("hidden"); document.body.classList.remove("modal-open"); }

async function openAsset(uuid) {
  const seq = ++assetSeq;
  drawer.classList.remove("hidden");
  document.body.classList.add("modal-open");
  $("#drawer-body").innerHTML = '<p class="muted">Loading...</p>';
  let d, meta, sim;
  try {
    [d, meta, sim] = await Promise.all([
      api("/api/assets/" + uuid),
      api("/api/assets/" + uuid + "/metadata"),
      api("/api/assets/" + uuid + "/similar"),
    ]);
  } catch (err) {
    // Without this the drawer sits on "Loading..." forever and the failure
    // only shows up as an unhandled rejection in the console.
    if (seq !== assetSeq) return;
    $("#drawer-body").innerHTML =
      `<p class="error-banner">Could not load this asset: ${esc(err.message || err)}</p>`;
    return;
  }
  if (seq !== assetSeq) return;   // a newer click (or a close) already won
  const a = d.asset;
  const canEdit = ["admin","editor","contributor"].includes(state.user.role);
  const preview = a.media_type === "image"
    ? `<img class="detail-preview" src="/api/assets/${uuid}/preview" alt="${esc(a.alt_text||a.title)}">`
    : `<img class="detail-preview" src="/api/assets/${uuid}/thumb" alt="${esc(a.title)}">`;
  const dc = a.dublin_core || {};
  $("#drawer-body").innerHTML = `
    ${preview}
    <h2 class="detail-title">${esc(a.title)}</h2>
    <p class="muted">${a.media_type} &middot; ${a.ext?.toUpperCase()} &middot; ${fmtBytes(a.size)} ${a.width?`&middot; ${a.width}&times;${a.height}`:""}</p>
    <div class="btn-row">
      <a class="btn" href="/api/assets/${uuid}/file" download>Download</a>
      ${canEdit?`<button class="btn" id="d-share">Create share link</button>`:""}
      ${["admin","editor"].includes(state.user.role)?`<button class="btn btn-danger" id="d-delete">Delete</button>`:""}
    </div>

    ${canEdit ? `<div class="detail-section"><h4>Edit metadata</h4>
      <div class="edit-grid">
        <label>Title<input id="e-title" value="${esc(a.title)}"></label>
        <label>Description<textarea id="e-description" rows="2">${esc(a.description)}</textarea></label>
        <label>Alt text (accessibility)<input id="e-alt_text" value="${esc(a.alt_text)}"></label>
        <label>Creator<input id="e-dc_creator" value="${esc(a.dc_creator)}"></label>
        <label>Rights / license<input id="e-rights_statement" value="${esc(a.rights_statement)}"></label>
        <label>Retention until<input id="e-retention_until" type="date" value="${esc((a.retention_until||'').slice(0,10))}"></label>
      </div>
      <div class="btn-row"><button class="btn btn-primary" id="d-save">Save changes</button></div></div>` : ""}

    <div class="detail-section"><h4>Tags</h4>
      <div class="tag-row" id="tag-row">
        ${d.tags.map(t => `<span class="tag ${t.source==='ai'?'ai':''}">${esc(t.name)}${canEdit?`<span class="rm" data-tag="${esc(t.name)}">&times;</span>`:""}</span>`).join("")}
        ${canEdit?`<input class="tag-add" id="tag-add" placeholder="+ add tag">`:""}
      </div></div>

    ${sim.items.length?`<div class="detail-section"><h4>Similar assets</h4>
      <div class="similar-strip">${sim.items.slice(0,8).map(s=>`<img src="/api/assets/${s.uuid}/thumb" data-uuid="${s.uuid}" title="${esc(s.title)}">`).join("")}</div></div>`:""}

    <div class="detail-section"><h4>Dublin Core</h4>
      ${Object.entries(dc).filter(([,v])=>v).map(([k,v])=>`<div class="field-row"><span class="k">${k}</span><span class="v">${esc(v)}</span></div>`).join("")}
      <div class="btn-row"><a class="btn btn-ghost" href="/oai?verb=GetRecord&metadataPrefix=oai_dc&identifier=oai::${a.uuid}" target="_blank">View OAI-PMH XML</a></div>
    </div>

    ${Object.keys(meta.exif||{}).length||Object.keys(meta.iptc||{}).length?`<div class="detail-section"><h4>Embedded metadata (EXIF / IPTC)</h4>
      <div class="kv-scroll">${[...Object.entries(meta.iptc||{}),...Object.entries(meta.exif||{})].slice(0,40).map(([k,v])=>`<div class="field-row"><span class="k">${esc(k)}</span><span class="v">${esc(typeof v==='object'?JSON.stringify(v):v)}</span></div>`).join("")}</div></div>`:""}

    ${d.versions.length?`<div class="detail-section"><h4>Versions</h4>${d.versions.map(v=>`<div class="field-row"><span class="k">v${v.version_no}</span><span class="v">${esc(v.note||'')} &middot; ${fmtBytes(v.size)} &middot; ${(v.created_at||'').slice(0,10)}</span></div>`).join("")}</div>`:""}
  `;

  // wire detail actions
  $$("#drawer-body .similar-strip img").forEach(img => img.addEventListener("click", () => openAsset(img.dataset.uuid)));
  if (canEdit) {
    $("#d-save")?.addEventListener("click", async () => {
      const body = {};
      ["title","description","alt_text","dc_creator","rights_statement","retention_until"].forEach(f => { const el = $("#e-"+f); if (el) body[f] = el.value; });
      await api("/api/assets/"+uuid, { method: "PATCH", json: body });
      toast("Saved"); openAsset(uuid); loadLibrary();
    });
    $("#tag-add")?.addEventListener("keydown", async (e) => {
      if (e.key === "Enter" && e.target.value.trim()) {
        await api("/api/assets/"+uuid+"/tags", { json: { name: e.target.value.trim() } });
        openAsset(uuid);
      }
    });
    $$("#tag-row .rm").forEach(rm => rm.addEventListener("click", async () => {
      await api("/api/assets/"+uuid+"/tags/"+encodeURIComponent(rm.dataset.tag), { method: "DELETE" });
      openAsset(uuid);
    }));
    $("#d-share")?.addEventListener("click", async () => {
      const r = await api("/api/shares", { json: { target_type: "asset", target_id: a.id, permission: "download" } });
      await navigator.clipboard?.writeText(r.url).catch(()=>{});
      toast("Share link copied: " + r.url);
    });
  }
  $("#d-delete")?.addEventListener("click", async () => {
    if (!confirm("Delete this asset?")) return;
    await api("/api/assets/"+uuid, { method: "DELETE" });
    toast("Deleted"); closeDrawer(); loadLibrary();
  });
}

// ---------- upload ----------
const uploadModal = $("#upload-modal");
$("#btn-upload").addEventListener("click", () => { uploadModal.classList.remove("hidden"); document.body.classList.add("modal-open"); });
$("#upload-cancel").addEventListener("click", () => { uploadModal.classList.add("hidden"); document.body.classList.remove("modal-open"); loadLibrary(); });
$(".modal-backdrop").addEventListener("click", () => { uploadModal.classList.add("hidden"); document.body.classList.remove("modal-open"); });
const dz = $("#dropzone"), fileInput = $("#file-input");
dz.addEventListener("click", () => fileInput.click());
dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("drag"); });
dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
dz.addEventListener("drop", e => { e.preventDefault(); dz.classList.remove("drag"); uploadFiles(e.dataTransfer.files); });
fileInput.addEventListener("change", () => uploadFiles(fileInput.files));

async function uploadFiles(files) {
  const list = $("#upload-list");
  for (const file of files) {
    const row = document.createElement("div");
    row.className = "upload-item"; row.innerHTML = `<span>${esc(file.name)}</span><span>uploading...</span>`;
    list.prepend(row);
    const fd = new FormData(); fd.append("files", file); fd.append("run_ai", $("#ai-toggle").checked);
    try {
      const r = await api("/api/assets", { method: "POST", body: fd });
      const created = r.created[0];
      row.classList.add(created.duplicate ? "dup" : "ok");
      row.lastChild.textContent = created.duplicate ? "duplicate (deduped)" : "done";
    } catch (err) { row.classList.add("err"); row.lastChild.textContent = "failed"; }
  }
}

// ---------- collections ----------
async function loadCollections() {
  const data = await api("/api/collections");
  $("#collection-list").innerHTML = data.collections.map(c =>
    `<div class="collection-card" data-id="${c.id}"><span class="n">${esc(c.name)}</span><span class="muted">${c.asset_count} assets</span><span class="muted">${esc(c.description||"")}</span></div>`).join("")
    || '<p class="muted">No collections yet.</p>';
  $$("#collection-list .collection-card").forEach(card => card.addEventListener("click", () => {
    state.collection = card.dataset.id;
    state.media = null; state.tags = []; state.excludeTags = []; state.page = 1;
    $("#f-collection").value = state.collection;
    $("#f-include").value = ""; $("#f-exclude").value = "";
    $$(".nav-item").forEach(b => b.classList.remove("active"));
    $('.nav-item[data-view="library"]').classList.add("active");
    $$(".view").forEach(v => v.classList.add("hidden")); $("#view-library").classList.remove("hidden");
    loadLibrary();
  }));
}
$("#btn-new-collection").addEventListener("click", async () => {
  const name = prompt("Collection name"); if (!name) return;
  await api("/api/collections", { json: { name } }); loadCollections();
});

// ---------- dashboard ----------
async function loadDashboard() {
  const s = await api("/api/stats");
  const cards = [
    ["Total assets", s.total_assets], ["Storage used", fmtBytes(s.total_bytes)],
    ["Saved by dedup", fmtBytes(s.dedup_saved_bytes)], ["Tags", s.tags], ["Collections", s.collections],
    ...Object.entries(s.by_type).map(([k,v]) => [k.charAt(0).toUpperCase()+k.slice(1), v]),
  ];
  $("#stat-cards").innerHTML = cards.map(([l,n]) => `<div class="stat-card"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");
}
$("#btn-fixity").addEventListener("click", async () => {
  $("#fixity-result").textContent = "Checking...";
  const r = await api("/api/admin/fixity", { method: "POST" });
  $("#fixity-result").textContent = r.ok ? `All ${r.checked} blobs verified. Integrity intact.` : `${r.failures.length} of ${r.checked} failed fixity.`;
});

// ---------- users ----------
async function loadUsers() {
  const data = await api("/api/users");
  $("#user-rows").innerHTML = data.users.map(u =>
    `<tr><td>${esc(u.username)}</td><td>${esc(u.email||"")}</td><td>${u.role}</td><td>${u.is_active?"Yes":"No"}</td><td>${(u.last_login||"").slice(0,16).replace("T"," ")}</td></tr>`).join("");
}
$("#btn-new-user").addEventListener("click", async () => {
  const username = prompt("Username"); if (!username) return;
  const password = prompt("Temporary password"); if (!password) return;
  const role = prompt("Role (admin/editor/contributor/viewer)", "contributor") || "viewer";
  await api("/api/users", { json: { username, password, role } }); loadUsers();
});

// ---------- audit ----------
async function loadAudit() {
  const data = await api("/api/audit");
  $("#audit-rows").innerHTML = data.entries.map(e =>
    `<tr><td>${(e.ts||"").slice(0,19).replace("T"," ")}</td><td>${esc(e.username||"-")}</td><td>${esc(e.action)}</td>
     <td>${esc(e.target_type||"")} ${esc((e.target_id||"").slice(0,8))}</td><td>${esc(e.detail||"")}</td><td>${esc(e.ip||"")}</td></tr>`).join("");
}

// ---------- boot ----------
(async function boot() {
  if (state.token) {
    try { state.user = await api("/api/auth/me"); enterApp(); return; } catch {}
  }
  $("#login").classList.remove("hidden");
})();
