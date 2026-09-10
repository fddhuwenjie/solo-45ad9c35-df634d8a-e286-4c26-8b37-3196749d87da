/* 换版索引校准台 —— 原生 JS 单页工作台 */
"use strict";

let S = null;                 // 当前项目完整状态
let currentPid = null;
let selectedLoc = null;       // {entryId, locatorId}
const pageCache = {};         // pid/edition/page -> {content}
const pagePos = { old: 1, new: 1 };
let shownHighlights = { new: {}, old: {} }; // 每侧 {pageNo: [sentences]}

/* ---------------- 基础工具 ---------------- */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

async function api(url, opts = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function toast(msg, kind = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast " + kind;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 3200);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function rangeText(l, prefix = "") {
  const s = l[prefix + "start"], e = l[prefix + "end"];
  if (s == null) return "";
  return e && e !== s ? `${s}-${e}` : `${s}`;
}

function confClass(c) { return c === "high" ? "high" : c === "medium" ? "medium" : "low"; }
function confLabel(c) { return c === "high" ? "高置信" : c === "medium" ? "中置信" : "低置信"; }
function methodLabel(m) {
  return { mapped: "逐页映射", offset: "全书偏移", window: "窗口滑选",
           combined: "综合", anchor: "页对照锚点" }[m] || m;
}

/* ---------------- 启动 ---------------- */

init();

async function init() {
  bindChrome();
  await refreshProjectList(true);
}

async function refreshProjectList(autoselect = false) {
  const projects = await api("/api/projects");
  const sel = $("#projectSelect");
  sel.innerHTML = projects.length
    ? projects.map(p => `<option value="${p.id}">${esc(p.name)}（${p.entry_count} 条）</option>`).join("")
    : `<option value="">（无项目）</option>`;
  if (autoselect && projects.length) {
    sel.value = projects[0].id;
    await openProject(projects[0].id);
  } else if (!projects.length) {
    showWelcome();
  }
}

async function openProject(pid) {
  currentPid = pid;
  selectedLoc = null;
  Object.keys(pageCache).forEach(k => { if (k.startsWith(pid + "/")) delete pageCache[k]; });
  pagePos.old = pagePos.new = 1;
  shownHighlights = { new: {}, old: {} };
  try {
    S = await api(`/api/state/${pid}`);
  } catch (e) {
    showWelcome();
    return;
  }
  $("#welcome").classList.add("hidden");
  $("#workbench").classList.remove("hidden");
  $$(".need-project").forEach(b => b.disabled = false);
  renderAll();
}

function showWelcome() {
  S = null;
  $("#welcome").classList.remove("hidden");
  $("#workbench").classList.add("hidden");
}

/* ---------------- 顶栏事件 ---------------- */

function bindChrome() {
  $("#projectSelect").addEventListener("change", e => e.target.value && openProject(+e.target.value));

  $$(".topbar [data-act], .welcome [data-act]").forEach(btn => {
    btn.addEventListener("click", () => onAction(btn.dataset.act));
  });

  $("#exportMenu").addEventListener("click", e => {
    const kind = e.target.dataset.exp;
    if (kind) { window.open(`/api/projects/${currentPid}/export/${kind}`, "_blank"); }
    $("#exportMenu").classList.add("hidden");
  });
  document.addEventListener("click", e => {
    if (!e.target.closest("[data-act=export]")) $("#exportMenu").classList.add("hidden");
  });

  $$(".pgnav").forEach(b => b.addEventListener("click", () => {
    const side = b.dataset.side, dir = +b.dataset.dir;
    const list = S.pages[side];
    const idx = list.findIndex(p => p.page_no === pagePos[side]);
    const next = list[Math.min(list.length - 1, Math.max(0, idx + dir))];
    if (next) { pagePos[side] = next.page_no; renderPage(side); renderAnchorBadges(); }
  }));

  $("#searchBox").addEventListener("input", renderEntries);
  $("#statusFilter").addEventListener("change", renderEntries);

  $$(".tab").forEach(t => t.addEventListener("click", () => {
    $$(".tab").forEach(x => x.classList.toggle("active", x === t));
    ["inspect", "anchors", "issues", "versions", "settings"].forEach(name =>
      $("#tab-" + name).classList.toggle("hidden", name !== t.dataset.tab));
    if (t.dataset.tab === "versions") renderVersions();
    if (t.dataset.tab === "anchors") renderAnchors();
    if (t.dataset.tab === "issues") renderIssues();
    if (t.dataset.tab === "settings") fillSettings();
  }));

  // 导入弹窗
  $$("[data-close]").forEach(b => b.addEventListener("click", () =>
    $("#" + b.dataset.close).classList.add("hidden")));
  $("#impDo").addEventListener("click", doImport);
  $("#snapBtn").addEventListener("click", doSnapshot);
  $("#settingsSave").addEventListener("click", saveSettings);

  // 锚点快捷条 / 重匹配弹窗
  $("#setAnchorBtn").addEventListener("click", createAnchorFromPages);
  $("#anchorQuickNote").addEventListener("keydown", e => {
    if (e.key === "Enter") createAnchorFromPages();
  });
  $("#rematchDo").addEventListener("click", executeRematch);
}

async function onAction(act) {
  try {
    if (act === "sample") {
      const r = await api("/api/sample", { method: "POST" });
      await refreshProjectList();
      $("#projectSelect").value = r.id;
      await openProject(r.id);
      toast("已载入演示样例：点击「▶ 运行匹配」开始");
    } else if (act === "new") {
      const name = prompt("项目名称：", "换版校准项目 " + new Date().toLocaleDateString());
      if (!name) return;
      const r = await api("/api/projects", { method: "POST", body: { name } });
      await refreshProjectList();
      $("#projectSelect").value = r.id;
      await openProject(r.id);
      toast("项目已创建，请先导入分页文本与 CSV");
      openImport();
    } else if (act === "import") {
      openImport();
    } else if (act === "match") {
      toast("正在计算指纹与候选页…");
      const info = await api(`/api/projects/${currentPid}/match`, { method: "POST" });
      await reloadState();
      const off = info.info && info.info.global_offset != null
        ? `全书主偏移 ${info.info.global_offset >= 0 ? "+" : ""}${info.info.global_offset}`
        : "未能估计统一偏移";
      toast(`匹配完成（${off}）`, "success");
    } else if (act === "batch") {
      const threshold = S.project.batch_threshold || 0.85;
      const r = await api(`/api/projects/${currentPid}/batch-accept`,
        { method: "POST", body: { threshold } });
      await reloadState();
      toast(`批量接受 ${r.accepted} 条；${r.skipped.length} 条因歧义跳过`,
        r.accepted ? "success" : "");
    } else if (act === "undo") {
      const r = await api(`/api/projects/${currentPid}/undo`, { method: "POST" });
      if (!r.ok) { toast(r.message, "error"); return; }
      await reloadState();
      toast(`已撤销：${describeAction(r.undone)}`);
    } else if (act === "export") {
      $("#exportMenu").classList.toggle("hidden");
    }
  } catch (e) {
    toast(e.message, "error");
  }
}

function openImport() {
  $("#importModal").classList.remove("hidden");
}

async function doImport() {
  const edition = $("#impEdition").value;
  const text = $("#impText").value.trim();
  const csv = $("#impCsv").value.trim();
  const mode = $('input[name="impMode"]:checked').value;
  if (!text && !csv) { toast("请粘贴分页文本或 CSV", "error"); return; }
  try {
    await api(`/api/projects/${currentPid}/import`,
      { method: "POST", body: { edition, text, csv, mode } });
    $("#importModal").classList.add("hidden");
    $("#impText").value = $("#impCsv").value = "";
    selectedLoc = null;
    await reloadState();
    toast("导入完成，已执行一次实时检查", "success");
  } catch (e) { toast(e.message, "error"); }
}

async function reloadState() {
  S = await api(`/api/state/${currentPid}`);
  renderAll();
}

/* ---------------- 总体渲染 ---------------- */

function renderAll() {
  renderStats();
  renderEntries();
  renderInspector();
  renderIssuesBadge();
  renderAnchorBadges();
  renderPages();
  if (!$("#tab-anchors").classList.contains("hidden")) renderAnchors();
}

function renderStats() {
  const st = S.stats;
  const offsetInfo = S.match_info.last_match ? JSON.parse(S.match_info.last_match) : null;
  const off = offsetInfo && offsetInfo.global_offset != null
    ? `主偏移 ${offsetInfo.global_offset >= 0 ? "+" : ""}${offsetInfo.global_offset}` +
      `（${Math.round(offsetInfo.offset_ratio * 100)}% 页面）`
    : "尚未运行匹配";
  $("#statsBar").innerHTML = `
    <span class="stat">定位号 <b>${st.total}</b></span>
    <span class="stat pending">待确认 <b>${st.pending}</b></span>
    <span class="stat confirmed">已确认 <b>${st.confirmed}</b></span>
    <span class="stat rejected">已拒绝 <b>${st.rejected}</b></span>
    <span class="stat high">高 <b>${st.high}</b></span>
    <span class="stat medium">中 <b>${st.medium}</b></span>
    <span class="stat low">低 <b>${st.low}</b></span>
    <span class="stat">未匹配 <b>${st.unmatched}</b></span>
    <span class="stat info">${esc(S.project.name)} · ${off} ·
      旧版 ${S.pages.old.length} 页 / 新版 ${S.pages.new.length} 页 ·
      问题 ${S.issues.length}</span>`;
}

/* ---------------- 左栏：条目树 ---------------- */

function issueEntrySet() {
  const ids = new Set();
  S.issues.forEach(i => { if (i.entry_id) ids.add(i.entry_id); });
  return ids;
}

function renderEntries() {
  const q = $("#searchBox").value.trim().toLowerCase();
  const filter = $("#statusFilter").value;
  const badEntries = issueEntrySet();
  const badLocs = new Set(S.issues.filter(i => i.locator_id).map(i => i.locator_id));

  const groups = new Map();
  S.entries.forEach(e => {
    if (!groups.has(e.term)) groups.set(e.term, { main: null, subs: [] });
    const g = groups.get(e.term);
    if (e.subterm) g.subs.push(e); else g.main = e;
  });

  const terms = [...groups.keys()].sort((a, b) => a.localeCompare(b, "zh"));
  const html = terms.map(term => {
    const g = groups.get(term);
    const mains = g.main ? [g.main] : g.subs;
    const subs = g.main ? g.subs : [];

    const entryMatches = (e, locs) => {
      const hay = (e.term + " " + (e.subterm || "") + " " + (e.target || "")).toLowerCase();
      if (q && !hay.includes(q)) return false;
      if (filter === "issues") return badEntries.has(e.id) || locs.some(l => badLocs.has(l.id));
      if (filter === "high") return locs.some(l => l.candidate && l.candidate.confidence === "high");
      if (["pending", "confirmed", "rejected"].includes(filter))
        return locs.some(l => l.status === filter);
      return true;
    };

    const mainRow = mains.map(e => {
      if (!entryMatches(e, e.locators) && !subs.some(s => entryMatches(s, s.locators))) return "";
      return entryRowHtml(e, false, badEntries, badLocs);
    }).join("");
    const subRows = subs
      .filter(e => entryMatches(e, e.locators) || (q && (e.term + " " + e.subterm).toLowerCase().includes(q)))
      .map(e => entryRowHtml(e, true, badEntries, badLocs)).join("");
    if (!mainRow && !subRows) return "";
    return `<div class="entry-group">${mainRow}${subRows}</div>`;
  }).join("");

  $("#entryList").innerHTML = html || `<p class="muted small" style="padding:16px">没有匹配的条目</p>`;
  $$("#entryList .entry-main, #entryList .entry-sub").forEach(row => {
    row.addEventListener("click", e => {
      if (e.target.closest(".chip")) return;
      selectLocator(+row.dataset.entry, null);
    });
  });
  $$("#entryList .chip").forEach(chip => chip.addEventListener("click", e => {
    e.stopPropagation();
    selectLocator(+chip.dataset.entry, +chip.dataset.locator);
  }));
}

function entryRowHtml(e, isSub, badEntries, badLocs) {
  const active = selectedLoc && selectedLoc.entryId === e.id && !selectedLoc.locatorId
    ? "active" : "";
  let xref = "";
  if (e.kind !== "term") {
    xref = `<span class="entry-xref">${e.kind === "see" ? "见" : "参见"} ${esc(e.target || "?")}
      ${badEntries.has(e.id) ? '<span class="term-issue-dot">●</span>' : ""}</span>`;
  } else if (badEntries.has(e.id) && !e.locators.some(l => badLocs.has(l.id))) {
    xref = `<span class="term-issue-dot">●</span>`;
  }
  const chips = e.locators.map(l => {
    const sel = selectedLoc && selectedLoc.locatorId === l.id ? "active" : "";
    const c = l.candidate;
    const conf = c ? `conf-${confClass(c.confidence)}` : "";
    const dot = badLocs.has(l.id) ? ` <span class="term-issue-dot">●</span>` : "";
    const newTxt = l.status === "confirmed" ? `<b>→${rangeText(l, "new_")}</b>` : "";
    return `<span class="chip ${l.status} ${conf} ${sel}" data-entry="${e.id}" data-locator="${l.id}"
      title="${esc(l.anchor || "")}">旧${rangeText(l, "old_")}${newTxt}${dot}</span>`;
  }).join("");
  return `<div class="${isSub ? "entry-sub" : "entry-main"} ${active}" data-entry="${e.id}">
    <div class="entry-line">
      <div><span class="entry-term">${esc(isSub && e.subterm ? e.subterm : e.term)}</span> ${xref}</div>
      <div class="chips">${chips}</div>
    </div>
  </div>`;
}

/* ---------------- 选择定位号 / 并排页面 ---------------- */

function findLocator(entryId, locatorId) {
  const e = S.entries.find(x => x.id === entryId);
  if (!e) return null;
  if (locatorId) return { entry: e, loc: e.locators.find(l => l.id === locatorId) };
  return { entry: e, loc: e.locators[0] || null };
}

async function selectLocator(entryId, locatorId) {
  const found = findLocator(entryId, locatorId);
  if (!found) return;
  selectedLoc = { entryId, locatorId: found.loc ? found.loc.id : null };

  // 默认翻页：旧页到旧范围起点；新页到候选/已确认范围起点
  if (found.loc) {
    pagePos.old = found.loc.old_start;
    const ns = found.loc.new_start ?? (found.loc.candidate && found.loc.candidate.new_start);
    if (ns != null) pagePos.new = ns;
    shownHighlights = await computeHighlights(found.entry, found.loc);
  } else {
    shownHighlights = { new: {}, old: {} };
  }
  renderEntries();
  renderInspector();
  renderPages();
}

async function computeHighlights(entry, loc) {
  // 旧版：条目词高亮；新版：候选命中句高亮（需要候选详情）
  const out = { old: {}, new: {} };
  try {
    const cands = await api(`/api/locators/${loc.id}/candidates`);
    cands.forEach(c => {
      const hl = c.highlights || {};
      Object.entries(hl).forEach(([pg, sents]) => {
        out.new[pg] = (out.new[pg] || []).concat(sents);
      });
    });
  } catch (e) { /* 无候选 */ }
  return out;
}

function renderPages() {
  if (!S) return;
  const oldList = S.pages.old, newList = S.pages.new;
  if (!oldList.some(p => p.page_no === pagePos.old)) pagePos.old = oldList[0]?.page_no;
  if (!newList.some(p => p.page_no === pagePos.new)) pagePos.new = newList[0]?.page_no;
  $("#oldPgno").textContent = pagePos.old ?? "–";
  $("#newPgno").textContent = pagePos.new ?? "–";
  renderRangeLinks();
  renderAnchorBadges();
  renderPage("old");
  renderPage("new");
}

function renderRangeLinks() {
  const mk = (side, l) => {
    const s = l[side + "_start"], e = l[side + "_end"];
    if (s == null) return "";
    return `<button data-side="${side}" data-page="${s}">${e && e !== s ? s + "-" + e : s}</button>`;
  };
  const old = [], neu = [];
  if (selectedLoc) {
    const found = findLocator(selectedLoc.entryId, selectedLoc.locatorId);
    if (found && found.loc) {
      old.push(mk("old", found.loc));
      neu.push(mk("new", found.loc));
    }
  }
  $("#oldRangelinks").innerHTML = old.join("");
  $("#newRangelinks").innerHTML = neu.join("");
  $$(".rangelinks button").forEach(b => b.addEventListener("click", () => {
    pagePos[b.dataset.side] = +b.dataset.page;
    renderPage(b.dataset.side);
    $(`#${b.dataset.side}Pgno`).textContent = pagePos[b.dataset.side];
    renderAnchorBadges();
  }));
}

async function renderPage(side) {
  const no = pagePos[side];
  const box = $("#" + side + "Page");
  if (!no) { box.innerHTML = `<div class="placeholder">该版本尚未导入页面</div>`; return; }

  const key = `${currentPid}/${side}/${no}`;
  if (!pageCache[key]) {
    box.innerHTML = `<div class="placeholder">载入中…</div>`;
    try {
      pageCache[key] = await api(`/api/pages/${currentPid}/${side}/${no}`);
    } catch (e) {
      box.innerHTML = `<div class="placeholder">无此页</div>`;
      return;
    }
  }
  const page = pageCache[key];
  const chapterStarts = S.chapters[side] || [];
  const badge = chapterStarts.includes(no) ? `<span class="tag">章节起始页</span>` : "";

  let termWords = [];
  if (selectedLoc) {
    const found = findLocator(selectedLoc.entryId, selectedLoc.locatorId);
    if (found && found.entry.kind === "term") {
      termWords = (found.entry.subterm || found.entry.term)
        .toLowerCase().split(/[^a-z0-9一-鿿]+/i).filter(w => w.length >= 3);
    }
  }
  const hitSents = (shownHighlights[side][no] || []).slice();
  const inRange = selectedLoc && inLocatorRange(side, no);
  const anc = anchorAt(side, no), seg = segmentOf(side, no);
  const pane = box.closest(".page-pane");
  if (pane) {
    pane.classList.remove(...[...pane.classList].filter(c => c.startsWith("seg-c")));
    if (seg) pane.classList.add("seg-c" + seg.index % 6);
    pane.classList.toggle("is-anchor-page", !!anc);
  }
  let anchorBanner = "";
  if (anc) {
    const other = side === "old" ? anc.new_page : anc.old_page;
    anchorBanner = `<div class="anchor-banner">⚓ 锚点页：${side === "old" ? "旧" : "新"}${no} 固定对应${side === "old" ? "新" : "旧"}${other}
      ${anc.note ? ` · ${esc(anc.note)}` : ""}</div>`;
  } else if (seg) {
    const kindName = { head: "首段外推", tail: "尾段外推", interval: "锚点区间", single: "单锚平移" }[seg.kind] || "";
    anchorBanner = `<div class="seg-banner seg-c${seg.index % 6}">映射区间 ${seg.index}（${kindName}）：
      旧 ${seg.old_start}–${seg.old_end} → 新 ${seg.new_start ?? "?"}–${seg.new_end ?? "?"}</div>`;
  }
  box.innerHTML =
    `<div class="page-label">${badge} ${side === "old" ? "旧版" : "新版"} · 第 ${no} 页
      ${inRange ? '<span class="tag combined">当前定位范围</span>' : ""}</div>` +
    anchorBanner +
    highlightContent(page.content, termWords, hitSents);
}

function inLocatorRange(side, no) {
  const found = selectedLoc && findLocator(selectedLoc.entryId, selectedLoc.locatorId);
  if (!found || !found.loc) return false;
  const l = found.loc;
  if (side === "old") return no >= l.old_start && no <= (l.old_end || l.old_start);
  const s = l.new_start, e = l.new_end ?? l.new_start;
  return s != null && no >= s && no <= e;
}

/* 高亮：先给命中句打标，再给条目词打标；输出 <p>/换行保留的 HTML */
function highlightContent(text, termWords, hitSents) {
  const normHit = s => s.toLowerCase().replace(/\s+/g, " ").trim();
  const hitNorm = hitSents.map(normHit);

  // 句子级：用与后端近似的分句，norm 命中即标
  const sentenceSpans = [];
  const re = /[^.!?。！？\n]+[.!?。！？]?/g;
  let m;
  while ((m = re.exec(text))) {
    const raw = m[0];
    if (!raw.trim()) continue;
    const n = normHit(raw);
    if (hitNorm.some(h => n.includes(h.slice(0, 60)))) {
      sentenceSpans.push([m.index, m.index + raw.length]);
    }
  }

  const marks = [];
  sentenceSpans.forEach(([a, b]) => marks.push([a, b, 1]));
  if (termWords.length) {
    const lowered = text.toLowerCase();
    termWords.forEach(w => {
      let from = 0, idx;
      while ((idx = lowered.indexOf(w, from)) !== -1) {
        marks.push([idx, idx + w.length, 0]);
        from = idx + w.length;
      }
    });
  }
  marks.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
  // 去重叠：句子标包含词标时丢弃词标
  const picked = [];
  for (const mk2 of marks) {
    if (picked.some(p => mk2[0] >= p[0] && mk2[1] <= p[1])) continue;
    picked.push(mk2);
  }

  let out = "", cursor = 0;
  for (const [a, b, kind] of picked) {
    out += esc(text.slice(cursor, a));
    out += `<mark class="${kind ? "hit" : "term"}">${esc(text.slice(a, b))}</mark>`;
    cursor = b;
  }
  out += esc(text.slice(cursor));
  return out
    .split(/\n{2,}/).map(p => `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");
}

/* ---------------- 右栏：候选检查器 ---------------- */

function renderInspector() {
  const box = $("#inspectContent");
  if (!selectedLoc) { box.innerHTML = `<p class="muted">在左侧选择一个页码定位号。</p>`; return; }
  const found = findLocator(selectedLoc.entryId, selectedLoc.locatorId);
  if (!found || !found.loc) {
    box.innerHTML = `<h3>${esc(found ? found.entry.term : "")}</h3>
      <p class="muted">该交叉引用条目没有页码定位号。</p>`;
    return;
  }
  const { entry, loc } = found;
  const c = loc.candidate;

  const newPill = loc.status === "confirmed"
    ? `<span class="range-pill confirmed">新版 ${rangeText(loc, "new_")}</span>`
    : loc.status === "rejected"
      ? `<span class="range-pill rejected">已拒绝</span>`
      : c ? `<span class="range-pill new">候选新版 ${rangeText(c, "new_")}</span>`
        : `<span class="range-pill">无候选</span>`;

  let candHtml = "";
  if (c) {
    const aNotes = (c.anchor_notes || []).map(n =>
      `<li class="anchor-note" title="${esc(n[0])}">${esc(n[1])}</li>`).join("");
    candHtml = `
      <div class="conf-bar"><i class="${confClass(c.confidence)}"
        style="width:${Math.round((c.score || 0) * 100)}%"></i></div>
      <div class="${confClass(c.confidence)}"><b>${confLabel(c.confidence)}</b>
        · 综合分 ${(c.score || 0).toFixed(2)} · 方法 ${methodLabel(c.method)}
        ${c.anchor_pinned ? '<span class="tag anchor-tag">⚓ 锚点固定</span>' : ""}</div>
      ${aNotes ? `<ul class="anchor-notes">${aNotes}</ul>` : ""}`;
    if (c.reasons && c.reasons.length) {
      candHtml += `<ul class="reasons">${c.reasons.map(r =>
        `<li title="${esc(r[0])}">${esc(r[1])}</li>`).join("")}</ul>`;
    } else {
      candHtml += `<p class="small muted">无歧义信号：指纹、邻近语句与标题词一致。</p>`;
    }
    candHtml += `<div id="altCandidates" class="small muted">展开其余候选…</div>`;
  } else {
    candHtml = `<p class="muted">未找到候选页，可直接在下方手动改绑新版页码。</p>`;
  }

  box.innerHTML = `
    <div class="loc-head">
      <h3>${esc(entry.term)}${entry.subterm ? " — " + esc(entry.subterm) : ""}</h3>
      <div class="range-flow">
        <span class="range-pill">旧版 ${rangeText(loc, "old_")}</span>
        <span class="arrow">→</span>${newPill}
      </div>
    </div>
    <div class="anchor-box"><b>旧版邻近语句：</b>${esc(loc.anchor || "（窗口内未提取到语句）")}</div>
    ${candHtml}
    <div class="decision-row">
      <button class="primary" id="btnConfirm" ${loc.status === "confirmed" ? "disabled" : ""}>✓ 确认候选</button>
      <button id="btnReject" ${loc.status === "rejected" ? "disabled" : ""}>✗ 拒绝</button>
      <button id="btnReset">↺ 重置待确认</button>
    </div>
    <div class="rebind-row">
      <span class="small">改绑新版页：</span>
      <input type="number" id="rebindStart" min="1" placeholder="起" value="${loc.new_start ?? ""}">
      <input type="number" id="rebindEnd" min="1" placeholder="止（单页可空）" value="${loc.new_end ?? ""}">
      <button id="btnRebind">改绑</button>
    </div>
    <div id="rangeNote" class="small muted" style="margin-top:8px"></div>`;

  $("#btnConfirm").onclick = () => decide(loc.id, "confirm");
  $("#btnReject").onclick = () => decide(loc.id, "reject");
  $("#btnReset").onclick = () => decide(loc.id, "reset");
  $("#btnRebind").onclick = () => {
    const s = +$("#rebindStart").value, e = $("#rebindEnd").value ? +$("#rebindEnd").value : s;
    if (!s || e < s) { toast("页码无效（起点为空或起止倒置）", "error"); return; }
    decide(loc.id, "rebind", { start: s, end: e });
  };
  $("#altCandidates") && ($("#altCandidates").onclick = e => expandAlternatives(loc.id, e.target));

  // 受影响范围提示
  const sibs = entry.locators.filter(x => x.id !== loc.id);
  if (sibs.length) {
    $("#rangeNote").innerHTML = `本条目还有 ${sibs.length} 个定位号；每次决定后系统会重算
      同条目待确认定位号与全部页码范围并重新校验。`;
  }
}

async function expandAlternatives(locatorId, el) {
  const cands = await api(`/api/locators/${locatorId}/candidates`);
  el.outerHTML = cands.slice(1).map(c => `
    <div class="cand-card" data-s="${c.new_start}">
      <div class="cc-head"><span>新版 ${rangeText(c, "new_")}</span>
        <span class="${confClass(c.confidence)}">${(c.score || 0).toFixed(2)}</span></div>
      <div class="cc-meta"><span class="tag ${c.method}">${methodLabel(c.method)}</span>
        ${confLabel(c.confidence)}</div>
    </div>`).join("") || `<p class="small muted">无其他候选。</p>`;
  $$("#altCandidates, #inspectContent .cand-card").forEach(card =>
    card.addEventListener("click", () => {
      pagePos.new = +card.dataset.s;
      renderPages();
    }));
}

async function decide(locatorId, action, extra = {}) {
  try {
    const r = await api(`/api/locators/${locatorId}/decision`,
      { method: "POST", body: { action, ...extra } });
    await reloadState();
    // 保持当前定位号选中
    selectedLoc = { entryId: selectedLoc.entryId, locatorId: action === "reset" ? locatorId : locatorId };
    const found = findLocator(selectedLoc.entryId, locatorId);
    if (found && found.loc) {
      pagePos.old = found.loc.old_start;
      if (found.loc.new_start != null) pagePos.new = found.loc.new_start;
      shownHighlights = await computeHighlights(found.entry, found.loc);
    }
    renderEntries(); renderInspector(); renderPages();
    const verb = { confirm: "已确认", reject: "已拒绝", rebind: "已改绑", reset: "已重置" }[action];
    toast(`${verb}；受影响页码范围已重算并重新校验`, "success");
  } catch (e) { toast(e.message, "error"); }
}

/* ---------------- 页对照锚点 ---------------- */

function A() { return (S && S.anchors) || { anchors: [], segments: [], page_map: {}, conflicts: [] }; }
function activeAnchors() { return A().anchors.filter(a => a.status === "active"); }

function anchorAt(side, pageNo) {
  if (!pageNo) return null;
  return activeAnchors().find(a => a[side === "old" ? "old_page" : "new_page"] === pageNo) || null;
}

function segmentOf(side, pageNo) {
  const segs = A().segments, pmap = A().page_map;
  if (!segs.length || pageNo == null) return null;
  if (side === "old") {
    return segs.find(g => pageNo >= g.old_start && pageNo <= g.old_end) || null;
  }
  // 新页：找其反投影落入的区间
  for (const g of segs) {
    if (g.new_start == null) continue;
    if (pageNo >= Math.min(g.new_start, g.new_end) && pageNo <= Math.max(g.new_start, g.new_end))
      return g;
  }
  // 外推段超出投影页时按最近锚点归类
  return null;
}

function renderAnchorBadges() {
  if (!S) return;
  for (const side of ["old", "new"]) {
    const no = pagePos[side], a = anchorAt(side, no), seg = segmentOf(side, no);
    const badge = $("#" + side + "AnchorBadge");
    badge.innerHTML = "";
    if (a) {
      const cls = side === "old" ? "old" : "new";
      badge.innerHTML = `<span class="anc-badge ${cls}" title="页对照锚点">⚓ 旧${a.old_page}→新${a.new_page}</span>`;
    } else if (seg) {
      badge.innerHTML = `<span class="seg-badge seg-c${seg.index % 6}" title="所属映射区间">区间 ${seg.index}</span>`;
    }
  }
  const qb = A();
  const act = qb.anchors.filter(a => a.status === "active").length;
  $("#anchorCount").textContent = act;
  $("#qbOld").textContent = pagePos.old ?? "–";
  $("#qbNew").textContent = pagePos.new ?? "–";
  const dupPair = qb.anchors.some(a =>
    a.old_page === pagePos.old && a.new_page === pagePos.new);
  const btn = $("#setAnchorBtn");
  btn.disabled = !pagePos.old || !pagePos.new || dupPair;
  btn.textContent = dupPair ? "⚓ 这对页已是锚点" : "⚓ 把当前两页设为对照锚点";
}

async function createAnchorFromPages() {
  const oldPage = pagePos.old, newPage = pagePos.new;
  const note = $("#anchorQuickNote").value.trim();
  if (!oldPage || !newPage) { toast("请先在两侧选定页面", "error"); return; }
  try {
    const r = await api(`/api/projects/${currentPid}/anchors`, {
      method: "POST", body: { old_page: oldPage, new_page: newPage, note, active: true },
    });
    S.anchors = r;
    $("#anchorQuickNote").value = "";
    renderAll();
    toast(`已设锚点 旧${oldPage}→新${newPage}；单调分段映射已更新`, "success");
  } catch (e) {
    // 被拒绝（倒退/重复/越界）：提供“保存为停用锚点”的选择，便于稍后调整
    if (confirm(`锚点被拒绝：${e.message}\n\n仍要保存为「停用」锚点（在锚点面板中可调整后再启用）吗？`)) {
      try {
        const r = await api(`/api/projects/${currentPid}/anchors`, {
          method: "POST", body: { old_page: oldPage, new_page: newPage, note, active: false },
        });
        S.anchors = r;
        $("#anchorQuickNote").value = "";
        renderAll();
        toast("已保存为停用锚点，可在锚点面板启用", "");
      } catch (e2) { toast(e2.message, "error"); }
    }
  }
}

function renderAnchors() {
  const box = $("#anchorsContent");
  if (!S) return;
  const qb = A();
  const act = qb.anchors.filter(a => a.status === "active");
  const dis = qb.anchors.filter(a => a.status !== "active");

  let segHtml = "";
  if (qb.segments.length) {
    segHtml = `<h4>单调分段映射（${act.length} 个启用锚点）</h4>
      <div class="seg-list">${qb.segments.map(g => {
        const kindName = { head: "首段外推", tail: "尾段外推", interval: "锚点区间", single: "单锚平移" }[g.kind];
        const slope = g.slope == null ? "" :
          ` · 斜率 ${g.slope === 1 ? "1（平移）" : g.slope.toFixed(2)} 页/页`;
        return `<div class="seg-row seg-c${g.index % 6}">
          <b>区间 ${g.index}</b> <span class="tag">${kindName}</span>
          旧 ${g.old_start}–${g.old_end} → 新 ${g.new_start ?? "?"}–${g.new_end ?? "?"}
          <span class="muted small">${slope}</span></div>`;
      }).join("")}</div>`;
  }

  const rowHtml = (a) => `
    <div class="anchor-row ${a.status}" data-aid="${a.id}">
      <div class="ar-head">
        <span class="ar-pages">⚓ 旧<b>${a.old_page}</b> → 新<b>${a.new_page}</b></span>
        <span class="ar-actions">
          <button data-aact="toggle" data-aid="${a.id}">${a.status === "active" ? "停用" : "启用"}</button>
          <button data-aact="delete" data-aid="${a.id}">删除</button>
        </span>
      </div>
      <input class="ar-note" data-aid="${a.id}" value="${esc(a.note || "")}"
        placeholder="备注…（回车保存）">
      ${a.status === "active" ? "" : '<span class="tag disabled-tag">已停用 · 不参与映射</span>'}
    </div>`;

  let conflictsHtml = "";
  if (qb.conflicts.length) {
    conflictsHtml = `<h4>冲突与漂移（${qb.conflicts.length}）</h4>
      <div class="anchor-conflicts">${qb.conflicts.map(c => conflictHtml(c)).join("")}</div>`;
  }

  box.innerHTML = `
    <div class="anchor-intro small muted">在中间并排预览中翻到对应页，点击
      「⚓ 把当前两页设为对照锚点」即可建立对照点并填写备注。启用锚点的旧页、新页
      都必须严格递增；重匹配时锚点页固定，只重算相邻区间内未确认的定位号。</div>
    <div class="anchor-toolbar">
      <button id="rematchPreviewBtn" class="primary" ${act.length ? "" : "disabled"}>
        ▶ 按锚点重匹配（先预览）</button>
      <button id="anchorExportBtn">导出对照表 JSON</button>
    </div>
    ${segHtml}
    <h4>启用锚点</h4>
    <div id="activeAnchorList">${act.map(rowHtml).join("") || '<p class="muted small">尚无启用锚点。</p>'}</div>
    ${dis.length ? `<h4>停用锚点</h4><div id="disabledAnchorList">${dis.map(rowHtml).join("")}</div>` : ""}
    ${conflictsHtml}`;

  $("#rematchPreviewBtn")?.addEventListener("click", openRematchPreview);
  $("#anchorExportBtn")?.addEventListener("click", () =>
    window.open(`/api/projects/${currentPid}/export/anchors`, "_blank"));
  $$("#anchorsContent [data-aact]").forEach(b => b.addEventListener("click", () =>
    anchorAction(+b.dataset.aid, b.dataset.aact)));
  $$("#anchorsContent .ar-note").forEach(inp => {
    inp.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); saveAnchorNote(+inp.dataset.aid, inp.value); }
    });
    inp.addEventListener("blur", () => {
      const cur = A().anchors.find(a => a.id === +inp.dataset.aid);
      if (cur && (cur.note || "") !== inp.value.trim()) saveAnchorNote(+inp.dataset.aid, inp.value, true);
    });
  });
}

function conflictHtml(c) {
  if (c.kind === "disabled_anchor") {
    return `<div class="conf-item disabled" data-aid="${c.anchor_id}">
      <span class="sev-badge warning">停用锚点</span>
      旧${c.old_page}→新${c.new_page}：${esc(c.message)}
      ${c.note ? `<div class="small muted">备注：${esc(c.note)}</div>` : ""}
      <button data-aact="delete" data-aid="${c.anchor_id}">删除</button></div>`;
  }
  const kind = c.kind === "confirmed_drift"
    ? '<span class="sev-badge error">已确认漂移</span>'
    : '<span class="sev-badge warning">候选漂移</span>';
  return `<div class="conf-item drift" data-loc="${c.locator_id}">
    ${kind} <b>${esc((c.subterm ? c.term + " — " + c.subterm : c.term))}</b>
    旧${c.old_range[0] === c.old_range[1] ? c.old_range[0] : c.old_range.join("-")}：
    当前新 ${c.actual_range.join("-")}，锚点投影区间 新${c.expected_range.join("-")}
    <div class="small muted">${esc(c.message)}</div></div>`;
}

async function anchorAction(aid, act) {
  try {
    if (act === "delete") {
      if (!confirm("删除该锚点？（可随后用顶栏「撤销」恢复）")) return;
      const r = await api(`/api/anchors/${aid}`, { method: "DELETE" });
      S.anchors = r;
    } else if (act === "toggle") {
      const r = await api(`/api/anchors/${aid}/toggle`, { method: "POST" });
      S.anchors = r;
      toast(r.status === "active" ? "锚点已启用，映射已扩展" : "锚点已停用", "success");
    }
    renderAll();
  } catch (e) {
    toast(e.message, "error");
    // 启用失败（与现有锚点冲突）时刷新面板，让用户看到最新状态
    const fresh = await api(`/api/projects/${currentPid}/anchors`);
    S.anchors = fresh;
    renderAll();
  }
}

async function saveAnchorNote(aid, note, quiet) {
  const r = await api(`/api/anchors/${aid}/note`, {
    method: "POST", body: { note: note.trim() },
  });
  S.anchors = r;
  renderAll();
  if (!quiet) toast("备注已保存", "success");
}

/* ---------------- 锚点重匹配（预览 + 执行） ---------------- */

async function openRematchPreview() {
  if (!activeAnchors().length) { toast("还没有启用的锚点", "error"); return; }
  toast("正在按锚点在后台试算（不改动当前数据）…");
  let r;
  try {
    r = await api(`/api/projects/${currentPid}/rematch/preview`, { method: "POST" });
  } catch (e) { toast(e.message, "error"); return; }
  $("#rematchModal").classList.remove("hidden");
  $("#rematchSummary").innerHTML =
    `启用锚点 <b>${r.anchor_count}</b> 个；待确认定位号 <b>${r.pending_total}</b> 条，` +
    `其中 <b class="${r.affected ? "" : "ok-"}">${r.affected}</b> 条头名候选将发生变化。`;
  const rng = c => c[0] === c[1] ? `${c[0]}` : `${c[0]}-${c[1]}`;
  const rows = [
    ...r.changes.map(c => `<tr>
      <td>${esc(c.label)} <span class="muted">旧${rng(c.old_locator)}</span></td>
      <td class="cell-before">新${rng(c.before)} <span class="small muted">${methodLabel(c.before_method)}</span></td>
      <td class="cell-arrow">→</td>
      <td class="cell-after">新${rng(c.after)} <span class="small muted">${methodLabel(c.after_method)}</span></td></tr>`),
    ...r.added.map(c => `<tr><td>${esc(c.label)}</td>
      <td class="cell-before muted">无候选</td><td class="cell-arrow">→</td>
      <td class="cell-after">新${rng(c.after)}</td></tr>`),
    ...r.dropped.map(c => `<tr><td>${esc(c.label)}</td>
      <td class="cell-before">有候选</td><td class="cell-arrow">→</td>
      <td class="cell-after muted">无候选</td></tr>`),
  ].join("");
  $("#rematchBody").innerHTML = rows
    ? `<table class="rematch-table"><thead><tr><th>条目 / 旧页</th><th>原候选</th><th></th><th>新候选</th></tr></thead>
       <tbody>${rows}</tbody></table>`
    : `<p class="muted">所有待确认定位号的头名候选都保持不变（锚点约束与当前结果一致）。</p>`;
}

async function executeRematch() {
  try {
    const r = await api(`/api/projects/${currentPid}/rematch`, { method: "POST" });
    $("#rematchModal").classList.add("hidden");
    await reloadState();
    toast(`重匹配完成：${r.affected} 条候选变化，${r.pending_total} 条待确认已重算`, "success");
  } catch (e) { toast(e.message, "error"); }
}

/* ---------------- 问题 ---------------- */

function renderIssuesBadge() {
  $("#issueCount").textContent = S.issues.length;
}

function renderIssues() {
  if (!S.issues.length) {
    $("#issuesContent").innerHTML = `<p class="muted">没有未决问题。✅</p>`;
    return;
  }
  const codeName = {
    inverted_range: "倒置范围", broken_range: "断裂范围", out_of_bounds: "超出全书",
    duplicate_locator: "重复页码", duplicate_see: "重复参见", missing_target: "缺失目标",
    subterm_contradiction: "主子矛盾", cross_chapter: "跨章范围", chapter_parity: "章首页规则",
  };
  $("#issuesContent").innerHTML = S.issues.map(i => `
    <div class="issue-item ${i.severity}" data-entry="${i.entry_id || ""}" data-loc="${i.locator_id || ""}">
      <span class="sev-badge ${i.severity}">${i.severity === "error" ? "错误" : "警告"}</span>
      <span class="ii-code">${codeName[i.code] || i.code}</span>
      <div class="ii-msg">${esc(i.message)}</div>
    </div>`).join("");
  $$("#issuesContent .issue-item").forEach(it => it.addEventListener("click", () => {
    const eid = +it.dataset.entry, lid = +it.dataset.loc;
    if (!eid) return;
    $$(".tab").forEach(x => x.classList.toggle("active", x.dataset.tab === "inspect"));
    ["inspect", "anchors", "issues", "versions", "settings"].forEach(n =>
      $("#tab-" + n).classList.toggle("hidden", n !== "inspect"));
    selectLocator(eid, lid || null);
  }));
}

/* ---------------- 版本 / 历史 ---------------- */

async function doSnapshot() {
  const name = $("#snapName").value.trim() || `版本 ${new Date().toLocaleString()}`;
  await api(`/api/projects/${currentPid}/snapshots`, { method: "POST", body: { name } });
  $("#snapName").value = "";
  renderVersions();
  toast("已保存命名版本", "success");
}

function renderVersions() {
  $("#snapList").innerHTML = S.snapshots.map(s => `
    <div class="snap-item">
      <span>📌 ${esc(s.name)}</span>
      <button data-restore="${s.id}">恢复</button>
      <span class="ts">${s.ts}</span>
    </div>`).join("") || `<p class="muted small">尚无命名版本。</p>`;
  $$("[data-restore]").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("恢复该版本？当前状态会保留在撤销栈中（可撤销恢复）。")) return;
    await api(`/api/projects/${currentPid}/snapshots/${b.dataset.restore}/restore`,
      { method: "POST" });
    await reloadState();
    toast("已恢复版本（可用撤销回退）", "success");
  }));

  $("#historyList").innerHTML = S.history.map(h => `
    <div class="hist-item"><span>${actionIcon(h.kind)} ${esc(describeAction(h.detail))}</span>
      <span class="ts">${h.ts}</span></div>`).join("") ||
    `<p class="muted small">尚无操作。</p>`;
}

function actionIcon(k) {
  return { confirm: "✓", reject: "✗", rebind: "🔗", reset: "↺", batch_accept: "⚡",
           restore: "📌", anchor_add: "⚓", anchor_toggle: "⚓",
           anchor_delete: "⚓", anchor_note: "⚓" }[k] || "•";
}

function describeAction(d) {
  if (d.kind && d.kind.startsWith("anchor_")) {
    const pair = `旧${d.old_page}→新${d.new_page}`;
    if (d.kind === "anchor_add")
      return `${d.status === "disabled" ? "停用保存" : "新增"}锚点 ${pair}` + (d.note ? `（${d.note}）` : "");
    if (d.kind === "anchor_toggle")
      return `${d.to === "active" ? "启用" : "停用"}锚点 ${pair}`;
    if (d.kind === "anchor_delete") return `删除锚点 ${pair}`;
    if (d.kind === "anchor_note") return `修改锚点备注`;
  }
  if (d.kind === "batch_accept" || d.label === undefined) {
    if (d.accepted) return `批量接受 ${d.accepted.length} 个高置信定位号`;
    if (d.snapshot) return `恢复版本「${d.snapshot}」`;
    return "操作";
  }
  const label = d.label;
  const old = d.old ? `旧${d.old[1] && d.old[1] !== d.old[0] ? d.old[0] + "-" + d.old[1] : d.old[0]}` : "";
  const neu = d.new ? `→新${d.new[1] && d.new[1] !== d.new[0] ? d.new[0] + "-" + d.new[1] : d.new[0]}` : "";
  const map = {
    confirm: `确认 ${label} ${old}${neu}`,
    reject: `拒绝 ${label} ${old}`,
    rebind: `改绑 ${label} ${old}${neu}`,
    reset: `重置 ${label} ${old}`,
  };
  return map[d.kind] || d.kind;
}

/* ---------------- 规则设置 ---------------- */

function fillSettings() {
  $("#setPolicy").value = S.project.chapter_policy;
  $("#setCross").checked = S.project.cross_chapter;
  $("#setRegex").value = S.project.heading_regex;
  $("#setThreshold").value = S.project.batch_threshold;
  $("#setChapters").textContent = (S.chapters.new.length ? S.chapters.new.join(", ") : "未识别到");
}

async function saveSettings() {
  try {
    new RegExp($("#setRegex").value);
  } catch (e) { toast("章节标题正则无效：" + e.message, "error"); return; }
  await api(`/api/projects/${currentPid}/settings`, {
    method: "POST",
    body: {
      chapter_policy: $("#setPolicy").value,
      cross_chapter: $("#setCross").checked,
      heading_regex: $("#setRegex").value,
      batch_threshold: +$("#setThreshold").value,
    },
  });
  await reloadState();
  fillSettings();
  toast("规则已保存，全部问题重新检查", "success");
}
