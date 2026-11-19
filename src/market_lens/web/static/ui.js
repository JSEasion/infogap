/* ============================================================
   ui.js — 交互层：fetch 封装 / toast / modal / 确认弹窗 / 任务中心 / 主题
   企业级交互规范：操作 ≤200ms 反馈；长操作任务化；破坏性操作确认弹窗
   ============================================================ */
"use strict";

/* ---------------- 主题 ---------------- */
(function initTheme() {
  const saved = localStorage.getItem("ds-theme");
  if (saved) document.documentElement.dataset.theme = saved;
})();
function toggleTheme() {
  const cur = document.documentElement.dataset.theme === "dark" ? "" : "dark";
  document.documentElement.dataset.theme = cur;
  localStorage.setItem("ds-theme", cur);
}

/* ---------------- Toast ---------------- */
function dsToast(title, message, kind, ms) {
  let host = document.getElementById("ds-toasts");
  if (!host) {
    host = document.createElement("div"); host.id = "ds-toasts";
    document.body.appendChild(host);
  }
  const el = document.createElement("div");
  el.className = "ds-toast " + (kind || "");
  el.innerHTML = `<div class="t"></div>` + (message ? `<div class="m"></div>` : "");
  el.querySelector(".t").textContent = title;
  if (message) el.querySelector(".m").textContent = message;
  const dismiss = () => { el.classList.add("leaving"); setTimeout(() => el.remove(), 320); };
  el.onclick = dismiss;
  host.appendChild(el);
  setTimeout(dismiss, ms || (kind === "err" ? 7000 : 4200));
  return el;
}

/* ---------------- fetch 封装（统一 loading / 错误 toast） ---------------- */
async function dsFetch(url, opts) {
  opts = opts || {};
  const btn = opts.trigger;
  if (btn) { btn.dataset.origHtml = btn.innerHTML; btn.classList.add("loading"); btn.disabled = true; }
  try {
    const resp = await fetch(url, {
      method: opts.method || "GET",
      headers: opts.json ? { "Content-Type": "application/json" } : undefined,
      body: opts.json ? JSON.stringify(opts.json) : opts.body,
    });
    const ct = resp.headers.get("content-type") || "";
    const data = ct.includes("json") ? await resp.json() : await resp.text();
    if (!resp.ok) throw new Error((data && data.detail) || `HTTP ${resp.status}`);
    if (opts.okToast) dsToast(opts.okToast, "", "ok");
    return data;
  } catch (e) {
    dsToast(opts.errTitle || "操作失败", String(e.message || e), "err");
    throw e;
  } finally {
    if (btn) { btn.classList.remove("loading"); btn.disabled = false; }
  }
}

/* ---------------- Modal（focus trap + Esc） ---------------- */
function dsModal(title, bodyHtml, buttons) {
  const backdrop = document.createElement("div");
  backdrop.className = "ds-modal-backdrop";
  backdrop.innerHTML = `
    <div class="ds-modal" role="dialog" aria-modal="true">
      <div class="ds-modal-head"><span class="t"></span>
        <button class="ds-iconbtn ds-modal-x" aria-label="关闭">✕</button></div>
      <div class="ds-modal-body"></div>
      <div class="ds-modal-foot"></div>
    </div>`;
  backdrop.querySelector(".t").textContent = title;
  backdrop.querySelector(".ds-modal-body").innerHTML = bodyHtml;
  const foot = backdrop.querySelector(".ds-modal-foot");
  (buttons || [{ label: "关闭" }]).forEach(b => {
    const btn = document.createElement("button");
    btn.className = "ds-btn " + (b.cls || "plain");
    btn.textContent = b.label;
    btn.onclick = () => { const keep = b.onClick && b.onClick(backdrop); if (!keep) close(); };
    foot.appendChild(btn);
  });
  function close() { backdrop.remove(); document.removeEventListener("keydown", onKey); }
  function onKey(e) { if (e.key === "Escape") close(); }
  backdrop.querySelector(".ds-modal-x").onclick = close;
  backdrop.onclick = e => { if (e.target === backdrop) close(); };
  document.addEventListener("keydown", onKey);
  document.body.appendChild(backdrop);
  const first = backdrop.querySelector("input, textarea, select") ||
    backdrop.querySelector(".ds-modal-foot .ds-btn");
  if (first) first.focus();
  return { close, el: backdrop };
}

/* ---------------- 确认弹窗（替代浏览器 confirm） ---------------- */
function dsConfirm(title, message, onOk, okLabel, danger) {
  dsModal(title, `<p style="margin:0;color:var(--muted)"></p>`, [
    { label: "取消" },
    { label: okLabel || "确认", cls: danger ? "danger" : "", onClick: () => onOk && onOk() },
  ]);
  document.querySelector(".ds-modal-body p").textContent = message;
}

/* ---------------- Tabs ---------------- */
document.addEventListener("click", e => {
  const tab = e.target.closest("[data-ds-tab]");
  if (!tab) return;
  const group = tab.closest("[data-ds-tabs]");
  group.querySelectorAll("[data-ds-tab]").forEach(t => t.classList.remove("active"));
  tab.classList.add("active");
  const target = group.dataset.dsTabs;
  document.querySelectorAll(`[data-ds-tabpanel="${target}"]`).forEach(p => {
    p.hidden = p.dataset.dsPanel !== tab.dataset.dsTab;
  });
});

/* ============================================================
   任务中心：触发即返回 → FAB 指示器 → 进度面板 → 完成 toast（刷新安全）
   ============================================================ */
const TaskCenter = {
  seen: {},          // id → status（检测状态变化 → 完成通知）
  panelOpen: false,
  timer: null,

  async submit(kind, params, title, btn) {
    try {
      const data = await dsFetch("/api/tasks", { method: "POST",
        json: { kind, params, title }, trigger: btn });
      dsToast("任务已提交", title + " · 进度见右下角任务中心", "ok", 3000);
      this.refresh(true);
      return data.task_id;
    } catch (e) { /* dsFetch 已 toast */ }
  },

  async refresh(started) {
    try {
      const tasks = await fetch("/api/tasks?limit=12").then(r => r.json());
      this.render(tasks);
      if (started) this.open();
    } catch (e) { /* 静默：轮询失败不打扰 */ }
  },

  render(tasks) {
    let host = document.getElementById("ds-taskbar");
    if (!host) {
      host = document.createElement("div"); host.id = "ds-taskbar";
      host.className = "ds-taskbar";
      host.innerHTML = `
        <div class="ds-task-panel" id="ds-task-panel" hidden></div>
        <button class="ds-task-fab" id="ds-task-fab">📋 任务</button>`;
      document.body.appendChild(host);
      host.querySelector("#ds-task-fab").onclick = () => this.toggle();
    }
    const active = tasks.filter(t => ["queued", "running"].includes(t.status));
    const fab = host.querySelector("#ds-task-fab");
    fab.innerHTML = active.length
      ? `<span class="dot"></span> ${active.length} 个任务进行中`
      : "📋 任务中心";
    const panel = host.querySelector("#ds-task-panel");
    panel.innerHTML = tasks.map(t => this.itemHtml(t)).join("") ||
      '<div class="ds-empty" style="padding:24px"><div class="icon">🗂</div>暂无任务</div>';
    panel.querySelectorAll("[data-cancel]").forEach(b => b.onclick = () => this.cancel(b.dataset.cancel));
    // 完成检测：状态从 active → done/failed 弹 toast
    tasks.forEach(t => {
      const prev = this.seen[t.id];
      if (prev && ["queued", "running"].includes(prev) &&
          ["done", "failed", "cancelled"].includes(t.status)) {
        if (t.status === "done") {
          const r = t.result || {};
          dsToast("✅ " + t.title,
            r.curated !== undefined
              ? `精选 ${r.curated ?? 0} · 隔离 ${r.quarantined ?? 0} · 拦截 ${r.rejected ?? 0}` : "",
            "ok", 6000);
        } else if (t.status === "failed") {
          dsToast("❌ " + t.title, t.error || "", "err");
        }
      }
      this.seen[t.id] = t.status;
    });
    // 有活动任务 → 高频轮询；空闲 → 低频
    const freq = active.length ? 1500 : 6000;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.refresh(), freq);
  },

  itemHtml(t) {
    const pct = t.progress == null
      ? `<div class="ds-progress indet"><i></i></div>`
      : `<div class="ds-progress"><i style="width:${Math.round(t.progress)}%"></i></div>`;
    const status = { done: "✅", failed: "❌", cancelled: "⛔", orphaned: "🕳" }[t.status] || "⏳";
    const cancel = ["queued", "running"].includes(t.status)
      ? `<button class="ds-btn ghost sm" data-cancel="${t.id}">取消</button>` : "";
    const result = t.status === "done" && t.result
      ? Object.entries(t.result).filter(([k]) => !["report"].includes(k))
          .slice(0, 4).map(([k, v]) => `${k}: ${v}`).join(" · ") : "";
    return `<div class="ds-task-item ${t.status}">
      <div class="row1"><span>${status} ${t.title}</span>${cancel}</div>
      <div class="stage">${t.stage || ""}${t.message ? " · " + t.message : ""}</div>
      ${pct}${result ? `<div class="stage">${result}</div>` : ""}</div>`;
  },

  toggle() {
    const panel = document.getElementById("ds-task-panel");
    if (panel) { panel.hidden = !panel.hidden; this.refresh(); }
  },
  open() {
    const panel = document.getElementById("ds-task-panel");
    if (panel) panel.hidden = false;
  },
  async cancel(id) {
    await dsFetch(`/api/tasks/${id}/cancel`, { method: "POST", okToast: "已请求取消" });
    this.refresh();
  },
};

/* 页面加载：恢复进行中任务（刷新安全）+ 启动轮询 */
document.addEventListener("DOMContentLoaded", () => TaskCenter.refresh());

/* ---------------- 通用：flash → toast（旧 redirect 消息兼容） ---------------- */
document.addEventListener("DOMContentLoaded", () => {
  const params = new URLSearchParams(location.search);
  const msg = params.get("msg"), err = params.get("err");
  if (msg) { dsToast("✅ " + msg, "", "ok"); params.delete("msg"); }
  if (err) { dsToast("❌ " + err, "", "err"); params.delete("err"); }
  if (msg || err) history.replaceState(null, "", location.pathname +
    (params.toString() ? "?" + params : ""));
});

/* ---------------- 复制到剪贴板 ---------------- */
function dsCopy(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    dsToast("已复制", text.length > 60 ? text.slice(0, 60) + "…" : text, "ok", 2500);
  });
}
