"use strict";
window.SourceDiscovery = {
  render(state, esc, date, tag) {
    const d = state.discovery,
      s = d.config,
      last = d.last;
    const job = state.jobs.find((j) =>
      ["running", "queued"].includes(j.status),
    );
    const checks = (name, choices, selected) =>
      Object.entries(choices)
        .map(
          ([key, value]) =>
            `<label class="check"><input type="checkbox" name="${name}" value="${esc(key)}" ${selected.includes(key) ? "checked" : ""}>${esc(typeof value === "string" ? value : value.name)}</label>`,
        )
        .join("");
    const numbers = [
      ["interval", "发现间隔（小时）", 1, 168],
      ["probe_budget", "每轮最多检测线路", 1, 100],
      ["new_channel_limit", "每轮最多新增频道", 1, 30],
      ["max_channels", "自动频道总量上限", 1, 300],
      ["probe_timeout", "单条检测超时（秒）", 2, 15],
    ];
    return `<div class="stats"><div class="stat"><div class="stat-label">跟踪的订阅入口</div><div class="stat-number">${d.sources.length}</div><div class="stat-sub">可信目录自动发现，按内容去重</div></div><div class="stat"><div class="stat-label">检测通过的自动频道</div><div class="stat-number">${d.counts.valid}</div><div class="stat-sub">隐藏或停用后不会发布</div></div><div class="stat"><div class="stat-label">候选线路记录</div><div class="stat-number">${d.counts.candidates}</div><div class="stat-sub">每个频道最多保留两条候选</div></div><div class="stat"><div class="stat-label">冷却中的线路</div><div class="stat-number">${d.counts.cooling}</div><div class="stat-sub">连续失败三次后延后重试</div></div></div>
      <section class="panel"><div class="panel-head"><div><h2>自动发现新的入口与频道</h2><p class="hint">${s.enabled ? `每 ${s.interval} 小时发现一次 · 下次 ${esc(date(d.next_run))}` : "自动发现未开启，可手动运行"}</p></div><button class="primary" data-action="discover" ${job ? "disabled" : ""}>立即发现</button></div><p class="muted">从 IPTV-org、Free-TV 和 APTV 推荐页收集公开订阅。检测通过的新频道自动进入原订阅地址，视频仍由电视直接播放。</p>${job?.kind === "discover" ? `<div class="task-info"><strong>正在发现与检测</strong><span>${job.current} / ${job.total || "…"}</span></div><progress max="${job.total || 100}" value="${job.current || 0}"></progress><p class="hint">${esc(job.message || "正在读取目录…")}</p><button class="small danger" data-action="cancel" data-id="${esc(job.id)}">停止任务</button>` : `<p class="hint">最近完成：${esc(date(last?.finished_at))}${last ? ` · 检测 ${last.checked} 条 · 新增 ${last.added_channels} 个频道` : ""}</p>`}${last?.provider_errors?.length ? `<p class="hint">${last.provider_errors.map((e) => `${esc(d.providers[e.provider]?.name || e.provider)}：${esc(e.error)}`).join("；")}</p>` : ""}
      <details id="discovery-config"><summary>发现范围与资源预算</summary><form id="discovery-form"><label class="check"><input type="checkbox" name="enabled" ${s.enabled ? "checked" : ""}>开启定期自动发现</label><label class="check"><input type="checkbox" name="auto_import" ${s.auto_import ? "checked" : ""}>自动导入可信入口，陌生来源先保留为候选</label><h3>资源目录</h3><div class="form-grid">${checks("providers", d.providers, s.providers)}</div><h3>地区</h3><div class="form-grid">${checks("countries", d.countries, s.countries)}</div><h3>语言</h3><div class="form-grid">${checks("languages", d.languages, s.languages)}</div><div class="form-grid">${numbers.map(([key, label, min, max]) => `<label>${label}<input type="number" name="${key}" min="${min}" max="${max}" value="${s[key]}" required></label>`).join("")}</div><p class="hint">发现任务与完整更新串行运行。只检测新线路或到期线路；已通过结果每 12 小时到期，连续失败 3 次冷却 24 小时。不开定时仍保留已发现频道。</p><button class="primary">保存发现设置</button></form></details></section>
      <section class="panel"><div class="panel-head"><h2>发现的入口</h2><button class="link" data-page="sources">管理全部入口 →</button></div>${d.sources.length ? `<div class="table-scroll"><table><thead><tr><th>入口 / 来源</th><th>列表记录 / 自动线路通过</th><th>检查结果</th><th>导入状态</th></tr></thead><tbody>${d.sources.map((r) => `<tr><td><div class="source-name">${esc(r.name)}</div><div class="source-url mono">${esc(r.url)}</div><div class="hint">来自 ${esc(d.providers[r.provider]?.name || r.provider)}</div></td><td>${r.candidates || 0} / ${r.passed || 0}</td><td>${tag(r.status, r.status === "duplicate" ? "重复列表" : undefined)}<div class="hint">${esc(r.error || "")}</div><div class="hint">${esc(date(r.checked_at))}</div></td><td>${r.removed ? "已手动删除，不会自动恢复" : r.source_id ? (r.enabled ? "已导入并启用" : "已停用，不会自动启用") : r.status === "ok" ? `<button class="small" data-action="import-discovered" data-url="${esc(r.url)}">导入这个入口</button>` : "未导入"}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">运行一次发现后，这里会显示入口及其检查结果。</div>'}</section>
      <section class="panel"><div class="panel-head"><h2>自动频道检测记录</h2><button class="link" data-page="channels">查看线路与屏蔽规则 →</button></div>${d.channels.length ? `<div class="table-scroll"><table><thead><tr><th>频道</th><th>分组 / 来源</th><th>状态</th><th>最近检测</th></tr></thead><tbody>${d.channels.map((r) => `<tr><td>${esc(r.name)}<div class="hint">${esc(r.resolution || "")}</div></td><td>${esc(r.category)}<div class="hint">${esc(d.providers[r.provider]?.name || r.provider)}</div></td><td>${tag(r.valid ? "ok" : r.failures >= 3 ? "empty" : "", r.valid ? "通过" : r.failures >= 3 ? "冷却中" : r.checked_at ? "等待重试" : "等待检测")}<div class="hint">${esc(r.error)}</div>${r.failures ? `<div class="hint">连续失败 ${r.failures} 次 · 下次 ${esc(date(r.next_check))}</div>` : ""}</td><td>${esc(date(r.checked_at))}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">选定的地区与语言中，目录之外的新频道会进入这里。通过后自动发布。</div>'}<p class="hint">最多展示 200 条记录。手工频道目录保持不变；自动频道使用独立的“发现·地区 / 语言”分组。</p></section>`;
  },
};
