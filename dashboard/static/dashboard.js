"use strict";

var byId = function (id) { return document.getElementById(id); };
var clamp = function (v, min, max) { min = min || 0; max = max === undefined ? 100 : max; return Math.min(max, Math.max(min, Number(v) || 0)); };

function node(tag, className, textValue) {
  var el = document.createElement(tag);
  if (className) { el.className = className; }
  if (textValue !== undefined && textValue !== null) { el.textContent = String(textValue); }
  return el;
}

function replaceChildren(target, children) {
  target.replaceChildren.apply(target, children);
  target.classList.remove("empty-state");
}

function setText(id, value, fallback) {
  var el = byId(id);
  if (!el) { return; }
  el.textContent = (value === undefined || value === null || value === "") ? (fallback || "—") : String(value);
}

function setMeter(id, value) {
  var el = byId(id);
  if (el) { el.style.width = clamp(value) + "%"; }
}

function percent(value) {
  return (value === undefined || value === null) ? "—" : Number(value).toFixed(1) + "%";
}

function formatCollectedAt(value) {
  if (!value) { return "未知时间"; }
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
    }).format(new Date(value)).split("/").join("-");
  } catch (err) {
    return value;
  }
}

function detailRow(label, value) {
  var row = node("div", "detail-row");
  row.append(node("span", "", label), node("strong", "", value || "—"));
  return row;
}

function renderSummary(data) {
  var summary = data.summary || {};
  var system = data.system || {};
  var pill = byId("health-pill");
  pill.className = "health-pill " + (summary.level === "warning" ? "warning" : "healthy");
  pill.lastElementChild.textContent = summary.title || "已采集";
  setText("hero-subtitle", (summary.message || "状态快照已更新") + " · 采集于 " + formatCollectedAt(data.collected_at));
  setText("system-device", (system.device || "HF310") + (system.machine ? " · " + system.machine : ""));
  setText("system-uptime", system.uptime_human);
  setText("system-load", Array.isArray(system.load) ? system.load.join(" / ") : "—");
  var procs = data.processes || {};
  setText("system-processes", (procs.count === undefined ? "—" : procs.count) + " / " + (procs.threads === undefined ? "—" : procs.threads));
  setText("collection-duration", "本次采集耗时 " + (data.collection_ms === undefined ? "—" : data.collection_ms) + " ms");
}

function renderMetrics(data) {
  var cpu = data.cpu || {};
  var ram = (data.memory || {}).ram || {};
  var temps = Array.isArray(data.temperatures) ? data.temperatures : [];
  var hottest = null;
  temps.forEach(function (t) { if (!hottest || t.celsius > hottest.celsius) { hottest = t; } });
  var disks = Array.isArray(data.disks) ? data.disks : [];
  var root = disks[0] || {};

  setText("cpu-value", percent(cpu.usage_percent));
  setText("cpu-detail", (cpu.logical_cores || "—") + " 线程 · 空闲 " + percent(cpu.idle_percent));
  setMeter("cpu-meter", cpu.usage_percent);

  setText("temp-value", hottest ? hottest.celsius.toFixed(1) + "°" : "—");
  setText("temp-detail", hottest ? hottest.name : "暂无温度数据");
  setMeter("temp-meter", hottest ? hottest.celsius : 0);

  setText("memory-value", percent(ram.percent));
  setText("memory-detail", (ram.used_human || "—") + " / " + (ram.total_human || "—"));
  setMeter("memory-meter", ram.percent);

  setText("disk-value", percent(root.percent));
  setText("disk-detail", (root.used_human || "—") + " / " + (root.total_human || "—"));
  setMeter("disk-meter", root.percent);
}

function renderLinks(data) {
  var net = data.network || {};
  var ifaces = Array.isArray(net.interfaces) ? net.interfaces : [];
  var target = byId("link-list");
  var tag = byId("link-state");
  var internet = net.internet || {};
  tag.className = "status-tag " + (internet.ok ? "healthy" : "warning");
  tag.textContent = internet.ok ? "外网正常" : "外网异常";
  if (!ifaces.length) {
    target.textContent = "没有可用网络接口";
    return;
  }
  var rows = [];
  ifaces.forEach(function (i) {
    var label = i.name + " · " + (i.state === "up" ? "已连接" : i.state);
    var value = (i.ipv4 || "无 IPv4") + " · ↓" + i.rx_human + " ↑" + i.tx_human;
    rows.push(detailRow(label, value));
  });
  rows.push(detailRow("默认路由", (net.default_dev || "—") + " 网关 " + (net.gateway || "—")));
  rows.push(detailRow("外网延迟", internet.ok ? (internet.latency_ms + " ms") : "不可达"));
  replaceChildren(target, rows);
  setText("network-summary", internet.ok ? (ifaces.filter(function (i) { return i.state === "up"; }).length + " 个在线 · " + internet.latency_ms + " ms") : "外网不通");
}

function renderFrp(data) {
  var frp = data.frp || {};
  var target = byId("frp-details");
  var tag = byId("frp-state");
  tag.className = "status-tag " + (frp.active ? "healthy" : "warning");
  tag.textContent = frp.active ? "运行中" : "未运行";
  var cards = [];
  var current = node("div", "model-current");
  current.append(node("span", "", "FRPC CLIENT"));
  current.append(node("strong", "", frp.active ? "已连接穿透服务" : "未连接"));
  current.append(node("small", "", "服务器 " + (frp.server || "—")));
  cards.push(current);
  var list = node("div", "detail-list");
  list.append(detailRow("穿透代理", (frp.proxies || []).join(", ") || "—"));
  list.append(detailRow("最近日志", frp.last || "—"));
  cards.push(list);
  replaceChildren(target, cards);
}

function renderCores(data) {
  var cpu = data.cpu || {};
  setText("cpu-model", (cpu.model || "—") + (cpu.freq_mhz && cpu.freq_mhz.length ? " · " + Math.round(cpu.freq_mhz.reduce(function (a, b) { return a + b; }, 0) / cpu.freq_mhz.length) + " MHz" : ""));
  var cores = Array.isArray(cpu.per_core) ? cpu.per_core : [];
  var coreList = cores.map(function (c) {
    var card = node("div", "core-card");
    card.append(node("span", "", c.name));
    card.append(node("strong", "", percent(c.usage_percent)));
    card.append(node("small", "", "负载"));
    return card;
  });
  replaceChildren(byId("core-list"), coreList);

  var temps = Array.isArray(data.temperatures) ? data.temperatures : [];
  var chips = temps.map(function (t) {
    var chip = node("div", "temperature-chip");
    chip.append(document.createTextNode(t.name));
    chip.append(node("strong", "", t.celsius.toFixed(1) + "°C"));
    return chip;
  });
  replaceChildren(byId("temperature-list"), chips.length ? chips : [node("div", "temperature-chip", "无温度传感器")]);
}

function renderMemoryPanel(data) {
  var mem = data.memory || {};
  var ram = mem.ram || {};
  var swap = mem.swap || {};
  var cards = [];
  var current = node("div", "model-current");
  current.append(node("span", "", "RAM"));
  current.append(node("strong", "", percent(ram.percent)));
  current.append(node("small", "", (ram.used_human || "—") + " / " + (ram.total_human || "—")));
  cards.push(current);
  var list = node("div", "detail-list");
  list.append(detailRow("可用内存", ram_total_avail(ram)));
  list.append(detailRow("交换分区", (swap.total_bytes ? (swap.used_human + " / " + swap.total_human + " · " + percent(swap.percent)) : "未启用")));
  cards.push(list);
  replaceChildren(byId("memory-panel"), cards);
}

function ram_total_avail(ram) {
  if (!ram || !ram.total_bytes) { return "—"; }
  var avail = ram.total_bytes - (ram.used_bytes || 0);
  var mb = avail / 1048576;
  return mb.toFixed(0) + " MiB";
}

function renderDisks(data) {
  var disks = Array.isArray(data.disks) ? data.disks : [];
  var target = byId("disk-list");
  if (!disks.length) { target.textContent = "没有可读取的分区"; return; }
  var cards = disks.map(function (d) {
    var card = node("div", "storage-card");
    var head = node("div", "storage-head");
    head.append(node("strong", "", d.mountpoint), node("span", "", d.fs || ""));
    card.append(head);
    var meter = node("div", "meter");
    var fill = node("span");
    fill.style.width = clamp(d.percent) + "%";
    meter.append(fill);
    card.append(meter);
    var meta = node("div", "storage-meta");
    meta.append(node("span", "", d.used_human + " / " + d.total_human));
    meta.append(node("span", "", percent(d.percent)));
    card.append(meta);
    return card;
  });
  replaceChildren(target, cards);
}

function renderProcList(rows, valueOf) {
  var out = [];
  if (!rows.length) { return [node("div", "process-row", "没有数据")]; }
  rows.forEach(function (r, idx) {
    var row = node("div", "process-row");
    row.append(node("span", "process-rank", idx + 1));
    row.append(node("span", "process-name", r.name));
    row.append(node("span", "process-value", valueOf(r)));
    row.append(node("span", "process-value", "PID " + r.pid));
    out.push(row);
  });
  return out;
}

function renderProcesses(data) {
  var procs = data.processes || {};
  replaceChildren(byId("cpu-process-list"), renderProcList(procs.cpu || [], function (r) { return percent(r.cpu_percent); }));
  replaceChildren(byId("memory-process-list"), renderProcList(procs.memory || [], function (r) { return r.rss_human || "—"; }));
}

function renderTasks(data) {
  var tasks = data.tasks || {};
  var items = Array.isArray(tasks.items) ? tasks.items : [];
  var target = byId("cron-list");
  setText("schedule-summary", tasks.summary || ("0 个定时任务"));
  if (!items.length) {
    target.textContent = tasks.errors && tasks.errors.length ? ("读取失败：" + tasks.errors.join("; ")) : "没有配置定时任务";
    return;
  }
  var cards = items.slice(0, 20).map(function (t) {
    var card = node("div", "schedule-item");
    var head = node("div", "schedule-item-head");
    head.append(node("span", "schedule-name", t.name), node("span", "schedule-source", t.source));
    card.append(head);
    var meta = node("div", "schedule-meta");
    meta.append(node("span", "schedule-expression", t.schedule || "—"));
    meta.append(node("span", "schedule-next", t.next && t.next !== "—" ? t.next : t.status));
    card.append(meta);
    return card;
  });
  replaceChildren(target, cards);
}


function renderOs(data) {
  var system = data.system || {};
  var target = byId("os-details");
  var tag = byId("os-state");
  if (!target) { return; }
  var rows = [];
  rows.push(detailRow("系统版本", system.os_name || system.os));
  if (system.os_version) { rows.push(detailRow("发行版本", system.os_version)); }
  rows.push(detailRow("内核", system.kernel));
  rows.push(detailRow("架构", system.machine));
  rows.push(detailRow("主机名", system.hostname));
  if (system.hardware_model) { rows.push(detailRow("硬件型号", system.hardware_model)); }
  rows.push(detailRow("时区", system.timezone));
  rows.push(detailRow("启动时间", formatCollectedAt(system.boot_time)));
  if (system.systemd_version) { rows.push(detailRow("systemd", system.systemd_version)); }
  if (system.package_count !== undefined && system.package_count !== null) {
    rows.push(detailRow("已安装软件包", system.package_count + " 个"));
  }
  if (tag) {
    tag.className = "status-tag healthy";
    tag.textContent = system.machine || "已读取";
  }
  replaceChildren(target, rows);
}

function renderAll(data) {
  renderSummary(data);
  renderOs(data);
  renderMetrics(data);
  renderLinks(data);
  renderFrp(data);
  renderCores(data);
  renderMemoryPanel(data);
  renderDisks(data);
  renderProcesses(data);
  renderTasks(data);
}

function setBanner(mode, data) {
  var title = byId("refresh-title");
  var detail = byId("refresh-detail");
  var banner = byId("refresh-banner");
  banner.classList.toggle("complete", mode === "fresh");
  if (mode === "cached") {
    title.textContent = "显示上次缓存";
    detail.textContent = "采集于 " + formatCollectedAt(data && data.collected_at) + "，正在获取最新状态…";
  } else if (mode === "fresh") {
    title.textContent = "状态已更新";
    detail.textContent = "采集于 " + formatCollectedAt(data && data.collected_at) + "。" + (data && data.throttled ? "（10 秒内复用快照）" : "");
  } else if (mode === "error") {
    title.textContent = "采集失败";
    detail.textContent = "请稍后刷新页面重试。";
  } else {
    title.textContent = "正在获取最新状态";
    detail.textContent = "已有缓存会先显示，采集完成后自动替换。";
  }
}

function refresh() {
  return fetch("/api/refresh", { method: "POST", credentials: "same-origin", headers: { "Accept": "application/json" } })
    .then(function (res) {
      if (res.status === 401) { window.location.reload(); return null; }
      if (!res.ok) { throw new Error("HTTP " + res.status); }
      return res.json();
    })
    .then(function (data) {
      if (!data) { return; }
      renderAll(data);
      setBanner("fresh", data);
    })
    .catch(function () { setBanner("error"); });
}

function init() {
  var raw = byId("initial-snapshot");
  var cached = null;
  try { cached = raw && raw.textContent ? JSON.parse(raw.textContent) : null; } catch (err) { cached = null; }
  if (cached && cached.collected_at) {
    renderAll(cached);
    setBanner("cached", cached);
  }
  var button = byId("refresh-button");
  if (button) { button.addEventListener("click", function () { window.location.reload(); }); }
  refresh();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
