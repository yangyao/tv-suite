import ipaddress
import json
import math
import re
import secrets
import socket
import threading
import time
from urllib.parse import urljoin, urlsplit

import requests
from flask import Response, jsonify, render_template, request, stream_with_context

from manager.playlists import channel_key, parse_playlist


def catalog(store):
    current = (store.root / "current").resolve()
    path = current / "result.m3u"
    if not path.exists():
        return []
    details = current / "channels.json"
    rows = json.loads(details.read_text()) if details.exists() else []
    metrics = {
        (row["channel_key"], result["url"]): result
        for row in rows
        for result in row.get("results", [])
    }
    channels = {}
    for entry in parse_playlist(path.read_text()):
        key = channel_key(entry["category"], entry["name"])
        logo = re.search(r'tvg-logo="([^"]*)"', entry["extinf"])
        channel = channels.setdefault(
            key,
            dict(
                id=key,
                name=entry["name"],
                category=entry["category"],
                logo=logo.group(1)
                if logo and logo.group(1).startswith(("https://", "http://"))
                else "",
                sources=[],
            ),
        )
        if any(s["url"] == entry["url"] for s in channel["sources"]):
            continue
        info = metrics.get((key, entry["url"]), {})
        channel["sources"].append(
            dict(
                url=entry["url"],
                options=entry["options"],
                resolution=info.get("resolution"),
                delay=info.get("delay")
                if isinstance(info.get("delay"), (int, float))
                and math.isfinite(info["delay"])
                else None,
            )
        )
    return list(channels.values())


def validate_remote(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("预览转发只支持公开的 HTTP / HTTPS 地址")
    if any(ord(char) < 32 for char in url):
        raise ValueError("地址格式无效")
    addresses = socket.getaddrinfo(
        parsed.hostname,
        parsed.port or (443 if parsed.scheme == "https" else 80),
        type=socket.SOCK_STREAM,
    )
    if not addresses or any(
        not ipaddress.ip_address(a[4][0]).is_global for a in addresses
    ):
        raise ValueError("预览转发不访问内网地址；内网源请使用直连模式")
    return url


class Relay:
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions = {}
        self.slots = threading.BoundedSemaphore(8)

    def stop(self, token):
        with self.lock:
            session = self.sessions.pop(token, None)
            if session:
                session["stopped"] = True
                for response in list(session["responses"]):
                    response.close()

    def start(self, source, owner):
        validate_remote(source["url"])
        with self.lock:
            now = time.monotonic()
            for token, session in list(self.sessions.items()):
                if (
                    session["owner"] == owner
                    or now - session["used"] > 60
                    or now - session["created"] > 1800
                ):
                    self.stop(token)
            if len(self.sessions) >= 2:
                raise ValueError("已有两个预览正在转发，请先停止其中一个")
            headers = {"User-Agent": "TV-Suite-Preview/1.0"}
            for option in source.get("options", []):
                for name, header in (
                    ("http-user-agent", "User-Agent"),
                    ("http-referrer", "Referer"),
                ):
                    prefix = "#EXTVLCOPT:" + name + "="
                    if option.startswith(prefix) and not any(
                        c in option for c in "\r\n"
                    ):
                        headers[header] = option[len(prefix) :]
            token = secrets.token_urlsafe(24)
            self.sessions[token] = dict(
                owner=owner,
                created=now,
                used=now,
                refs={"0": source["url"]},
                headers=headers,
                responses=set(),
                stopped=False,
            )
            return f"/api/preview/relay/{token}/0", token

    def ref(self, token, session, url):
        with self.lock:
            for key, value in session["refs"].items():
                if value == url:
                    return f"/api/preview/relay/{token}/{key}"
            if len(session["refs"]) >= 12000:
                raise ValueError("预览会话已达到资源上限，请重新播放")
            key = secrets.token_urlsafe(12)
            session["refs"][key] = url
            return f"/api/preview/relay/{token}/{key}"

    def rewrite(self, text, base, token, session):
        lines = []
        for line in text.splitlines():
            if line.startswith("#"):
                line = re.sub(
                    r'URI="([^"]+)"',
                    lambda m: (
                        'URI="' + self.ref(token, session, urljoin(base, m[1])) + '"'
                    ),
                    line,
                )
            elif line.strip():
                line = self.ref(token, session, urljoin(base, line.strip()))
            lines.append(line)
        return "\n".join(lines) + "\n"

    def fetch(self, token, ref):
        with self.lock:
            session = self.sessions.get(token)
            now = time.monotonic()
            if (
                not session
                or now - session["used"] > 60
                or now - session["created"] > 1800
            ):
                self.stop(token)
                return jsonify(error="预览会话已结束，请重新播放"), 410
            if ref not in session["refs"]:
                return jsonify(error="资源不存在"), 404
            url = session["refs"][ref]
            session["used"] = now
        if not self.slots.acquire(blocking=False):
            return jsonify(error="预览请求繁忙"), 429
        response = None
        handed_off = False
        try:
            headers = dict(session["headers"])
            byte_range = request.headers.get("Range")
            if byte_range:
                if (
                    not re.fullmatch(r"bytes=\d*-\d*", byte_range)
                    or byte_range == "bytes=-"
                ):
                    raise ValueError("不支持的 Range 请求")
                headers["Range"] = byte_range
            for _ in range(5):
                validate_remote(url)
                response = requests.get(
                    url,
                    headers=headers,
                    stream=True,
                    allow_redirects=False,
                    timeout=(4, 10),
                )
                with self.lock:
                    if session["stopped"]:
                        response.close()
                        return "", 410
                    session["responses"].add(response)
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                location = response.headers.get("Location")
                response.close()
                with self.lock:
                    session["responses"].discard(response)
                if not location:
                    raise ValueError("上游重定向缺少地址")
                url = urljoin(url, location)
            else:
                raise ValueError("上游重定向过多")
            if response.status_code not in {200, 206}:
                return jsonify(error="上游暂时无法播放"), 502
            content_type = response.headers.get(
                "Content-Type", "application/octet-stream"
            ).split(";")[0]
            iterator = response.iter_content(64 * 1024)
            first = next(iterator, b"")
            if first.lstrip().startswith(b"#EXTM3U"):
                data = bytearray(first)
                for chunk in iterator:
                    data.extend(chunk)
                    if len(data) > 2 * 1024 * 1024:
                        raise ValueError("播放列表过大")
                return Response(
                    self.rewrite(data.decode("utf-8-sig"), url, token, session),
                    mimetype="application/vnd.apple.mpegurl",
                )

            closed = False

            def cleanup():
                nonlocal closed
                with self.lock:
                    if closed:
                        return
                    closed = True
                    session["responses"].discard(response)
                response.close()
                self.slots.release()

            def body():
                total = len(first)
                try:
                    yield first
                    for chunk in iterator:
                        total += len(chunk)
                        if session["stopped"] or total > 32 * 1024 * 1024:
                            break
                        yield chunk
                finally:
                    cleanup()

            result = Response(
                stream_with_context(body()),
                status=response.status_code,
                content_type=content_type,
            )
            for name in ("Content-Range", "Accept-Ranges"):
                if name in response.headers:
                    result.headers[name] = response.headers[name]
            result.call_on_close(cleanup)
            handed_off = True
            return result
        except (requests.RequestException, OSError, UnicodeError) as exc:
            return jsonify(error="预览读取失败：" + type(exc).__name__), 502
        finally:
            if not handed_off:
                if response is not None:
                    response.close()
                    with self.lock:
                        session["responses"].discard(response)
                self.slots.release()


def register_preview(app, store, csrf):
    relay = Relay()
    app.extensions["preview_relay"] = relay

    @app.get("/preview")
    def preview():
        return render_template("preview.html")

    @app.get("/api/preview/channels")
    def preview_channels():
        return jsonify(channels=catalog(store), csrf=csrf)

    @app.post("/api/preview/start")
    def preview_start():
        body = request.get_json()
        owner = body.get("owner")
        if not isinstance(owner, str) or not 16 <= len(owner) <= 100:
            raise ValueError("预览窗口标识无效")
        channel = next(
            (c for c in catalog(store) if c["id"] == body.get("channel")), None
        )
        index = body.get("source", 0)
        if (
            not channel
            or not isinstance(index, int)
            or not 0 <= index < len(channel["sources"])
        ):
            raise ValueError("频道或线路已变化，请刷新列表")
        url, token = relay.start(channel["sources"][index], owner)
        return jsonify(url=url, token=token)

    @app.post("/api/preview/stop")
    def preview_stop():
        token = request.get_json().get("token")
        if not isinstance(token, str):
            raise ValueError("预览会话标识无效")
        relay.stop(token)
        return jsonify(ok=True)

    @app.get("/api/preview/relay/<token>/<ref>")
    def preview_relay(token, ref):
        return relay.fetch(token, ref)
