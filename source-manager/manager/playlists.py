import hashlib
import re
import sqlite3
from pathlib import Path


def channel_key(group, name):
    return hashlib.sha256(f"{group.strip()}\x1f{name.strip()}".encode()).hexdigest()[
        :24
    ]


def parse_playlist(text):
    entries = []
    metadata = None
    options = []
    group = "订阅频道"
    is_m3u = "#EXTM3U" in text.upper()
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if line.startswith("#EXTINF:"):
            quoted = False
            separator = -1
            for index, char in enumerate(line):
                if char == '"':
                    quoted = not quoted
                elif char == "," and not quoted:
                    separator = index
                    break
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
            metadata = {
                "name": line[separator + 1 :].strip()
                if separator >= 0
                else attrs.get("tvg-name", ""),
                "category": attrs.get("group-title", "订阅频道"),
                "extinf": line,
            }
            options = []
        elif line.startswith("#EXTVLCOPT:") or line.startswith("#KODIPROP:"):
            options.append(line)
        elif metadata and line.startswith(
            ("http://", "https://", "rtsp://", "rtmp://")
        ):
            entries.append({**metadata, "url": line, "options": list(options)})
            metadata = None
        elif not is_m3u and "," in line and not line.startswith("#"):
            name, url = line.split(",", 1)
            if url.strip() == "#genre#":
                group = name.strip()
            elif url.strip().startswith(("http://", "https://", "rtsp://", "rtmp://")):
                name, url = name.strip(), url.strip()
                entries.append(
                    {
                        "name": name,
                        "category": group,
                        "url": url,
                        "extinf": f'#EXTINF:-1 group-title="{group}",{name}',
                        "options": [],
                    }
                )
    return entries


def filter_playlist(text, rules):
    blocked = {r["target"] for r in rules if r["kind"] == "url"}
    hidden = {r["target"] for r in rules if r["kind"] == "channel"}
    entries = [
        e
        for e in parse_playlist(text)
        if e["url"] not in blocked
        and channel_key(e["category"], e["name"]) not in hidden
    ]
    lines = ["#EXTM3U"]
    txt = []
    for entry in entries:
        lines.extend([entry["extinf"], *entry["options"], entry["url"]])
        txt.append(entry["name"] + "," + entry["url"])
    return "\n".join(lines) + "\n", "\n".join(txt) + "\n", entries


def read_channels(db_path, fallback=""):
    path = Path(db_path)
    if not path.exists():
        channels = {}
        for e in parse_playlist(fallback):
            key = channel_key(e["category"], e["name"])
            channel = channels.setdefault(
                key,
                {
                    "channel_key": key,
                    "name": e["name"],
                    "category": e["category"],
                    "results": [],
                },
            )
            channel["results"].append(
                {"url": e["url"], "valid": 1, "test_status": "imported"}
            )
        return list(channels.values())
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        channels = [
            dict(r) for r in db.execute("SELECT * FROM channels ORDER BY category,name")
        ]
        for channel in channels:
            channel["results"] = [
                dict(r)
                for r in db.execute(
                    "SELECT url,speed,delay,resolution,valid,test_status,error_type,selected_rank FROM channel_results WHERE channel_key=? ORDER BY valid DESC,speed DESC",
                    (channel["channel_key"],),
                )
            ]
        return channels
    except sqlite3.OperationalError:
        return read_channels(path.with_name("missing.db"), fallback)
    finally:
        db.close()
