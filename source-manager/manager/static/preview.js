"use strict";
const $ = (id) => document.getElementById(id);
const video = $("video");
let channels = [],
  category = "",
  selected = null,
  sourceIndex = 0,
  csrf = "";
let hls = null,
  token = null,
  serial = 0,
  timer = null,
  playing = false,
  actualMode = "";
const owner = crypto.randomUUID
  ? crypto.randomUUID()
  : `${Date.now()}-${Math.random()}`;
const escape = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const visible = () =>
  channels.filter(
    (c) =>
      (!category || c.category === category) &&
      (c.name + c.category)
        .toLowerCase()
        .includes($("search").value.trim().toLowerCase()),
  );
async function api(path, body) {
  const response = await fetch(
    path,
    body === undefined
      ? { cache: "no-store" }
      : {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
          body: JSON.stringify(body),
        },
  );
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "请求失败");
  return result;
}
function status(text) {
  $("status").textContent = text;
}
function render() {
  const focusedChannel = document.activeElement?.dataset.channel;
  const focusedGroup = document.activeElement?.dataset.group;
  const groups = [...new Set(channels.map((c) => c.category))];
  $("groups").innerHTML = ["", ...groups]
    .map(
      (g) =>
        `<button class="group ${g === category ? "active" : ""}" data-group="${escape(g)}" aria-pressed="${g === category}">${escape(g || "全部频道")}<span>${channels.filter((c) => !g || c.category === g).length}</span></button>`,
    )
    .join("");
  const list = visible();
  $("category-title").textContent = category || "全部频道";
  $("count").textContent = `${list.length} 个频道 · 已发布可用列表`;
  $("cards").innerHTML = list
    .map(
      (c) =>
        `<button class="card ${selected?.id === c.id ? "active" : ""}" data-channel="${c.id}" aria-label="播放 ${escape(c.name)}" aria-pressed="${selected?.id === c.id}"><div class="logo">${c.logo ? `<img src="${escape(c.logo)}" alt="" loading="lazy" referrerpolicy="no-referrer" />` : escape(c.name.slice(0, 7))}</div>${selected?.id === c.id ? '<span class="playing">已选中</span>' : ""}<div class="card-info"><span class="card-title">${escape(c.name)}</span><small>${c.sources.length} 条线路 · ${escape(c.category)}</small></div></button>`,
    )
    .join("");
  $("cards")
    .querySelectorAll("img")
    .forEach((img) =>
      img.addEventListener(
        "error",
        () => {
          img.parentNode.textContent =
            list
              .find((c) => c.id === img.closest("button").dataset.channel)
              ?.name.slice(0, 7) || "TV";
        },
        { once: true },
      ),
    );
  if (focusedChannel)
    [...$("cards").querySelectorAll("button")]
      .find((b) => b.dataset.channel === focusedChannel)
      ?.focus({ preventScroll: true });
  else if (focusedGroup !== undefined)
    [...$("groups").querySelectorAll("button")]
      .find((b) => b.dataset.group === focusedGroup)
      ?.focus({ preventScroll: true });
  $("empty").hidden = !!list.length;
  $("previous").disabled = $("next").disabled = !selected || !list.length;
}
async function load() {
  try {
    $("refresh").disabled = true;
    const result = await api("/api/preview/channels");
    channels = result.channels;
    csrf = result.csrf;
    if (category && !channels.some((c) => c.category === category))
      category = "";
    if (
      selected &&
      !channels.some(
        (c) =>
          c.id === selected.id &&
          c.sources.some((s) => s.url === selected.sources[sourceIndex]?.url),
      )
    ) {
      stop();
      selected = null;
      $("now-title").textContent = "频道列表已更新";
    }
    render();
    if (!channels.length) {
      $("empty").textContent =
        "还没有已发布的频道，请到源管理添加入口并执行更新。";
      status("订阅生成后，频道会显示在这里。");
    }
  } catch (e) {
    status(`读取列表失败：${e.message}`);
  } finally {
    $("refresh").disabled = false;
  }
}
function release() {
  video.onerror = video.onplaying = video.onwaiting = null;
  clearTimeout(timer);
  if (hls) {
    hls.destroy();
    hls = null;
  }
  video.pause();
  video.removeAttribute("src");
  video.load();
  if (token) {
    fetch("/api/preview/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ token }),
      keepalive: true,
    }).catch(() => {});
    token = null;
  }
}
function stop() {
  serial++;
  playing = false;
  release();
  $("placeholder").hidden = false;
  $("stop").disabled = true;
  $("retry").hidden = !selected;
  status("已停止预览。");
}
function sources() {
  $("source").disabled = false;
  $("source").innerHTML = selected.sources
    .map(
      (s, i) =>
        `<option value="${i}">线路 ${i + 1}${s.resolution ? ` · ${escape(s.resolution)}` : ""}</option>`,
    )
    .join("");
  $("source").value = String(sourceIndex);
}
async function play(channel, index = 0, forceMode = null, automatic = false) {
  const run = ++serial;
  release();
  selected = channel;
  sourceIndex = index;
  playing = true;
  sources();
  render();
  $("now-title").textContent = channel.name;
  $("placeholder").hidden = true;
  $("stop").disabled = false;
  $("retry").hidden = true;
  actualMode =
    forceMode || ($("transport").value === "relay" ? "relay" : "direct");
  status(
    `正在连接线路 ${index + 1} · ${actualMode === "relay" ? "预览转发" : "直连"}${automatic ? "，自动尝试备用连接" : ""}…`,
  );
  let url = channel.sources[index].url;
  let failed = false;
  const fail = async (reason) => {
    if (run !== serial || failed || !playing) return;
    failed = true;
    if (
      $("transport").value === "auto" &&
      actualMode === "direct" &&
      /^https?:/i.test(url)
    )
      return play(channel, index, "relay", true);
    if ($("transport").value === "auto" && index + 1 < channel.sources.length)
      return play(channel, index + 1, null, true);
    serial++;
    playing = false;
    release();
    $("stop").disabled = true;
    $("retry").hidden = false;
    status(`${reason}。可换一条线路或连接方式后重试。`);
  };
  try {
    if (!/^https?:/i.test(url)) throw new Error("浏览器不支持此流协议");
    if (actualMode === "relay") {
      const result = await api("/api/preview/start", {
        channel: channel.id,
        source: index,
        owner,
      });
      if (run !== serial) {
        api("/api/preview/stop", { token: result.token }).catch(() => {});
        return;
      }
      url = result.url;
      token = result.token;
    }
    if (run !== serial) return;
    video.onerror = () => fail("浏览器无法读取或解码该线路");
    const attempt = () =>
      video.play().catch((e) => {
        if (run !== serial || e.name === "AbortError") return;
        if (e.name === "NotAllowedError") {
          clearTimeout(timer);
          status("连接已就绪，请点击播放器的播放按钮。");
        } else fail("浏览器无法开始播放");
      });
    video.onplaying = () => {
      if (run !== serial) return;
      clearTimeout(timer);
      status(
        `正在播放 · 线路 ${index + 1} · ${actualMode === "relay" ? "预览转发（无转码）" : "直连"}`,
      );
    };
    video.onwaiting = () => {
      if (run === serial && playing) {
        status("正在缓冲…");
        clearTimeout(timer);
        timer = setTimeout(() => fail("缓冲超时"), 20000);
      }
    };
    timer = setTimeout(() => fail("连接超时"), 20000);
    if (window.Hls?.isSupported()) {
      hls = new Hls({
        enableWorker: true,
        backBufferLength: 15,
        maxBufferLength: 15,
        manifestLoadPolicy: {
          default: {
            maxTimeToFirstByteMs: 8000,
            maxLoadTimeMs: 10000,
            timeoutRetry: {
              maxNumRetry: 0,
              retryDelayMs: 0,
              maxRetryDelayMs: 0,
            },
            errorRetry: { maxNumRetry: 0, retryDelayMs: 0, maxRetryDelayMs: 0 },
          },
        },
      });
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) fail("此线路在浏览器中播放失败");
      });
      hls.on(Hls.Events.MANIFEST_PARSED, attempt);
      hls.loadSource(url);
      hls.attachMedia(video);
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = url;
      attempt();
    } else throw new Error("当前浏览器不支持 HLS 播放");
  } catch (e) {
    fail(e.message);
  }
}
function step(delta) {
  const list = visible();
  if (!list.length) return;
  const index = list.findIndex((c) => c.id === selected?.id);
  play(list[(index + delta + list.length) % list.length]);
}
$("groups").addEventListener("click", (e) => {
  const button = e.target.closest("[data-group]");
  if (button) {
    category = button.dataset.group;
    render();
  }
});
$("cards").addEventListener("click", (e) => {
  const button = e.target.closest("[data-channel]");
  if (button) play(channels.find((c) => c.id === button.dataset.channel));
});
$("search").addEventListener("input", render);
$("source").addEventListener(
  "change",
  () => selected && play(selected, Number($("source").value)),
);
$("transport").addEventListener(
  "change",
  () => selected && play(selected, sourceIndex),
);
$("stop").addEventListener("click", stop);
$("retry").addEventListener(
  "click",
  () => selected && play(selected, sourceIndex),
);
$("previous").addEventListener("click", () => step(-1));
$("next").addEventListener("click", () => step(1));
$("refresh").addEventListener("click", load);
function fullscreen() {
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  else
    $("player-shell")
      .requestFullscreen?.()
      .catch(() => status("当前浏览器不支持全屏，请使用播放器的全屏按钮。"));
}
$("fullscreen").addEventListener("click", fullscreen);
document.addEventListener("keydown", (e) => {
  if (
    e.altKey ||
    e.ctrlKey ||
    e.metaKey ||
    /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)
  )
    return;
  const cards = [...$("cards").querySelectorAll(".card")],
    index = cards.indexOf(document.activeElement);
  if (
    index >= 0 &&
    ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(e.key)
  ) {
    const columns = getComputedStyle($("cards")).gridTemplateColumns.split(
      " ",
    ).length;
    const offset = {
      ArrowRight: 1,
      ArrowLeft: -1,
      ArrowDown: columns,
      ArrowUp: -columns,
    }[e.key];
    cards[Math.max(0, Math.min(cards.length - 1, index + offset))]?.focus();
    e.preventDefault();
  } else if (e.key === "PageDown" || e.key === "PageUp") {
    step(e.key === "PageDown" ? 1 : -1);
    e.preventDefault();
  } else if (e.key.toLowerCase() === "f") {
    fullscreen();
    e.preventDefault();
  }
});
window.addEventListener("pagehide", () => {
  serial++;
  release();
});
load();
