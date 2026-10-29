"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
let state,
  page = "overview",
  csrf = "",
  channels = [],
  categories = [],
  search = "",
  category = "",
  logId = null;
const titles = {
  overview: ["运行概览", "维护好入口，其余交给自动更新。"],
  sources: ["订阅入口", "集中维护采集入口，自动拉取、检测并筛选频道。"],
  channels: ["频道与线路", "查看检测结果，屏蔽失效线路或隐藏不需要的频道。"],
  jobs: ["任务记录", "追踪每一轮更新，查看结果与失败原因。"],
  settings: ["更新设置", "保存后在下一轮生效，不会打断正在进行的任务。"],
};
const names = {
  queued: "等待执行",
  running: "运行中",
  success: "已完成",
  failed: "失败",
  cancelled: "已停止",
  interrupted: "已中断",
  empty: "无可用结果",
  ok: "正常",
};
const phases = {
  prepare: "准备配置",
  fetch: "拉取订阅",
  subscribe: "拉取订阅",
  speed_test: "检测线路",
  aggregate: "合并频道",
  output: "生成列表",
  complete: "完成",
  schedule: "等待下次更新",
};
const date = (t) =>
  t
    ? new Date(t * 1000).toLocaleString("zh-CN", { hour12: false })
    : "尚未执行";
const duration = (j) =>
  j.started_at
    ? `${Math.max(0, Math.round(((j.finished_at || Date.now() / 1000) - j.started_at) / 60))} 分钟`
    : "—";
const tag = (s, label) =>
  `<span class="tag ${["ok", "success"].includes(s) ? "ok" : ["failed", "interrupted"].includes(s) ? "bad" : ["running", "queued"].includes(s) ? "busy" : s === "empty" ? "warn" : ""}">${esc(label || names[s] || s || "未检测")}</span>`;
function toast(text, error = false) {
  const n = $("#toast");
  n.textContent = text;
  n.classList.toggle("error", error);
  n.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (n.hidden = true), 5000);
}
async function api(path, options = {}) {
  const r = await fetch("/api/" + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
      ...options.headers,
    },
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "操作失败");
  return data;
}
const active = () =>
  state?.jobs.find((j) => ["running", "queued"].includes(j.status));
function jobTable(jobs) {
  return jobs.length
    ? `<div class="table-scroll"><table><thead><tr><th>开始时间</th><th>任务</th><th>状态</th><th>用时</th><th>结果</th><th></th></tr></thead><tbody>${jobs.map((j) => `<tr><td>${esc(date(j.created_at))}</td><td>${j.kind === "check" ? "测试入口" : "采集与检测"}</td><td>${tag(j.status)}</td><td>${duration(j)}</td><td>${esc(j.message || "等待执行")}${j.summary?.metrics ? `<div class="hint">通过 ${j.summary.metrics.valid || 0} / 检测 ${j.summary.metrics.completed || 0}</div>` : ""}</td><td><button class="small" data-action="log" data-id="${esc(j.id)}">查看日志</button></td></tr>`).join("")}</tbody></table></div>`
    : '<div class="empty">还没有任务记录。添加入口后即可开始更新。</div>';
}
function render() {
  if (!state) return;
  $("#page-title").textContent = titles[page][0];
  $("#page-description").textContent = titles[page][1];
  document
    .querySelectorAll(".nav")
    .forEach((n) => n.classList.toggle("active", n.dataset.page === page));
  $("#run-update").disabled = !!active();
  $("#run-update").textContent = active() ? "更新任务进行中" : "立即更新";
  let html = "";
  if (page === "overview") {
    const pub = state.publication,
      j = active(),
      enabled = state.sources.filter((s) => s.enabled).length;
    html = `<div class="stats"><div class="stat"><div class="stat-label">已发布频道</div><div class="stat-number">${pub?.channels || 0}</div><div class="stat-sub">电视当前可读取的频道</div></div><div class="stat"><div class="stat-label">已发布线路</div><div class="stat-number">${pub?.lines || 0}</div><div class="stat-sub">包含同频道备用线路</div></div><div class="stat"><div class="stat-label">启用入口</div><div class="stat-number">${enabled}<small class="muted"> / ${state.sources.length}</small></div><div class="stat-sub">支持 M3U · TXT</div></div><div class="stat"><div class="stat-label">自动更新</div><div class="stat-number">${state.settings.update_mode === "time" ? "定时" : esc(state.settings.update_interval) + '<small class="muted"> 小时</small>'}</div><div class="stat-sub">下次 ${esc(date(state.next_run))}</div></div></div><section class="panel"><div class="panel-head"><h2>当前任务</h2>${j ? tag(j.status) : tag("ok", "等待下一轮")}</div>${j ? `<div class="task-info"><strong>${esc(phases[j.phase] || "准备任务")}</strong><span class="muted">${j.total ? `${j.current} / ${j.total}` : "处理中…"}</span></div><progress max="${j.total || 100}" value="${j.current || 0}"></progress><div class="task-message">${esc(j.message || "正在准备配置快照…")}</div><div class="toolbar spaced"><button class="small" data-action="log" data-id="${esc(j.id)}">查看日志</button><button class="small danger" data-action="cancel" data-id="${esc(j.id)}" ${j.cancel ? "disabled" : ""}>${j.cancel ? "正在停止…" : "停止任务"}</button></div>` : `<p class="muted">${pub ? "正式列表已就绪，电视可以通过下方地址订阅。" : "还没有生成正式列表，请添加订阅入口并执行首次更新。"}</p><p class="hint">最近发布：${esc(date(pub?.published_at))}</p>`}</section><div class="grid-two"><section class="panel"><div class="panel-head"><h2>电视订阅地址</h2></div><div class="subscription"><span class="mono">${esc(state.subscription)}</span><button class="small" data-action="copy">复制</button></div><p class="hint">电视只需配置一次。更新失败或结果为空时，继续使用上一版列表。</p></section><section class="panel"><div class="panel-head"><h2>入口健康情况</h2><button class="link" data-page="sources">管理入口 →</button></div>${
      state.sources
        .slice(0, 4)
        .map(
          (s) =>
            `<div class="list-row"><div><strong>${esc(s.name)}</strong><div class="hint">${s.candidates ?? "—"} 条候选 · ${s.passed ?? "—"} 条通过</div></div>${s.enabled ? tag(s.status) : tag("disabled", "已停用")}</div>`,
        )
        .join("") || '<p class="muted">暂无入口，先添加一份公开订阅列表。</p>'
    }</section></div><section class="panel"><div class="panel-head"><h2>最近任务</h2><button class="link" data-page="jobs">全部记录 →</button></div>${jobTable(state.jobs.slice(0, 3))}</section>`;
  }
  if (page === "sources") {
    html = `<section class="panel"><div class="panel-head"><div><h2>订阅入口 <span class="muted">${state.sources.length}</span></h2><p class="hint">自动更新会读取所有启用的入口；测试入口只检查列表，不检测每条视频。</p></div><div class="toolbar"><button data-action="import">批量导入</button><button class="primary" data-action="add-source">＋ 添加入口</button></div></div>${state.sources.length ? `<div class="table-scroll"><table><thead><tr><th>名称 / 地址</th><th>启用</th><th>最近检查</th><th>候选 / 通过</th><th>状态</th><th>操作</th></tr></thead><tbody>${state.sources.map((s) => `<tr><td><div class="source-name">${esc(s.name)}</div><div class="source-url mono">${esc(s.url)}</div>${s.error ? `<div class="hint">${esc(s.error)}</div>` : ""}</td><td><button class="small" data-action="toggle-source" data-id="${esc(s.id)}">${s.enabled ? "已启用" : "已停用"}</button></td><td>${esc(date(s.checked_at))}</td><td>${s.candidates ?? "—"} / ${s.passed ?? "—"}</td><td>${tag(s.status)}${s.failures ? `<div class="hint">连续失败 ${s.failures} 次</div>` : ""}</td><td><div class="actions"><button class="small" data-action="edit-source" data-id="${esc(s.id)}">编辑</button><button class="small" data-action="check-source" data-id="${esc(s.id)}" ${active() ? "disabled" : ""}>测试入口</button><button class="small danger" data-action="delete-source" data-id="${esc(s.id)}">删除</button></div></td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">添加第一个入口，让系统开始自动采集。<br>支持粘贴多个订阅地址批量导入。</div>'}</section><p class="section-note">入口列表由你维护，播放地址由系统自动检测和筛选。暂时失败的入口不会自动删除，下轮更新会继续尝试。</p>`;
  }
  if (page === "channels") {
    const opened = new Set(
      [...document.querySelectorAll(".channel[open]")].map(
        (n) => n.dataset.key,
      ),
    );
    html = `<section class="panel"><div class="panel-head"><h2>频道检测结果 <span class="muted">${channels.length}</span></h2><div class="toolbar"><input id="channel-search" placeholder="搜索频道或分组" value="${esc(search)}" aria-label="搜索频道"><select id="channel-category" aria-label="筛选分组"><option value="">全部分组</option>${categories.map((c) => `<option ${category === c ? "selected" : ""} value="${esc(c)}">${esc(c)}</option>`).join("")}</select><button data-action="refresh-channels">刷新</button></div></div><p class="hint">显示最近一轮检测明细；发布数量以运行概览为准。隐藏或屏蔽会立即更新订阅，下轮检测也会保留规则。</p>${channels.map((c) => `<details class="channel" data-key="${esc(c.channel_key)}" ${opened.has(c.channel_key) ? "open" : ""}><summary><div><span class="channel-title">${esc(c.name)}</span><span class="hint">${esc(c.category)}</span></div><div>${c.hidden ? tag("disabled", "已隐藏") : tag(c.results.some((r) => r.valid && !r.blocked) ? "ok" : "failed", `${c.results.filter((r) => r.valid && !r.blocked).length} 条可用`)} <button class="small" data-action="hide-channel" data-id="${esc(c.channel_key)}">${c.hidden ? "恢复频道" : "隐藏频道"}</button></div></summary><div class="table-scroll"><table><thead><tr><th>线路地址</th><th>结果</th><th>速率 / 延迟</th><th>分辨率</th><th></th></tr></thead><tbody>${c.results.map((r, i) => `<tr><td class="mono">${esc(r.url)}${r.error_type ? `<div class="hint">${esc(r.error_type)}</div>` : ""}</td><td>${r.blocked ? tag("disabled", "已屏蔽") : tag(r.valid ? "ok" : "failed", r.valid ? "通过" : r.test_status || "未通过")}</td><td>${r.speed != null ? Number(r.speed).toFixed(2) + " MiB/s" : "—"}<div class="hint">${r.delay != null ? r.delay + " ms" : "—"}</div></td><td>${esc(r.resolution || "—")}</td><td><button class="small" data-action="block-url" data-id="${esc(c.channel_key)}" data-index="${i}">${r.blocked ? "恢复线路" : "屏蔽线路"}</button></td></tr>`).join("")}</tbody></table></div></details>`).join("") || '<div class="empty">没有符合条件的频道。完成一次更新后，检测明细会显示在这里。</div>'}</section>`;
  }
  if (page === "jobs")
    html = `<section class="panel"><div class="panel-head"><h2>任务历史</h2><span class="hint">自动保留最近 30 次任务与日志</span></div>${jobTable(state.jobs)}</section>`;
  if (page === "settings") {
    const s = state.settings;
    html = `<section class="panel settings-form"><div class="panel-head"><h2>自动更新与检测</h2></div><form id="settings-form"><div class="form-grid"><label>更新方式<select name="update_mode"><option value="interval" ${s.update_mode === "interval" ? "selected" : ""}>按间隔执行</option><option value="time" ${s.update_mode === "time" ? "selected" : ""}>每天固定时间</option></select></label><label>更新间隔（小时）<input name="update_interval" type="number" min="0.25" max="168" step="0.25" value="${s.update_interval}" required></label><label>固定时间（北京时间）<input name="update_times" value="${esc(s.update_times)}" placeholder="06:00,18:00" required></label><label>地址类型<select name="ipv_type">${["all", "ipv4", "ipv6"].map((v, i) => `<option value="${v}" ${s.ipv_type === v ? "selected" : ""}>${["IPv4 与 IPv6", "仅 IPv4", "仅 IPv6"][i]}</option>`).join("")}</select></label><label>检测并发<input name="speed_test_limit" type="number" min="1" max="16" value="${s.speed_test_limit}" required></label><label>单条检测超时（秒）<input name="speed_test_timeout" type="number" min="2" max="60" value="${s.speed_test_timeout}" required></label><label>每频道最多发布线路<input name="output_urls_limit" type="number" min="1" max="10" value="${s.output_urls_limit}" required></label><label>最低分辨率<input name="min_resolution" value="${esc(s.min_resolution)}" required></label><label>最低速率（MiB/s）<input name="min_speed" type="number" min="0" max="20" step="0.01" value="${s.min_speed}" required></label></div><label class="check"><input type="checkbox" name="open_filter_resolution" ${s.open_filter_resolution ? "checked" : ""}>按最低分辨率筛选</label><label class="check"><input type="checkbox" name="open_filter_speed" ${s.open_filter_speed ? "checked" : ""}>按最低速率筛选</label><label class="check"><input type="checkbox" name="update_startup" ${s.update_startup ? "checked" : ""}>服务启动时执行更新</label><p class="section-note">建议家庭路由器使用 3 个并发。新设置将在下一轮执行时使用，当前任务继续使用启动时的配置。</p><details><summary>频道目录（可选）</summary><label>参与采集和检测的频道<textarea name="channel_template" rows="12">${esc(s.channel_template || state.catalog || "")}</textarea></label><p class="hint">每行一个频道名称；分组使用“分组名,#genre#”。新增其他地区或语言的频道时，将名称加入这里。最多 2000 行。</p></details><button class="primary">保存设置</button></form></section>`;
  }
  const focused = document.activeElement?.id,
    position =
      focused === "channel-search"
        ? document.activeElement.selectionStart
        : null;
  $("#content").innerHTML = html;
  if (focused === "channel-search" && $("#channel-search")) {
    $("#channel-search").focus();
    $("#channel-search").setSelectionRange(position, position);
  }
}
async function refresh() {
  try {
    const old = state?.jobs[0]?.status,
      previous = JSON.stringify(state);
    state = await api("state");
    csrf = state.csrf;
    $("#connection").textContent = "● 已连接";
    if (
      page !== "settings" &&
      !document.querySelector("dialog[open]") &&
      previous !== JSON.stringify(state)
    )
      render();
    if (page === "channels" && old !== state.jobs[0]?.status)
      await loadChannels();
    if (logId && $("#log-dialog").open) {
      const r = await api(`jobs/${logId}/log`);
      $("#log-content").textContent = r.text || "任务准备中，暂无日志。";
    }
  } catch (e) {
    $("#connection").textContent = "连接中断，正在重试";
  }
}
async function loadChannels() {
  const r = await api("channels?" + new URLSearchParams({ search, category }));
  channels = r.channels;
  categories = r.categories;
  render();
}
async function navigate(value) {
  page = value;
  render();
  if (page === "channels") await loadChannels();
}
function editSource(s = { enabled: true }) {
  const f = $("#source-form");
  f.reset();
  f.elements.id.value = s.id || "";
  for (const k of ["name", "url", "user_agent"])
    f.elements[k].value = s[k] || "";
  f.elements.enabled.checked = !!s.enabled;
  $("#source-dialog-title").textContent = s.id
    ? "编辑订阅入口"
    : "添加订阅入口";
  $("#source-dialog").showModal();
}
document.addEventListener("click", async (event) => {
  const b = event.target.closest("button");
  if (!b) return;
  try {
    if (b.dataset.page) {
      await navigate(b.dataset.page);
      return;
    }
    const action = b.dataset.action,
      id = b.dataset.id;
    if (!action) return;
    if (action === "close-dialog") {
      b.closest("dialog").close();
      logId = null;
      return;
    }
    if (action === "run") {
      await api("jobs", {
        method: "POST",
        body: JSON.stringify({ kind: "update" }),
      });
      toast("更新任务已加入队列");
    }
    if (action === "cancel") {
      await api(`jobs/${id}/cancel`, { method: "POST", body: "{}" });
      toast("正在停止任务，当前订阅保持不变");
    }
    if (action === "add-source") editSource();
    if (action === "edit-source")
      editSource(state.sources.find((s) => s.id === id));
    if (action === "toggle-source") {
      const s = state.sources.find((s) => s.id === id);
      await api(`sources/${id}`, {
        method: "PUT",
        body: JSON.stringify({ ...s, enabled: !s.enabled }),
      });
    }
    if (
      action === "delete-source" &&
      confirm("删除这个采集入口？已经发布的频道将在下次成功更新后调整。")
    )
      await api(`sources/${id}`, { method: "DELETE", body: "{}" });
    if (action === "check-source") {
      await api("jobs", {
        method: "POST",
        body: JSON.stringify({ kind: "check", source_id: id }),
      });
      toast("已开始检查入口的连接与列表格式");
    }
    if (action === "import") {
      $("#import-result").hidden = true;
      $("#import-dialog").showModal();
    }
    if (action === "copy") {
      try {
        await navigator.clipboard.writeText(state.subscription);
        toast("订阅地址已复制");
      } catch (_) {
        const input = document.createElement("textarea");
        input.value = state.subscription;
        document.body.append(input);
        input.select();
        document.execCommand("copy");
        input.remove();
        toast("订阅地址已复制");
      }
    }
    if (action === "log") {
      logId = id;
      $("#log-content").textContent = "正在读取日志…";
      $("#log-dialog").showModal();
    }
    if (action === "refresh-channels") await loadChannels();
    if (action === "hide-channel") {
      event.preventDefault();
      const c = channels.find((c) => c.channel_key === id);
      await api("rules", {
        method: "PUT",
        body: JSON.stringify({
          kind: "channel",
          target: id,
          enabled: !c.hidden,
        }),
      });
      await loadChannels();
      toast("频道规则已保存，订阅已更新");
    }
    if (action === "block-url") {
      const r = channels.find((c) => c.channel_key === id).results[
        Number(b.dataset.index)
      ];
      await api("rules", {
        method: "PUT",
        body: JSON.stringify({
          kind: "url",
          target: r.url,
          enabled: !r.blocked,
        }),
      });
      await loadChannels();
      toast("线路规则已保存，订阅已更新");
    }
    await refresh();
  } catch (e) {
    toast(e.message, true);
  }
});
document.addEventListener("submit", async (event) => {
  event.preventDefault();
  const f = event.target;
  try {
    if (f.getAttribute("id") === "source-form") {
      const data = Object.fromEntries(new FormData(f));
      data.enabled = f.elements.enabled.checked;
      const id = data.id;
      delete data.id;
      await api(id ? `sources/${id}` : "sources", {
        method: id ? "PUT" : "POST",
        body: JSON.stringify(data),
      });
      $("#source-dialog").close();
      toast("入口已保存，下轮更新自动使用");
    }
    if (f.getAttribute("id") === "import-form") {
      const r = await api("sources/import", {
        method: "POST",
        body: JSON.stringify({ text: f.elements.text.value }),
      });
      $("#import-result").textContent =
        `新增 ${r.added} 个入口\n` +
        r.errors.map((e) => `第 ${e.line} 行：${e.message}`).join("\n");
      $("#import-result").hidden = false;
      toast(`已导入 ${r.added} 个入口`);
    }
    if (f.getAttribute("id") === "settings-form") {
      const data = { ...state.settings };
      for (const el of f.elements) {
        if (!el.name) continue;
        data[el.name] =
          el.type === "checkbox"
            ? el.checked
            : el.type === "number"
              ? el.valueAsNumber
              : el.value;
      }
      await api("settings", { method: "PUT", body: JSON.stringify(data) });
      toast("设置已保存，将在下一轮生效");
    }
    await refresh();
  } catch (e) {
    toast(e.message, true);
  }
});
document.addEventListener("change", async (event) => {
  if (event.target.id === "channel-category") {
    category = event.target.value;
    await loadChannels();
  }
});
document.addEventListener("input", (event) => {
  if (event.target.id === "channel-search") {
    search = event.target.value;
    clearTimeout(window.searchTimer);
    window.searchTimer = setTimeout(
      () => loadChannels().catch((e) => toast(e.message, true)),
      400,
    );
  }
});
refresh().then(render);
setInterval(refresh, 3000);
