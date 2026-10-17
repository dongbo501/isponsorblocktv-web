"use strict";
const $ = (q, root = document) => root.querySelector(q);
const $$ = (q, root = document) => [...root.querySelectorAll(q)];
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const paths = {
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  tv: "M4 4h16a1 1 0 0 1 1 1v12H3V5a1 1 0 0 1 1-1z M8 21h8 M12 17v4",
  sliders:
    "M4 4v5 M4 15v5 M12 4v9 M12 19v1 M20 4v1 M20 11v9 M1 9h6v6H1z M9 13h6v6H9z M17 5h6v6h-6z",
  heart:
    "M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8z",
  logs: "M5 3h14v18H5z M9 7h6 M9 11h6 M9 15h3",
  chart: "M4 3v18h17 M8 16v-5 M13 16V6 M18 16V9",
  settings:
    "M12 3v3 M12 18v3 M3 12h3 M18 12h3 M5.6 5.6l2.1 2.1 M16.3 16.3l2.1 2.1 M5.6 18.4l2.1-2.1 M16.3 7.7l2.1-2.1 M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  plus: "M12 5v14 M5 12h14",
  play: "m8 5 11 7-11 7z",
  refresh: "M20 7V3l-3 3a8 8 0 1 0 3 10 M20 7h-5",
  check: "m5 12 4 4L19 6",
  clock: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0 M12 7v5l3 2",
  shield: "M12 3 3 7v5c0 5 9 9 9 9s9-4 9-9V7z m-4 9 3 3 5-6",
  download: "M12 3v12 m-5-5 5 5 5-5 M4 17v4h16v-4",
  search: "M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0 m-2 5 6 6",
};
const icon = (name) =>
  `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.grid}"/></svg>`;
const nav = [
  ["overview", "总览", "grid"],
  ["devices", "电视设备", "tv"],
  ["statistics", "跳过统计", "chart"],
  ["rules", "跳过规则", "sliders"],
  ["channels", "频道白名单", "heart"],
  ["logs", "运行日志", "logs"],
  ["settings", "高级设置", "settings"],
];
const S = {
  csrf: null,
  config: null,
  revision: null,
  keys: {},
  panel: {},
  meta: {},
  status: { state: "waiting", devices: {} },
  statistics: null,
  statisticsStale: false,
  page: "overview",
  dirty: false,
  logs: [],
  paused: false,
  authenticated: false,
};
const states = {
  running: ["服务运行中", "good"],
  waiting: ["等待添加设备", "warn"],
  stopped: ["服务已暂停", "warn"],
  starting: ["正在启动", "warn"],
  restarting: ["正在重试", "bad"],
};
let toastTimer,
  polling = false;
function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = error ? "error" : "";
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.hidden = true), error ? 7000 : 3500);
}
async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(S.csrf ? { "X-CSRF-Token": S.csrf } : {}),
    ...options.headers,
  };
  let response;
  try {
    response = await fetch(`/api${path}`, { ...options, headers });
  } catch {
    throw new Error("无法连接管理面板，请检查网络或容器是否运行。");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && S.authenticated) {
      S.authenticated = false;
      await showAuth();
    }
    const details = data.errors
      ?.map((e) => `${e.field}: ${e.message}`)
      .join("\n");
    throw new Error(
      (typeof data.detail === "string" ? data.detail : "操作失败") +
        (details ? `\n${details}` : ""),
    );
  }
  return data;
}
const post = (path, body) =>
  api(path, { method: "POST", body: JSON.stringify(body) });
const put = (path, body) =>
  api(path, { method: "PUT", body: JSON.stringify(body) });
function adopt(data) {
  S.config = data.config;
  S.revision = data.revision;
  S.keys = data.device_keys;
}
async function save(config) {
  adopt(await put("/config", { revision: S.revision, config }));
  S.dirty = false;
  S.status = await api("/status");
  await refreshStatistics();
  render();
  toast("配置已保存并应用");
}
function copyConfig() {
  return structuredClone(S.config);
}
function badge() {
  const [text, cls] = states[S.status.state] || ["状态未知", "warn"];
  return `<span class="badge ${cls}">${text}</span>`;
}
function uptime() {
  const n = S.status.uptime || 0;
  return n < 60
    ? `${n} 秒`
    : n < 3600
      ? `${Math.floor(n / 60)} 分钟`
      : `${Math.floor(n / 3600)} 小时 ${Math.floor((n % 3600) / 60)} 分钟`;
}
function heading(title, subtitle, buttons = "") {
  return `<div class="page-heading"><div><h1>${title}</h1><p class="subline">${subtitle}</p></div><div class="actions">${buttons}</div></div>`;
}
function button(label, action, ic = "", cls = "") {
  return `<button class="btn ${cls}" data-action="${action}">${ic ? icon(ic) : ""}${label}</button>`;
}
function tvArt() {
  return `<div class="hero-art" aria-hidden="true"><div class="tv-art"><span class="play-art">▶</span></div><span class="art-badge">✓ 让观看更专注</span></div>`;
}
function switchRow(name, title, desc, checked) {
  return `<div class="switch-row"><div><strong>${title}</strong><p>${desc}</p></div><label class="switch"><input type="checkbox" name="${name}" aria-label="${title}" ${checked ? "checked" : ""}><i></i></label></div>`;
}
function field(name, label, value = "", hint = "", type = "text", extra = "") {
  return `<label class="field"><span>${label}</span><input type="${type}" name="${name}" value="${esc(value)}" ${extra}>${hint ? `<small>${hint}</small>` : ""}</label>`;
}
function empty(title, text, buttons = "") {
  return `<div class="empty"><div class="device-icon">${icon("tv")}</div><h3>${title}</h3><p>${text}</p>${buttons}</div>`;
}
function errorIn(form, error) {
  let node = $(".inline-error", form);
  if (!node) {
    node = document.createElement("div");
    node.className = "inline-error";
    node.setAttribute("role", "alert");
    form.append(node);
  }
  node.textContent = error.message;
}
function modal(title, body) {
  $("#modal-content").innerHTML =
    `<div class="modal-head"><h2>${title}</h2><button class="quiet" data-action="close-modal" aria-label="关闭弹窗">✕</button></div><div class="modal-body">${body}</div>`;
  if (!$("#modal").open) $("#modal").showModal();
}
function closeModal() {
  $("#modal").close();
}

async function showAuth() {
  $("#shell").hidden = true;
  closeModal();
  const state = await api("/auth/status");
  if (state.authenticated) {
    S.csrf = state.csrf;
    await enter();
    return;
  }
  $("#boot").hidden = true;
  $("#auth").hidden = false;
  const setup = state.setup_required;
  $("#auth").innerHTML =
    `<section class="auth-story"><a class="brand" href="/"><span class="brand-icon">▶</span><span>iSponsorBlock<span class="brand-sub">TV · 中文管理面板</span></span></a><div><div class="eyebrow">A LITTLE LESS NOISE. A LOT MORE VIDEO.</div><h1>少些打扰，<br>多些精彩。</h1><p>把复杂的设置留给我们。<br>连接你的电视，让每一次观看都更专注。</p></div>${tvArt()}<small>开源 · 单容器 · 为你的电视而来</small></section><section class="auth-form-wrap"><form id="auth-form" class="auth-form"><div class="eyebrow">YOUR TV, YOUR CONTROL</div><h2>${setup ? "开始你的清爽体验" : "欢迎回来"}</h2><p>${setup ? "首次使用，请先设置管理密码。之后所有设备和规则都可以在这里管理。" : "登录管理面板，查看设备状态和观看设置。"}</p>${field("password", setup ? "设置管理密码" : "管理密码", "", "", "password", `required ${setup ? 'minlength="10"' : ""} maxlength="128" autocomplete="${setup ? "new-password" : "current-password"}" placeholder="${setup ? "至少 10 个字符" : "输入管理密码"}"`)}${setup ? field("confirm", "确认密码", "", "", "password", 'required minlength="10" maxlength="128" autocomplete="new-password" placeholder="再次输入密码"') : ""}<button class="btn primary" type="submit">${setup ? "创建密码，进入面板" : "登录管理面板"} →</button><div class="auth-note">${setup ? "请在可信任的局域网内完成首次设置。" : "管理密码由你的服务器保存。"}<br>无需注册账号，无需第三方管理服务。</div></form></section>`;
  $("#auth-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget,
      submit = $("button", form);
    submit.disabled = true;
    try {
      const password = form.elements.password.value;
      if (setup && password !== form.elements.confirm.value)
        throw new Error("两次输入的密码不一致");
      const data = await post(setup ? "/auth/setup" : "/auth/login", {
        password,
      });
      S.csrf = data.csrf;
      await enter();
    } catch (error) {
      errorIn(form, error);
    } finally {
      submit.disabled = false;
    }
  });
}
async function enter() {
  const [config, meta, panel, status] = await Promise.all([
    api("/config"),
    api("/meta"),
    api("/panel"),
    api("/status"),
  ]);
  adopt(config);
  S.meta = meta;
  S.panel = panel;
  S.status = status;
  S.statistics = null;
  await refreshStatistics();
  S.authenticated = true;
  $("#boot").hidden = true;
  $("#auth").hidden = true;
  $("#auth").replaceChildren();
  $("#shell").hidden = false;
  $("#nav").innerHTML = nav
    .map(
      ([id, title, ic]) =>
        `<a href="#${id}" data-page="${id}">${icon(ic)}${title}</a>`,
    )
    .join("");
  $("#footer-version").textContent = `v${meta.version}`;
  const initial = location.hash.slice(1);
  S.page = nav.some((n) => n[0] === initial) ? initial : "overview";
  render();
  await refresh();
}
function render() {
  $("#breadcrumb").textContent =
    nav.find((n) => n[0] === S.page)?.[1] || "总览";
  $$("#nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.page === S.page);
    if (a.dataset.page === S.page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const pages = {
    overview: overviewPage,
    devices: devicesPage,
    statistics: statisticsPage,
    rules: rulesPage,
    channels: channelsPage,
    logs: logsPage,
    settings: settingsPage,
  };
  $("#page").innerHTML = pages[S.page]();
  updateLive();
  bindForms();
}
function overviewPage() {
  return (
    heading(
      "每一次观看，都更专注。",
      "在这里管理你的电视，打造少些打扰的观看体验。",
      button("添加设备", "add-device", "plus", "primary"),
    ) +
    `<section class="hero"><div class="hero-copy"><div class="eyebrow">YOUR SCREEN. YOUR MOMENT.</div><h2>精彩继续，打扰跳过。</h2><p>自动跳过视频中的赞助片段，静音 YouTube 广告。<br>设置一次，让你喜欢的内容自然接续。</p><div class="actions">${button("管理跳过规则", "go-rules", "sliders", "lime")}${button("查看设备", "go-devices", "", "")}</div></div>${tvArt()}</section>
  <div class="stats"><div class="stat"><div class="stat-label">后台服务 ${icon("shield")}</div><div class="stat-value state-value" id="stat-state"></div><div class="stat-note" id="stat-uptime"></div></div><div class="stat"><div class="stat-label">在线设备 ${icon("tv")}</div><div class="stat-value" id="stat-devices"></div><div class="stat-note">共 ${S.config.devices.length} 台已配置设备</div></div><div class="stat"><div class="stat-label">启用的跳过类别 ${icon("sliders")}</div><div class="stat-value">${S.config.skip_categories.length}<small> / 10</small></div><div class="stat-note">按你的喜好自动跳过</div></div><div class="stat"><div class="stat-label">累计跳过 ${icon("chart")}</div><div class="stat-value" id="stat-skips">0</div><div class="stat-note"><a href="#statistics" data-page="statistics">查看全局与设备明细 →</a></div></div></div>
  <div class="grid-two"><section><div class="section-head"><div><h2>我的电视 <small>(${S.config.devices.length})</small></h2><p>连接状态每 5 秒自动刷新</p></div><button class="quiet" data-action="go-devices">管理设备 →</button></div><div id="device-list"></div><div class="recent"><div class="section-head"><h2>最近动态</h2><button class="quiet" data-action="go-logs">全部日志 →</button></div><div class="card" id="recent-logs"></div></div></section><aside class="card flat"><div class="section-head"><h2>三步，开始观看</h2><span class="badge">快速入门</span></div><div class="steps"><div class="step"><span class="step-num">01</span><div><strong>打开电视上的 YouTube</strong><p>进入「设置」→「使用电视代码关联」，找到 12 位数字配对码。</p></div></div><div class="step"><span class="step-num">02</span><div><strong>在这里添加你的电视</strong><p>输入配对码即可连接，无需 SSH，也不用手动编辑配置文件。</p></div></div><div class="step"><span class="step-num">03</span><div><strong>选择规则，安心观看</strong><p>保存规则后自动生效。容器保持运行即可，管理页面可以关闭。</p></div></div></div><div class="hint spacer-top">片段数据来自 SponsorBlock 社区。没有社区标记的视频不会跳过；原生广告会在允许跳过时自动跳过。</div></aside></div>`
  );
}
function devicesPage() {
  return (
    heading(
      "电视设备",
      "一处连接家里的每一块屏幕。",
      button("局域网搜索", "discover-device", "search") +
        button("添加设备", "add-device", "plus", "primary"),
    ) +
    `<div id="device-list"></div><div class="hint spacer-top">电视可以与容器位于不同网络，只要容器能够访问 YouTube。局域网搜索需要处于同一网络，Linux Docker 推荐使用 host 网络。配对码方式无需 host 网络。</div>`
  );
}
const countText = (value) => Number(value || 0).toLocaleString("zh-CN");
function statisticsPage() {
  return heading("跳过统计", "原生广告与 10 类 SponsorBlock 片段，按设备累计。") +
    '<p id="statistics-error" class="hint warning" role="status" hidden>统计刷新失败；如有已读取的数据，将保留显示。连接恢复后会自动更新。</p><div id="skip-statistics"></div>';
}
function statisticsContent() {
  const data = S.statistics;
  if (!data) return `<p class="hint">${S.statisticsStale ? "暂时无法读取跳过统计，请稍后重试。" : "正在读取跳过统计…"}</p>`;
  const categories = data.categories.filter((category) =>
    category.id !== "unknown" || data.global.counts.unknown > 0,
  );
  const breakdown = (counts) => `<dl class="skip-breakdown">${categories.map((category) =>
    `<div><dt>${esc(category.name)}</dt><dd>${countText(counts[category.id])}<small> 次</small></dd></div>`,
  ).join("")}</dl>`;
  const since = new Date(data.since).toLocaleString("zh-CN", { hour12: false });
  return `<section class="card skip-global"><div class="section-head"><div><h2>全局汇总</h2><p>所有设备的累计跳过次数，包含已移除设备的历史记录</p></div><span class="badge">每 5 秒刷新</span></div><div class="skip-total"><span>累计跳过总次数</span><strong>${countText(data.global.total)}<small> 次</small></strong></div>${breakdown(data.global.counts)}</section>
    <div class="section-head"><div><h2>单设备统计 <small>(${data.devices.length})</small></h2><p>设备离线或服务暂停后，仍保留累计次数</p></div></div>
    ${data.devices.length ? `<div class="skip-devices">${data.devices.map((device) => `<article class="card"><div class="section-head"><div class="skip-device-name"><h3>${esc(device.name)}</h3>${device.configured ? "" : '<span class="badge">已移除 · 历史记录</span>'}</div>${icon("tv")}</div><div class="skip-total"><span>该设备跳过总次数</span><strong>${countText(device.total)}<small> 次</small></strong></div>${breakdown(device.counts)}</article>`).join("")}</div>` : `<section class="card">${empty("还没有设备统计", "添加电视后会显示各类别次数，首次跳过前均为 0。", button("添加设备", "add-device", "plus", "primary"))}</section>`}
    <div class="hint">统计起始时间：${esc(since)}。仅累计本功能启用后的记录，数据随数据卷保存，重启后保留。<br>原生广告按成功发送的跳过指令计数，同一广告的重复通知只计一次；SponsorBlock 按成功跳过的片段计数，合并跳过时各片段分别计入所属类别。总次数为各类别之和，静音不计入。关闭「回报跳过次数」不影响本地统计。</div>`;
}
function deviceList() {
  if (!S.config.devices.length)
    return empty(
      "连接你的第一台电视",
      "打开电视 YouTube 的「使用电视代码关联」，<br>输入 12 位数字即可开始。",
      button("添加设备", "add-device", "plus", "primary"),
    );
  return `<div class="device-grid">${S.config.devices
    .map((d, i) => {
      const state = S.status.devices[S.keys[d.screen_id]] || {},
        connected = state.connected,
        total = S.statistics ? countText(S.statistics.devices.find((device) => device.key === S.keys[d.screen_id])?.total) : "—";
      const label =
        S.status.state === "stopped"
          ? "服务已暂停"
          : connected
            ? state.playing
              ? "正在播放"
              : "已连接"
            : "等待电视上线";
      return `<article class="device"><div class="device-top"><span class="device-icon">${icon("tv")}</span><div class="device-info"><h3>${esc(d.name)}</h3><p>YouTube on TV</p></div><span class="badge ${connected ? "good" : ""}">${label}</span></div><div class="device-body"><span class="muted">${connected && state.video_id ? "当前视频" : "连接状态"}</span><span>${connected && state.video_id ? esc(state.video_id) : connected ? "已连接 YouTube" : "等待电视打开 YouTube"}</span></div><div class="device-body"><span class="muted">累计跳过</span><a href="#statistics" data-page="statistics">${total} 次 →</a></div><div class="device-bottom"><span>时间偏移 ${d.offset} ms</span><div><button class="quiet" data-action="edit-device" data-index="${i}">编辑</button><button class="quiet" data-action="delete-device" data-index="${i}">移除</button></div></div></article>`;
    })
    .join("")}</div>`;
}
function rulesPage() {
  const c = S.config;
  return (
    heading("跳过规则", "把时间留给真正想看的内容。") +
    `<form id="rules-form"><section class="card"><div class="section-head"><div><h2>视频片段</h2><p>根据 SponsorBlock 社区标记自动跳过所选类别</p></div><div class="actions"><button class="quiet" type="button" data-action="categories-all">全选</button><button class="quiet" type="button" data-action="categories-none">清空</button></div></div><div class="category-grid">${S.meta.categories.map((cat) => `<label class="category"><input type="checkbox" name="category" value="${cat.id}" ${c.skip_categories.includes(cat.id) ? "checked" : ""}><div><strong>${cat.name}<code>${cat.id}</code></strong><p>${cat.description}</p></div></label>`).join("")}</div><div class="form-grid spacer-top">${field("minimum_skip_length", "最短跳过长度（秒）", c.minimum_skip_length, "仅跳过长于此值的片段；填 0 表示不设最短长度。", "number", 'min="0" max="3600" step="0.1" required')}</div>${switchRow("skip_count_tracking", "回报跳过次数", "向 SponsorBlock 回报跳过的片段 UUID，帮助社区贡献者了解使用情况；关闭不影响本地跳过统计。", c.skip_count_tracking)}</section><section class="card"><h2>YouTube 广告与播放</h2>${switchRow("mute_ads", "广告自动静音", "在原生广告播放时静音，回到视频内容后恢复音量。", c.mute_ads)}${switchRow("skip_ads", "自动点击「跳过广告」", "在 YouTube 允许跳过广告的第一时间自动点击。", c.skip_ads)}${switchRow("auto_play", "自动播放下一条", "保持 YouTube 的自动播放功能开启。", c.auto_play)}<div class="hint spacer-top">原生广告无法保证完全移除。Shorts 和儿童资料存在上游兼容性限制，使用 AirPlay 外接音响时广告静音可能无效。</div><div class="save-row"><small>保存后自动应用到所有设备</small><button class="btn primary" type="submit">保存并应用</button></div></section></form>`
  );
}
function channelsPage() {
  return (
    heading(
      "频道白名单",
      "支持喜欢的创作者，为他们保留完整视频。",
      button("添加频道", "add-channel", "plus", "primary"),
    ) +
    `<div class="hint ${S.config.apikey ? "" : "warning"}">${S.config.apikey ? "已设置 YouTube API 密钥。" : "需要先在高级设置中填写 YouTube Data API v3 密钥。"} 白名单仅停止该频道的 SponsorBlock 片段跳过，原生广告的静音与跳过仍由全局规则控制。</div><section class="card spacer-top">${S.config.channel_whitelist.length ? `<div class="table-wrap"><table><thead><tr><th>频道名称</th><th>频道 ID</th><th>操作</th></tr></thead><tbody>${S.config.channel_whitelist.map((c, i) => `<tr><td>${esc(c.name)}</td><td><code>${esc(c.id)}</code></td><td><button class="quiet" data-action="delete-channel" data-index="${i}">移除</button></td></tr>`).join("")}</tbody></table></div>` : empty("还没有白名单频道", "添加你想完整观看的频道。", button("添加频道", "add-channel", "plus"))}</section>`
  );
}
function logsPage() {
  return (
    heading(
      "运行日志",
      "实时查看连接、跳过与后台运行情况。",
      `<a class="btn" href="/api/logs/export">${icon("download")}导出日志</a>`,
    ) +
    `<section class="card"><div class="log-toolbar"><div class="actions"><span class="badge good" id="log-live">实时刷新</span><label>级别 <select id="log-level" aria-label="日志级别"><option value="ALL">全部</option><option value="INFO">信息</option><option value="WARNING">警告</option><option value="ERROR">错误</option><option value="DEBUG">调试</option></select></label></div><button class="btn sm" data-action="pause-logs">${S.paused ? "继续刷新" : "暂停刷新"}</button></div><div class="logs" id="logs-list" role="log" aria-label="服务日志"></div><p class="subline spacer-top">显示本次面板启动后最近 1,000 条日志；导出可获取当前日志文件。磁盘日志自动轮转，上游原始日志保留英文。</p></section>`
  );
}
function settingsPage() {
  const c = S.config,
    p = S.panel;
  return (
    heading("高级设置", "网络连接、服务管理，以及你的配置备份。") +
    `<div class="narrow"><form id="advanced-form"><section class="card"><div class="section-head"><h2>连接与接口</h2></div><div class="form-grid">${field("join_name", "电视上显示的连接名称", c.join_name, "作为遥控客户端连接到电视时显示的名称。", "text", 'required maxlength="100"')}${field("sponsorblock_api_url", "SponsorBlock API 地址", c.sponsorblock_api_url, "支持自建兼容服务，默认使用官方 API。", "url", 'required maxlength="2048"')}${field("apikey", "YouTube Data API v3 密钥", c.apikey, "仅频道白名单与频道搜索需要；其他功能无需密钥。", "password", 'maxlength="512" autocomplete="off"')}</div>${switchRow("use_proxy", "通过代理访问网络", "启用后使用下方已保存的代理地址；局域网地址应保持直连。", c.use_proxy)}<div class="save-row"><button class="btn primary" type="submit">保存连接设置</button></div></section></form><form id="panel-form"><section class="card"><h2>代理与诊断</h2><div class="spacer-top">${field("proxy_url", "HTTP(S) 代理地址", p.proxy_url, "例如 http://192.168.1.10:7890；Docker 中的 127.0.0.1 指容器自身。先保存代理，再打开上方代理开关。", "password", 'maxlength="2048" placeholder="http://192.168.1.10:7890" autocomplete="off"')}</div>${switchRow("debug", "详细日志", "记录上游连接与播放事件，仅在排查问题时开启。", p.debug)}${switchRow("http_tracing", "HTTP 请求诊断", "记录请求方法、地址路径与响应状态；省略响应正文及查询参数。", p.http_tracing)}<div class="save-row"><button class="btn primary" type="submit">保存代理与诊断</button></div></section></form><section class="card"><div class="section-head"><h2>后台服务</h2><span id="service-state"></span></div><p class="subline">暂停后将停止所有电视上的自动操作，管理面板仍可访问。暂停状态会在容器重启后保留。</p><div class="actions spacer-top">${button("启动服务", "service-start", "play")}${button("重新启动", "service-restart", "refresh")}${button("暂停服务", "service-stop", "", "danger")}</div></section><section class="card"><h2>备份与恢复</h2><p class="subline spacer-top">导出配置兼容原版 iSponsorBlockTV，包含电视凭据及 YouTube API 密钥，请妥善保管。跳过统计、代理、诊断设置和管理密码保存在数据卷中，不包含在此配置文件内。</p><div class="actions spacer-top"><a class="btn" href="/api/config/export">${icon("download")}导出配置</a>${button("导入配置", "import-config", "", "")}</div></section><section class="card"><div class="section-head"><h2>访问安全</h2>${button("修改管理密码", "change-password")}</div><p class="subline">修改密码会使其他已登录会话失效。需要远程访问时，建议通过 HTTPS 反向代理或 VPN 连接。</p></section><section class="card"><h2>关于此项目</h2><div class="version-box spacer-top"><span>管理面板 <strong>v${esc(S.meta.version)}</strong></span><span>上游后端 <strong>v${esc(S.meta.upstream.version)}</strong></span><span>提交 <code>${esc(S.meta.upstream.commit.slice(0, 12))}</code></span></div><p class="subline spacer-top">基于 <a href="https://github.com/dmunozv04/iSponsorBlockTV" target="_blank" rel="noopener noreferrer">iSponsorBlockTV</a> · GPL-3.0。镜像不会在后台自动升级，更新后重新拉取镜像即可。</p><div class="actions spacer-top"><a class="btn sm" href="https://github.com/dongbo501/isponsorblocktv-web#readme" target="_blank" rel="noopener noreferrer">使用教程 ↗</a><a class="btn sm" href="/api/openapi.json" target="_blank" rel="noopener noreferrer">API 文档 ↗</a><button class="btn sm" data-action="logout">退出登录</button></div></section></div>`
  );
}
function updateLive() {
  const top = $("#top-status"),
    [label, cls] = states[S.status.state] || ["状态未知", "warn"];
  top.textContent = label;
  top.className = `badge ${cls}`;
  if ($("#stat-state")) {
    $("#stat-state").textContent =
      S.status.state === "running"
        ? "正在守护"
        : S.status.state === "waiting"
          ? "准备就绪"
          : label;
    $("#stat-uptime").textContent =
      S.status.state === "running"
        ? `已运行 ${uptime()}`
        : "配置就绪后自动开始";
    $("#stat-devices").textContent = Object.values(S.status.devices).filter(
      (d) => d.connected,
    ).length;
  }
  if ($("#device-list")) $("#device-list").innerHTML = deviceList();
  if ($("#stat-skips")) $("#stat-skips").textContent = S.statistics ? countText(S.statistics.global.total) : "—";
  if ($("#skip-statistics")) {
    const content = statisticsContent();
    if ($("#skip-statistics").innerHTML !== content) $("#skip-statistics").innerHTML = content;
    $("#statistics-error").hidden = !S.statisticsStale;
  }
  if ($("#service-state")) $("#service-state").innerHTML = badge();
  renderLogs();
}
function renderLogs() {
  if ($("#recent-logs"))
    $("#recent-logs").innerHTML = S.logs.length
      ? S.logs
          .slice(-4)
          .reverse()
          .map(
            (l) =>
              `<div class="log-preview"><time>${new Date(l.time).toLocaleTimeString("zh-CN", { hour12: false })}</time><span class="dot"></span><span title="${esc(l.message)}">${esc(l.message)}</span></div>`,
          )
          .join("")
      : '<p class="subline">还没有运行记录。添加电视后，这里会显示连接与跳过动态。</p>';
  if ($("#logs-list") && !S.paused) {
    const node = $("#logs-list"),
      atBottom = node.scrollTop + node.clientHeight >= node.scrollHeight - 50,
      level = $("#log-level").value;
    const logs = S.logs.filter((l) => level === "ALL" || l.level === level);
    node.innerHTML = logs.length
      ? logs
          .map(
            (l) =>
              `<div class="log-line ${l.level.toLowerCase()}"><time>${new Date(l.time).toLocaleTimeString("zh-CN", { hour12: false })}</time><span class="level">${esc(l.level)}</span><span>${esc(l.message)}</span></div>`,
          )
          .join("")
      : "<p>暂无日志。添加电视并启动服务后，运行信息会显示在这里。</p>";
    if (atBottom) node.scrollTop = node.scrollHeight;
  }
  if ($("#log-live")) {
    $("#log-live").textContent = S.paused ? "已暂停刷新" : "实时刷新";
    $("#log-live").className = S.paused ? "badge" : "badge good";
  }
}
async function refreshStatistics() {
  try {
    S.statistics = await api("/statistics");
    S.statisticsStale = false;
  } catch {
    S.statisticsStale = true;
  }
}
async function refresh() {
  if (!S.authenticated || polling || document.hidden) return;
  polling = true;
  try {
    const [status] = await Promise.all([api("/status"), refreshStatistics()]);
    S.status = status;
    if (["overview", "logs"].includes(S.page) && !S.paused) {
      const after = S.logs.at(-1)?.id || 0;
      let data = await api(`/logs?after=${after}`);
      if (data.cursor < after) {
        S.logs = [];
        data = await api("/logs");
      }
      S.logs = [...S.logs, ...data.entries].slice(-1000);
    }
    updateLive();
  } catch (error) {
    if (S.authenticated) {
      S.statisticsStale = true;
      if ($("#statistics-error")) $("#statistics-error").hidden = false;
      $("#top-status").textContent = "面板连接中断";
      $("#top-status").className = "badge bad";
    }
  } finally {
    polling = false;
  }
}
function navigate(page) {
  if (S.dirty && !confirm("当前有未保存的修改，确定离开此页面吗？")) return;
  S.dirty = false;
  S.page = page;
  history.replaceState(null, "", `#${page}`);
  render();
  refresh();
  window.scrollTo({ top: 0 });
}
function bindForm(id, handler) {
  const form = $(`#${id}`);
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const submit = $('button[type="submit"]', form);
    submit.disabled = true;
    $(".inline-error", form)?.remove();
    try {
      await handler(form);
    } catch (error) {
      errorIn(form, error);
    } finally {
      submit.disabled = false;
    }
  });
}
function bindForms() {
  bindForm("rules-form", async (form) => {
    const c = copyConfig();
    c.skip_categories = $$('input[name="category"]:checked', form).map(
      (e) => e.value,
    );
    c.minimum_skip_length = Number(form.elements.minimum_skip_length.value);
    for (const key of [
      "skip_count_tracking",
      "mute_ads",
      "skip_ads",
      "auto_play",
    ])
      c[key] = form.elements[key].checked;
    await save(c);
  });
  bindForm("advanced-form", async (form) => {
    const c = copyConfig();
    for (const key of ["join_name", "sponsorblock_api_url", "apikey"])
      c[key] = form.elements[key].value.trim();
    c.use_proxy = form.elements.use_proxy.checked;
    await save(c);
  });
  bindForm("panel-form", async (form) => {
    S.panel = await put("/panel", {
      proxy_url: form.elements.proxy_url.value.trim(),
      debug: form.elements.debug.checked,
      http_tracing: form.elements.http_tracing.checked,
    });
    S.dirty = false;
    toast("代理与诊断设置已应用");
  });
  $("#log-level")?.addEventListener("change", renderLogs);
}
function pairDialog(tab = "code") {
  const tabs = `<div class="tabs"><button type="button" class="${tab === "code" ? "active" : ""}" data-action="pair-tab-code">电视配对码</button><button type="button" class="${tab === "discover" ? "active" : ""}" data-action="pair-tab-discover">局域网搜索</button><button type="button" class="${tab === "manual" ? "active" : ""}" data-action="pair-tab-manual">手动添加</button></div>`;
  if (tab === "code") {
    modal(
      "添加电视",
      `${tabs}<form id="pair-form"><div class="hint">在电视 YouTube 中打开「设置」→「使用电视代码关联」，然后输入屏幕上的 12 位数字。</div><label class="field spacer-top"><span>电视配对码</span><input class="pair-code" name="code" placeholder="000 000 000 000" inputmode="numeric" autocomplete="off" required maxlength="32" aria-label="电视配对码"></label>${field("name", "设备名称（可选）", "", "留空则使用电视返回的名称。", "text", 'maxlength="100" placeholder="例如：客厅电视"')}<div class="modal-footer"><button class="btn" type="button" data-action="close-modal">取消</button><button class="btn primary" type="submit">配对并添加</button></div></form>`,
    );
    bindForm("pair-form", async (form) => {
      const data = await post("/devices/pair", {
        code: form.elements.code.value,
        name: form.elements.name.value.trim(),
        revision: S.revision,
      });
      adopt(data);
      S.status = await api("/status");
      closeModal();
      render();
      toast("电视配对成功，已开始连接");
    });
  } else if (tab === "manual") {
    modal(
      "手动添加电视",
      `${tabs}<form id="manual-device-form"><div class="hint">适用于已有 Screen ID 的设备，通常推荐使用电视配对码。旧版 26 位 ID 可能已被 YouTube 撤销。</div><div class="spacer-top">${field("name", "设备名称", "", "", "text", 'required maxlength="100"')}${field("screen_id", "Screen ID", "", "这是设备连接凭据，请勿公开分享。", "password", 'required maxlength="4096" autocomplete="off"')}</div><div class="modal-footer"><button class="btn primary" type="submit">添加设备</button></div></form>`,
    );
    bindForm("manual-device-form", async (form) => {
      const c = copyConfig();
      c.devices.push({
        name: form.elements.name.value.trim(),
        screen_id: form.elements.screen_id.value.trim(),
        offset: 0,
      });
      await save(c);
      closeModal();
    });
  } else {
    modal(
      "搜索局域网电视",
      `${tabs}<div class="hint">请让电视和容器处于同一局域网，并打开 YouTube。Linux Docker 推荐使用 host 网络；普通端口映射下可能无法发现设备。</div><form id="discovery-form">${switchRow("active", "主动唤起 YouTube", "启用后，搜索可能会在局域网电视上启动 YouTube 应用。", false)}<button class="btn primary spacer-top" type="submit">${icon("search")}开始搜索</button></form><div id="discovery-results" class="spacer-top"></div>`,
    );
    bindForm("discovery-form", async (form) => {
      const node = $("#discovery-results");
      node.textContent = "正在搜索，请稍候（约 12–35 秒）…";
      const data = await post("/devices/discover", {
        active: form.elements.active.checked,
      });
      S.discovered = data.devices;
      node.innerHTML = data.devices.length
        ? data.devices
            .map(
              (d, i) =>
                `<div class="result"><span>${esc(d.name || "YouTube on TV")}</span><button class="btn sm" data-action="add-discovered" data-index="${i}">添加</button></div>`,
            )
            .join("")
        : '<div class="hint">未发现设备。请检查网络模式，或改用电视配对码。</div>';
    });
  }
}
function editDevice(index) {
  const d = S.config.devices[index];
  modal(
    "编辑电视",
    `<form id="edit-device-form">${field("name", "设备名称", d.name, "", "text", 'required maxlength="100"')}${field("offset", "时间偏移（毫秒）", d.offset, "正值提前跳过，负值延后跳过；通常保持 0 即可。", "number", 'required min="-60000" max="60000" step="1"')}${field("screen_id", "Screen ID", d.screen_id, "设备失联时，可移除后使用新电视配对码重新添加。", "password", 'required maxlength="4096" autocomplete="off"')}<button class="quiet" type="button" data-action="toggle-screen-id">显示 / 隐藏 Screen ID</button><div class="modal-footer"><button class="btn" type="button" data-action="close-modal">取消</button><button class="btn primary" type="submit">保存修改</button></div></form>`,
  );
  bindForm("edit-device-form", async (form) => {
    const c = copyConfig();
    c.devices[index] = {
      name: form.elements.name.value.trim(),
      offset: Number(form.elements.offset.value),
      screen_id: form.elements.screen_id.value.trim(),
    };
    await save(c);
    closeModal();
  });
}
function channelDialog() {
  if (!S.config.apikey) {
    toast("请先在高级设置中保存 YouTube Data API 密钥", true);
    navigate("settings");
    return;
  }
  modal(
    "添加白名单频道",
    `<form id="channel-search-form"><label class="field"><span>搜索频道</span><input name="query" placeholder="输入频道名称" maxlength="100" required></label><button class="btn" type="submit">${icon("search")}搜索频道</button></form><div id="channel-results" class="spacer-top"></div><form id="channel-form" class="spacer-top"><h3>或手动填写频道</h3><div class="spacer-top">${field("name", "频道名称", "", "", "text", 'required maxlength="200" placeholder="例如：我喜欢的创作者"')}${field("id", "频道 ID", "", "以 UC 开头的 24 位 ID，不是 @用户名或网址。", "text", 'required pattern="UC[A-Za-z0-9_-]{22}" placeholder="UC…"')}</div><div class="modal-footer"><button class="btn primary" type="submit">加入白名单</button></div></form>`,
  );
  bindForm("channel-search-form", async (form) => {
    const node = $("#channel-results");
    node.textContent = "正在查询…";
    const data = await api(
      `/channels/search?q=${encodeURIComponent(form.elements.query.value)}`,
    );
    S.channels = data.channels;
    node.innerHTML = data.channels.length
      ? data.channels
          .map(
            (c, i) =>
              `<div class="result"><span>${esc(c.name)}<small>订阅人数 ${esc(c.subscribers === "Hidden" ? "未公开" : c.subscribers)}</small></span><button class="btn sm" data-action="add-search-channel" data-index="${i}">添加</button></div>`,
          )
          .join("")
      : '<div class="hint">未找到频道，请检查关键词、API 密钥权限和配额。</div>';
  });
  bindForm("channel-form", async (form) => {
    const c = copyConfig();
    c.channel_whitelist.push({
      id: form.elements.id.value.trim(),
      name: form.elements.name.value.trim(),
    });
    await save(c);
    closeModal();
  });
}
function importDialog() {
  modal(
    "导入配置",
    `<form id="import-form"><div class="hint warning">导入将覆盖当前电视、跳过规则、频道白名单及 API 密钥。建议先导出备份。支持原版 v2 配置；不会修改管理密码和代理地址。</div><label class="field spacer-top"><span>选择 config.json</span><input class="file-input" name="file" type="file" accept=".json,application/json" required></label><div class="modal-footer"><button class="btn" type="button" data-action="close-modal">取消</button><button class="btn primary" type="submit">导入并应用</button></div></form>`,
  );
  bindForm("import-form", async (form) => {
    const file = form.elements.file.files[0];
    if (file.size > 1048576) throw new Error("配置文件不能超过 1 MB");
    let config;
    try {
      config = JSON.parse(await file.text());
    } catch {
      throw new Error("不是有效的 JSON 文件");
    }
    if (!config || Array.isArray(config) || typeof config !== "object")
      throw new Error("配置必须为 JSON 对象");
    if ("atvs" in config)
      throw new Error("v1 atvs 配置已失效，请使用电视配对码重新添加设备");
    delete config.config_file;
    delete config.data_dir;
    if (
      !confirm(
        `将覆盖现有配置，导入 ${config.devices?.length || 0} 台设备，确定继续吗？`,
      )
    )
      return;
    await save(config);
    closeModal();
  });
}
function passwordDialog() {
  modal(
    "修改管理密码",
    `<form id="password-form">${field("current_password", "当前密码", "", "", "password", 'required maxlength="128" autocomplete="current-password"')}${field("password", "新密码", "", "至少 10 个字符。", "password", 'required minlength="10" maxlength="128" autocomplete="new-password"')}${field("confirm", "确认新密码", "", "", "password", 'required minlength="10" maxlength="128" autocomplete="new-password"')}<div class="modal-footer"><button class="btn primary" type="submit">更新密码</button></div></form>`,
  );
  bindForm("password-form", async (form) => {
    if (form.elements.password.value !== form.elements.confirm.value)
      throw new Error("两次输入的新密码不一致");
    const data = await post("/auth/password", {
      current_password: form.elements.current_password.value,
      password: form.elements.password.value,
    });
    S.csrf = data.csrf;
    closeModal();
    toast("管理密码已更新，其他会话已失效");
  });
}
async function logout() {
  if (S.dirty && !confirm("有未保存的修改，确定退出吗？")) return;
  await post("/auth/logout", {});
  S.authenticated = false;
  S.csrf = null;
  S.config = null;
  S.panel = {};
  S.logs = [];
  S.statistics = null;
  S.statisticsStale = false;
  S.dirty = false;
  await showAuth();
}
async function action(node) {
  const name = node.dataset.action,
    index = Number(node.dataset.index);
  if (name.startsWith("go-")) {
    navigate(name.slice(3));
    return;
  }
  if (name.startsWith("pair-tab-")) {
    pairDialog(name.slice(9));
    return;
  }
  switch (name) {
    case "close-modal":
      closeModal();
      break;
    case "add-device":
      pairDialog();
      break;
    case "discover-device":
      pairDialog("discover");
      break;
    case "edit-device":
      editDevice(index);
      break;
    case "toggle-screen-id": {
      const field = $("#edit-device-form").elements.screen_id;
      field.type = field.type === "password" ? "text" : "password";
      break;
    }
    case "delete-device":
      if (
        confirm(
          `确定移除「${S.config.devices[index].name}」吗？这会停止对这台电视的自动操作。`,
        )
      ) {
        const c = copyConfig();
        c.devices.splice(index, 1);
        await save(c);
      }
      break;
    case "add-discovered": {
      const d = S.discovered[index],
        c = copyConfig();
      c.devices.push({
        screen_id: d.screen_id,
        name: d.name || "YouTube on TV",
        offset: d.offset || 0,
      });
      await save(c);
      closeModal();
      break;
    }
    case "categories-all":
    case "categories-none":
      $$('input[name="category"]').forEach(
        (input) => (input.checked = name === "categories-all"),
      );
      S.dirty = true;
      break;
    case "add-channel":
      channelDialog();
      break;
    case "add-search-channel": {
      const channel = S.channels[index],
        c = copyConfig();
      c.channel_whitelist.push({ id: channel.id, name: channel.name });
      await save(c);
      closeModal();
      break;
    }
    case "delete-channel":
      if (
        confirm(
          `确定移除白名单频道「${S.config.channel_whitelist[index].name}」吗？`,
        )
      ) {
        const c = copyConfig();
        c.channel_whitelist.splice(index, 1);
        await save(c);
      }
      break;
    case "pause-logs":
      S.paused = !S.paused;
      node.textContent = S.paused ? "继续刷新" : "暂停刷新";
      renderLogs();
      if (!S.paused) await refresh();
      break;
    case "service-start":
    case "service-restart":
    case "service-stop":
      S.status = await post("/service", { action: name.slice(8) });
      updateLive();
      toast(name === "service-stop" ? "后台服务已暂停" : "后台服务设置已应用");
      break;
    case "import-config":
      importDialog();
      break;
    case "change-password":
      passwordDialog();
      break;
    case "logout":
      await logout();
      break;
  }
}
document.addEventListener("click", async (e) => {
  const link = e.target.closest("[data-page]");
  if (link) {
    e.preventDefault();
    navigate(link.dataset.page);
    return;
  }
  const node = e.target.closest("[data-action]");
  if (!node || node.disabled) return;
  e.preventDefault();
  node.disabled = true;
  try {
    await action(node);
  } catch (error) {
    toast(error.message, true);
  } finally {
    node.disabled = false;
  }
});
document.addEventListener("input", (e) => {
  if (e.target.closest("#rules-form,#advanced-form,#panel-form"))
    S.dirty = true;
});
$("#logout").addEventListener("click", () =>
  logout().catch((e) => toast(e.message, true)),
);
window.addEventListener("beforeunload", (e) => {
  if (S.dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
window.addEventListener("hashchange", () => {
  const page = location.hash.slice(1);
  if (S.authenticated && nav.some((n) => n[0] === page) && page !== S.page)
    navigate(page);
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});
setInterval(refresh, 5000);
showAuth().catch((error) => {
  $("#boot").textContent = error.message + " 请刷新页面重试。";
});
