import hashlib
import html
import json
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import requests

from manager.playlists import channel_key, parse_playlist
from manager.preview import validate_remote
from manager.store import atomic_write

DEFAULTS = {
    "enabled": False,
    "interval": 24,
    "auto_import": True,
    "providers": ["iptv-org", "free-tv", "aptv"],
    "countries": ["hk", "mo", "tw"],
    "languages": ["zho", "eng"],
    "probe_budget": 40,
    "new_channel_limit": 15,
    "max_channels": 100,
    "probe_timeout": 8,
}
PROVIDERS = {
    "iptv-org": {
        "name": "IPTV-org",
        "url": "https://raw.githubusercontent.com/iptv-org/iptv/master/PLAYLISTS.md",
    },
    "free-tv": {
        "name": "Free-TV",
        "url": "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8",
    },
    "aptv": {"name": "APTV 推荐页", "url": "https://profile.aptv.app/"},
}
REGIONS = {"hk": "香港", "mo": "澳门", "tw": "台湾"}
LANGUAGES = {
    "zho": "中文",
    "eng": "英语",
    "jpn": "日语",
    "fra": "法语",
    "kor": "韩语",
    "spa": "西班牙语",
}


def validate_config(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError("发现设置包含未知字段")
    result = {**DEFAULTS, **value}
    for key in ("enabled", "auto_import"):
        if not isinstance(result[key], bool):
            raise ValueError("启用设置必须为布尔值")
    for key, low, high in (
        ("interval", 1, 168),
        ("probe_budget", 1, 100),
        ("new_channel_limit", 1, 30),
        ("max_channels", 1, 300),
        ("probe_timeout", 2, 15),
    ):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"{key} 应为 {low}–{high} 之间的整数")
    for key, choices in (
        ("providers", PROVIDERS),
        ("countries", REGIONS),
        ("languages", LANGUAGES),
    ):
        if (
            not isinstance(result[key], list)
            or len(result[key]) != len(set(map(str, result[key])))
            or any(not isinstance(v, str) or v not in choices for v in result[key])
        ):
            raise ValueError(f"{key} 包含不支持或重复的选项")
    if not result["providers"]:
        raise ValueError("至少选择一个资源目录")
    return result


def config(store):
    return {**DEFAULTS, **store.get("discovery_config", {})}


def canonical(url):
    url = html.unescape(unquote(url)).strip().rstrip(".,，;；")
    if urlsplit(url).hostname == "add.aptv.app":
        url = parse_qs(urlsplit(url).query).get("url", [url])[0]
    for prefix in (
        "https://add.aptv.app/",
        "https://gh.aptv.app/",
        "https://cdn.aptv.app/",
    ):
        if url.startswith(prefix):
            url = url[len(prefix) :]
    if url.startswith("https://cdn.jsdelivr.net/gh/"):
        match = re.match(
            r"https://cdn.jsdelivr.net/gh/([^/]+/[^/@]+)@([^/]+)/(.*)", url
        )
        if match:
            url = f"https://raw.githubusercontent.com/{match[1]}/{match[2]}/{match[3]}"
    if url.startswith("https://raw.githubusercontent.com/"):
        url = url.replace("/refs/heads/", "/")
    return url


def name_key(name):
    name = name.translate(str.maketrans("鳳衛視資訊臺", "凤卫视资讯台"))
    key = re.sub(r"[^\w+]", "", name.casefold()).replace("_", "")
    match = re.match(r"cctv(\d{1,2})(\+|plus)?(.*)$", key)
    if match and not match[3].startswith("k"):
        return "cctv" + match[1] + ("+" if match[2] else "")
    if key.startswith("凤凰"):
        return key.replace("卫视", "").removesuffix("台")
    return {
        "cgtnenglish": "cgtn英语",
        "cgtndocumentary": "cgtn纪录",
        "cgtnfrench": "cgtn法语",
        "cgtnspanish": "cgtn西语",
        "cgtnarabic": "cgtn阿语",
        "cgtnrussian": "cgtn俄语",
    }.get(key, key)


def clean_name(name):
    return re.sub(
        r"\s*[\[(](?:\d{3,4}p|\d{3,4}i|geo-blocked|not 24/7)[\])]", "", name, flags=re.I
    ).strip()


class Fetcher:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["User-Agent"] = "TVSuite-Discovery/1.0"

    def get(self, url):
        path = self.directory / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        cached = json.loads(path.read_text()) if path.exists() else {}
        headers = {}
        if cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        if cached.get("modified"):
            headers["If-Modified-Since"] = cached["modified"]
        target = url
        for _ in range(5):
            validate_remote(target)
            with self.session.get(
                target,
                headers=headers,
                stream=True,
                allow_redirects=False,
                timeout=(4, 8),
            ) as response:
                if response.status_code == 304 and "text" in cached:
                    return cached["text"], False
                if response.status_code in {301, 302, 303, 307, 308}:
                    from urllib.parse import urljoin

                    target = urljoin(target, response.headers.get("Location", ""))
                    headers = {}
                    continue
                response.raise_for_status()
                data = bytearray()
                started = time.monotonic()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    limit = (
                        20
                        if url.startswith("https://iptv-org.github.io/iptv/languages/")
                        else 5
                    )
                    if len(data) > limit * 1024 * 1024:
                        raise ValueError(f"资源超过 {limit} MB")
                    if time.monotonic() - started > 35:
                        raise ValueError("资源下载超时")
                text = data.decode("utf-8-sig", errors="replace")
                changed = text != cached.get("text")
                atomic_write(
                    path,
                    json.dumps(
                        {
                            "text": text,
                            "etag": response.headers.get("ETag"),
                            "modified": response.headers.get("Last-Modified"),
                        },
                        ensure_ascii=False,
                    ),
                )
                return text, changed
        raise ValueError("资源重定向过多")


def find_playlists(provider, text, settings):
    if provider == "free-tv":
        return [(PROVIDERS[provider]["url"], "Free-TV · 港澳台与国际", "free-tv")]
    if provider == "iptv-org":
        found = set(
            re.findall(
                r"https://iptv-org\.github\.io/iptv/(?:countries|languages)/[a-z]+\.m3u",
                text,
            )
        )
        wanted = [
            (
                f"https://iptv-org.github.io/iptv/countries/{code}.m3u",
                "IPTV-org · " + REGIONS[code],
                "country:" + code,
            )
            for code in settings["countries"]
        ]
        wanted += [
            (
                f"https://iptv-org.github.io/iptv/languages/{code}.m3u",
                "IPTV-org · " + LANGUAGES[code],
                "language:" + code,
            )
            for code in settings["languages"]
        ]
        return [item for item in wanted if item[0] in found]
    text = html.unescape(text).split("中国移动内网源")[0]
    urls = re.findall(r'https?://[^\s<>"\x27]+?\.m3u8?(?:\?[^\s<>"\x27]*)?', text)
    return list(
        dict.fromkeys(
            (canonical(u), "APTV 推荐 · " + urlsplit(canonical(u)).hostname, "aptv")
            for u in urls
            if urlsplit(canonical(u)).scheme in {"http", "https"}
        )
    )[:10]


def category_for(entry, scope, settings):
    if scope.startswith("country:"):
        return "发现·" + REGIONS[scope.split(":")[1]]
    if scope.startswith("language:"):
        return "发现·" + LANGUAGES[scope.split(":")[1]]
    if scope == "free-tv":
        group = entry["category"].lower()
        for code, names in {
            "hk": ("hong kong", "香港"),
            "mo": ("macau", "macao", "澳门"),
            "tw": ("taiwan", "台湾"),
        }.items():
            if code in settings["countries"] and any(n in group for n in names):
                return "发现·" + REGIONS[code]
        if "eng" in settings["languages"] and re.search(
            r"BBC.*News|Sky News|France 24.*English|DW.*English|Al Jazeera.*English|TRT World|Bloomberg",
            entry["name"],
            re.I,
        ):
            return "发现·英语"
        return None
    if re.search(
        r"虎牙|斗鱼|Bili|哔哩|歌曲|点播|轮播|春晚|一起看", entry["category"], re.I
    ):
        return None
    return "发现·中文" if "zho" in settings["languages"] else None


def probe(entry, timeout, minimum, filter_resolution):
    try:
        validate_remote(entry["url"])
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "http,https,tcp,tls,crypto",
                "-rw_timeout",
                str(timeout * 1000000),
                "-analyzeduration",
                "1500000",
                "-probesize",
                "750000",
                "-show_entries",
                "stream=codec_type,codec_name,width,height",
                "-of",
                "json",
                entry["url"],
            ],
            capture_output=True,
            timeout=timeout,
        )
        streams = json.loads(result.stdout or b"{}").get("streams", [])
        video = next(
            (
                s
                for s in streams
                if s.get("codec_type") == "video"
                and s.get("width", 0)
                and s.get("height", 0)
            ),
            None,
        )
        if result.returncode or not video:
            return {"valid": False, "error": "未读取到可用视频轨"}
        width, height = video["width"], video["height"]
        low_w, low_h = map(int, minimum.split("x"))
        if filter_resolution and width * height < low_w * low_h:
            return {"valid": False, "error": "低于最低分辨率"}
        return {
            "valid": True,
            "resolution": f"{width}x{height}",
            "codec": video.get("codec_name"),
            "error": "",
        }
    except (ValueError, OSError, subprocess.SubprocessError, StopIteration):
        return {"valid": False, "error": "连接超时或无法探测视频"}


def scan(snapshot, directory, report=lambda message: print(message, flush=True)):
    snapshot = {
        **snapshot,
        "sources": [{**s, "url": canonical(s["url"])} for s in snapshot["sources"]],
    }
    settings = snapshot["discovery_config"]
    now = time.time()
    fetcher = Fetcher(Path(directory) / "http")
    known = {row["url"]: row for row in snapshot.get("discovery_sources", [])}
    sources, queues, provider_errors = [], [], []
    seen_urls, digests = set(), {}
    for provider in settings["providers"]:
        try:
            text, directory_changed = fetcher.get(PROVIDERS[provider]["url"])
            report("读取资源目录：" + PROVIDERS[provider]["name"])
            for url, label, scope in find_playlists(provider, text, settings):
                url = canonical(url)
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                row = {
                    "url": url,
                    "provider": provider,
                    "name": label,
                    "scope": scope,
                    "checked_at": now,
                    "previous_source_id": next(
                        (s["id"] for s in snapshot["sources"] if s["url"] == url), None
                    ),
                    "trusted": provider != "aptv"
                    or urlsplit(url).hostname
                    in {
                        "raw.githubusercontent.com",
                        "iptv.445569.xyz",
                        "iptv.yang-1989.xyz",
                    },
                }
                try:
                    content, changed = (
                        (text, True) if provider == "free-tv" else fetcher.get(url)
                    )
                    entries = parse_playlist(content)
                    if not entries:
                        raise ValueError("未解析出频道列表")
                    digest = hashlib.sha256(
                        "\n".join(sorted({e["url"] for e in entries})).encode()
                    ).hexdigest()
                    row.update(
                        status="ok",
                        candidates=len(entries),
                        digest=digest,
                        changed=changed,
                        error="",
                    )
                    if digest in digests:
                        row.update(status="duplicate", duplicate_of=digests[digest])
                    else:
                        digests[digest] = url
                        entries.sort(
                            key=lambda entry: (
                                not bool(
                                    re.search(
                                        r"news|新闻|新聞|RTHK|港台|翡翠|明珠|TVB|TDM|澳视|澳視|公视|公視|CNA|BBC|Al Jazeera",
                                        entry["name"],
                                        re.I,
                                    )
                                ),
                                entry["name"],
                            )
                        )
                        queues.append((row, entries))
                except (requests.RequestException, ValueError, OSError):
                    row.update(
                        status="failed",
                        candidates=0,
                        error="列表下载失败、超时或格式无效",
                    )
                sources.append(row)
        except (requests.RequestException, ValueError, OSError):
            provider_errors.append(
                {"provider": provider, "error": "目录下载失败；保留已有入口"}
            )
    report(f"发现 {len(sources)} 个入口，开始整理频道")
    original_names = {
        name_key(line.strip())
        for line in snapshot.get("channel_catalog", "").splitlines()
        if line.strip() and "#genre#" not in line and not line.startswith("#")
    }
    records = {row["id"]: row for row in snapshot.get("discovery_candidates", [])}
    source_state = {s["id"]: s for s in snapshot["sources"]}
    observed_urls = {e["url"] for _, entries in queues for e in entries}
    represented, per_name = {}, {}
    for row in records.values():
        per_name[row["name_key"]] = per_name.get(row["name_key"], 0) + 1
    # Interleave lists so a large language playlist cannot consume the whole discovery budget.
    for index in range(max((len(q[1]) for q in queues), default=0)):
        for source, entries in queues:
            if index >= len(entries):
                continue
            old = known.get(source["url"])
            existing = next(
                (s for s in snapshot["sources"] if s["url"] == source["url"]), None
            )
            approved = bool(existing and existing["enabled"]) or (
                settings["auto_import"]
                and source.get("trusted", True)
                and not (old or {}).get("source_id")
                and not existing
            )
            if old and old.get("source_id") and not source_state.get(old["source_id"]):
                approved = False
            if not approved:
                continue
            entry = entries[index]
            name = clean_name(entry["name"])
            key = name_key(name)
            group = category_for(entry, source["scope"], settings)
            if (
                not group
                or not key
                or key in original_names
                or len(name) > 100
                or any(c in name for c in "\r\n,#")
            ):
                continue
            if not entry["url"].startswith(("https://", "http://")):
                continue
            id = hashlib.sha256((key + "\x1f" + entry["url"]).encode()).hexdigest()[:24]
            if key not in per_name and len(per_name) >= settings["max_channels"] * 3:
                continue
            if id not in records and per_name.get(key, 0) >= 2:
                stale = sorted(
                    (
                        r
                        for r in records.values()
                        if r["name_key"] == key and r["url"] not in observed_urls
                    ),
                    key=lambda r: (
                        bool(r.get("valid")),
                        r.get("last_seen", r["first_seen"]),
                    ),
                )
                if not stale:
                    continue
                records.pop(stale[0]["id"])
                per_name[key] -= 1
            if id not in records and len(records) >= 1000:
                continue
            if id not in records:
                records[id] = {
                    "id": id,
                    "name": name,
                    "name_key": key,
                    "category": group,
                    "url": entry["url"],
                    "source_url": source["url"],
                    "provider": source["provider"],
                    "first_seen": now,
                    "checked_at": None,
                    "next_check": 0,
                    "valid": False,
                    "failures": 0,
                    "error": "等待检测",
                    "extinf": entry["extinf"],
                    "options": entry["options"],
                }
                per_name[key] = per_name.get(key, 0) + 1
            records[id]["last_seen"] = now
            records[id]["missing"] = 0
            represented[id] = True
    downloaded = {source["url"] for source, _ in queues}
    for row in records.values():
        if row["source_url"] in downloaded and row["id"] not in represented:
            row["missing"] = row.get("missing", 0) + 1
            if row["missing"] >= 3:
                row.update(valid=False, error="连续三次未在上游列表出现")
    records = {
        id: row
        for id, row in records.items()
        if row.get("valid") or now - row.get("last_seen", row["first_seen"]) < 7 * 86400
    }
    active_names = {r["name_key"] for r in records.values() if r.get("valid")}
    blocked = {r["target"] for r in snapshot.get("rules", []) if r["kind"] == "url"}
    hidden = {r["target"] for r in snapshot.get("rules", []) if r["kind"] == "channel"}
    approved_urls = {s["url"] for s in snapshot["sources"] if s["enabled"]}
    selected_categories = {"发现·" + REGIONS[c] for c in settings["countries"]} | {
        "发现·" + LANGUAGES[c] for c in settings["languages"]
    }
    due = sorted(
        (
            r
            for r in records.values()
            if (r["id"] in represented or r["source_url"] in approved_urls)
            and r["provider"] in settings["providers"]
            and r["category"] in selected_categories
            and r.get("missing", 0) < 3
            and r.get("next_check", 0) <= now
            and r["url"] not in blocked
            and channel_key(r["category"], r["name"]) not in hidden
        ),
        key=lambda r: (
            0 if r.get("valid") else 1,
            r.get("checked_at") or 0,
            r["first_seen"],
        ),
    )
    checked, added_names = 0, set()
    for row in due:
        if checked >= settings["probe_budget"]:
            break
        is_new = row["name_key"] not in active_names
        if is_new and (
            len(active_names) >= settings["max_channels"]
            or len(added_names) >= settings["new_channel_limit"]
        ):
            continue
        if any(
            r["valid"]
            and not r.get("error")
            and r["id"] in represented
            and r["name_key"] == row["name_key"]
            and r["id"] != row["id"]
            for r in records.values()
        ):
            continue
        result = probe(
            row,
            settings["probe_timeout"],
            snapshot["settings"]["min_resolution"],
            snapshot["settings"]["open_filter_resolution"],
        )
        checked += 1
        row.update(checked_at=now, error=result.get("error", ""))
        if result["valid"]:
            row.update(
                valid=True,
                failures=0,
                resolution=result.get("resolution"),
                next_check=now + 12 * 3600,
            )
            if is_new:
                active_names.add(row["name_key"])
                added_names.add(row["name_key"])
        else:
            row["failures"] += 1
            row["next_check"] = now + (24 if row["failures"] >= 3 else 2) * 3600
            if row["failures"] >= 3:
                row["valid"] = False
        report(
            f"检测 {checked}/{settings['probe_budget']}：{row['name']} · {'通过' if result['valid'] else row['error']}"
        )
    return {
        "discovery": True,
        "sources": sources,
        "records": list(records.values()),
        "provider_errors": provider_errors,
        "checked": checked,
        "added_channels": len(added_names),
        "valid_channels": len({r["name_key"] for r in records.values() if r["valid"]}),
    }


def schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS discovery_sources(url TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS discovery_candidates(id TEXT PRIMARY KEY, data TEXT NOT NULL);
    """)


def rows(store, table):
    assert table in {"discovery_sources", "discovery_candidates"}
    with store.connect() as db:
        return [
            json.loads(r[0])
            for r in db.execute(f"SELECT data FROM {table} ORDER BY rowid")
        ]


def apply_result(store, result, job_id):
    settings = config(store)
    now = time.time()
    with store.connect() as db:
        for row in result["sources"]:
            previous = db.execute(
                "SELECT data FROM discovery_sources WHERE url=?", (row["url"],)
            ).fetchone()
            old = json.loads(previous[0]) if previous else {}
            row = {**old, **row}
            found = next(
                (
                    r
                    for r in db.execute("SELECT id,url FROM sources")
                    if canonical(r["url"]) == row["url"]
                ),
                None,
            )
            if found:
                row["source_id"] = found[0]
            elif (
                not old.get("source_id")
                and not row.get("previous_source_id")
                and row["status"] == "ok"
                and settings["auto_import"]
                and row.get("trusted", True)
            ):
                row["source_id"] = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO sources(id,name,url,enabled,checked_at,status,candidates,error) VALUES (?,?,?,1,?,'ok',?,'')",
                    (row["source_id"], row["name"], row["url"], now, row["candidates"]),
                )
            row["passed"] = sum(
                bool(r.get("valid"))
                for r in result["records"]
                if r["source_url"] == row["url"]
            )
            row["job_id"] = job_id
            db.execute(
                "INSERT OR REPLACE INTO discovery_sources VALUES (?,?)",
                (row["url"], json.dumps(row, ensure_ascii=False)),
            )
        db.execute("DELETE FROM discovery_candidates")
        for row in result["records"]:
            db.execute(
                "INSERT OR REPLACE INTO discovery_candidates VALUES (?,?)",
                (row["id"], json.dumps(row, ensure_ascii=False)),
            )
        summary = {
            key: result[key]
            for key in (
                "checked",
                "added_channels",
                "valid_channels",
                "provider_errors",
            )
        }
        summary.update(job_id=job_id, finished_at=now)
        db.execute(
            "INSERT OR REPLACE INTO meta VALUES ('discovery_last',?)",
            (json.dumps(summary),),
        )


def published_entries(store):
    settings = config(store)
    available = {canonical(s["url"]) for s in store.sources() if s["enabled"]}
    enabled = set()
    for source in rows(store, "discovery_sources"):
        scope = source.get("scope", "")
        if source["provider"] not in settings["providers"]:
            continue
        if (
            scope.startswith("country:")
            and scope.split(":")[1] not in settings["countries"]
        ):
            continue
        if (
            scope.startswith("language:")
            and scope.split(":")[1] not in settings["languages"]
        ):
            continue
        if source["url"] in available:
            enabled.add(source["url"])
    categories = {"发现·" + REGIONS[c] for c in settings["countries"]} | {
        "发现·" + LANGUAGES[c] for c in settings["languages"]
    }
    entries, seen = [], set()
    for row in sorted(
        rows(store, "discovery_candidates"),
        key=lambda r: (r.get("failures", 0), -(r.get("checked_at") or 0)),
    ):
        if (
            not row.get("valid")
            or row["category"] not in categories
            or row["source_url"] not in enabled
            or row["name_key"] in seen
        ):
            continue
        seen.add(row["name_key"])
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', row.get("extinf", "")))
        logo = attrs.get("tvg-logo", "").replace('"', "")
        entries.append(
            {
                **row,
                "extinf": f'#EXTINF:-1 tvg-logo="{logo}" group-title="{row["category"]}",{row["name"]}',
                "options": row.get("options", []),
            }
        )
    return entries


def channel_rows(store):
    grouped = {}
    for row in rows(store, "discovery_candidates"):
        key = channel_key(row["category"], row["name"])
        grouped.setdefault(
            key,
            {
                "channel_key": key,
                "name": row["name"],
                "category": row["category"],
                "results": [],
            },
        )["results"].append(
            {
                "url": row["url"],
                "valid": row["valid"],
                "resolution": row.get("resolution"),
                "test_status": "discovery",
                "error_type": row.get("error"),
                "selected_rank": 1 if row["valid"] else None,
            }
        )
    return list(grouped.values())


def state(store):
    records = rows(store, "discovery_candidates")
    sources = rows(store, "discovery_sources")
    existing = {s["id"]: s for s in store.sources()}
    for source in sources:
        source["enabled"] = bool(
            existing.get(source.get("source_id"), {}).get("enabled")
        )
        source["removed"] = bool(
            source.get("source_id") and source["source_id"] not in existing
        )
    return {
        "config": config(store),
        "providers": PROVIDERS,
        "countries": REGIONS,
        "languages": LANGUAGES,
        "sources": sources,
        "channels": [
            {
                k: r.get(k)
                for k in (
                    "id",
                    "name",
                    "category",
                    "provider",
                    "checked_at",
                    "next_check",
                    "valid",
                    "failures",
                    "error",
                    "resolution",
                )
            }
            for r in sorted(records, key=lambda r: (not r["valid"], r["name"]))[:200]
        ],
        "counts": {
            "candidates": len(records),
            "valid": len({r["name_key"] for r in records if r["valid"]}),
            "cooling": sum(r.get("failures", 0) >= 3 for r in records),
        },
        "last": store.get("discovery_last"),
        "next_run": store.get("discovery_next", 0),
    }
