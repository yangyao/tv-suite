import contextlib
import json
import os
import secrets
import shlex
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_SETTINGS = {
    "channel_template": "",
    "update_mode": "interval",
    "update_interval": 12,
    "update_times": "06:00,18:00",
    "update_startup": True,
    "speed_test_limit": 3,
    "speed_test_timeout": 10,
    "output_urls_limit": 3,
    "open_filter_resolution": True,
    "min_resolution": "320x180",
    "open_filter_speed": True,
    "min_speed": 0.1,
    "ipv_type": "all",
}


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        with open(temporary, "w", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_source(value):
    if not isinstance(value, dict):
        raise ValueError("订阅信息格式错误")
    url = str(value.get("url", "")).strip()
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.port != 0
        )
    except ValueError:
        valid = False
    if not valid or len(url) > 4096 or any(c.isspace() for c in url):
        raise ValueError("请输入有效的 HTTP 或 HTTPS 订阅地址，不能包含空白字符")
    name = str(value.get("name", "")).strip() or (
        " / ".join(parsed.path.strip("/").split("/")[:2])
        if parsed.hostname == "raw.githubusercontent.com"
        else parsed.hostname
    )
    ua = str(value.get("user_agent", "")).strip()
    if len(name) > 120 or len(ua) > 512 or any(c in ua for c in "\r\n"):
        raise ValueError("名称或 User-Agent 格式不正确")
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("启用状态必须为布尔值")
    return {"name": name, "url": url, "user_agent": ua, "enabled": enabled}


def validate_settings(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_SETTINGS):
        raise ValueError("包含未知设置")
    result = dict(DEFAULT_SETTINGS)
    result.update(value)
    for key, minimum, maximum in [
        ("update_interval", 0.25, 168),
        ("speed_test_limit", 1, 16),
        ("speed_test_timeout", 2, 60),
        ("output_urls_limit", 1, 10),
        ("min_speed", 0, 20),
    ]:
        number = result[key]
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not minimum <= number <= maximum
        ):
            raise ValueError(f"{key} 应在 {minimum}–{maximum} 之间")
        if (
            key in {"speed_test_limit", "speed_test_timeout", "output_urls_limit"}
            and int(number) != number
        ):
            raise ValueError(f"{key} 必须是整数")
    for key in ("update_startup", "open_filter_resolution", "open_filter_speed"):
        if not isinstance(result[key], bool):
            raise ValueError(f"{key} 必须为布尔值")
    if result["update_mode"] not in {"interval", "time"} or result["ipv_type"] not in {
        "ipv4",
        "ipv6",
        "all",
    }:
        raise ValueError("更新模式或网络类型无效")
    import re

    if not re.fullmatch(r"[1-9]\d{1,4}x[1-9]\d{1,4}", str(result["min_resolution"])):
        raise ValueError("分辨率格式应为 320x180")
    times = str(result["update_times"]).split(",")
    if not 1 <= len(times) <= 12 or any(
        not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t.strip()) for t in times
    ):
        raise ValueError("更新时间应为 HH:MM，多项用英文逗号分隔")
    template = result["channel_template"]
    if (
        not isinstance(template, str)
        or len(template) > 65536
        or len(template.splitlines()) > 2000
    ):
        raise ValueError("频道目录最多 2000 行、64 KB")
    if template.strip() and not any(
        line.strip() and not line.strip().startswith("#") and "#genre#" not in line
        for line in template.splitlines()
    ):
        raise ValueError("频道目录至少需要一个频道名称")
    result["update_times"] = ",".join(t.strip() for t in times)
    return result


class Store:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "manager.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
                    user_agent TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
                    checked_at REAL, status TEXT, candidates INTEGER, passed INTEGER,
                    failures INTEGER NOT NULL DEFAULT 0, error TEXT);
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_id TEXT, status TEXT NOT NULL,
                    created_at REAL NOT NULL, started_at REAL, finished_at REAL, cancel INTEGER DEFAULT 0,
                    phase TEXT, current INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
                    message TEXT, summary TEXT, snapshot TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs((1))
                    WHERE status IN ('queued','running');
                CREATE TABLE IF NOT EXISTS rules(
                    kind TEXT NOT NULL, target TEXT NOT NULL, PRIMARY KEY(kind,target));
            """)
            for key, value in [
                ("settings", DEFAULT_SETTINGS),
                ("next_run", time.time()),
            ]:
                db.execute(
                    "INSERT OR IGNORE INTO meta VALUES (?,?)", (key, json.dumps(value))
                )
        self.import_sources()

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value))
            )

    def sources(self):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute("SELECT * FROM sources ORDER BY name,id")
            ]

    def save_source(self, value, source_id=None):
        source = validate_source(value)
        with self.connect() as db:
            if source_id:
                cursor = db.execute(
                    "UPDATE sources SET name=?,url=?,user_agent=?,enabled=? WHERE id=?",
                    (*source.values(), source_id),
                )
                if not cursor.rowcount:
                    raise KeyError("入口不存在")
            else:
                source_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO sources(id,name,url,user_agent,enabled) VALUES (?,?,?,?,?)",
                    (source_id, *source.values()),
                )
        return source_id

    def import_sources(self):
        if self.get("imported"):
            return
        file = self.root / "config/subscribe.txt"
        if file.exists():
            for line in file.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                enabled = not line.startswith("#")
                if not enabled:
                    line = line.lstrip("# ")
                if not line.startswith(("https://", "http://")):
                    continue
                try:
                    parts = shlex.split(line)
                    ua = next((p[3:] for p in parts[1:] if p.startswith("UA=")), "")
                    self.save_source(
                        {"url": parts[0], "user_agent": ua, "enabled": enabled}
                    )
                except (ValueError, sqlite3.IntegrityError):
                    continue
        self.set("imported", True)

    def enqueue(self, kind="update", source_id=None):
        if kind not in {"update", "check"}:
            raise ValueError("任务类型无效")
        sources = self.sources()
        if kind == "check":
            sources = [s for s in sources if s["id"] == source_id]
            if not sources:
                raise KeyError("入口不存在")
        else:
            sources = [s for s in sources if s["enabled"]]
            if not sources:
                raise ValueError("请先添加并启用至少一个订阅入口")
        snapshot = {"sources": sources, "settings": self.get("settings")}
        job_id = uuid.uuid4().hex
        with self.connect() as db:
            try:
                db.execute(
                    "INSERT INTO jobs(id,kind,source_id,status,created_at,snapshot) VALUES (?,?,?,'queued',?,?)",
                    (job_id, kind, source_id, time.time(), json.dumps(snapshot)),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("已有任务运行或排队，请等待完成") from exc
        return job_id

    def jobs(self, limit=50):
        with self.connect() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
                )
            ]
        for row in rows:
            row.pop("snapshot", None)
            row["summary"] = json.loads(row["summary"]) if row["summary"] else None
        return rows

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("任务不存在")
        return dict(row)

    def update_job(self, job_id, **fields):
        allowed = {
            "status",
            "started_at",
            "finished_at",
            "cancel",
            "phase",
            "current",
            "total",
            "message",
            "summary",
        }
        if not fields or set(fields) - allowed:
            raise ValueError("Invalid job fields")
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?",
                (*fields.values(), job_id),
            )

    def rules(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM rules")]
