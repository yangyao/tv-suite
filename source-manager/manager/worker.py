import configparser
import datetime
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from manager.playlists import filter_playlist, parse_playlist, read_channels
from manager.store import atomic_write

SOURCE_ROOT = Path(__file__).resolve().parents[1]


def next_run(settings, now=None):
    now = time.time() if now is None else now
    if settings["update_mode"] == "interval":
        return now + settings["update_interval"] * 3600
    current = datetime.datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai"))
    values = []
    for text in settings["update_times"].split(","):
        hour, minute = map(int, text.split(":"))
        value = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if value <= current:
            value += datetime.timedelta(days=1)
        values.append(value.timestamp())
    return min(values)


class Worker:
    def __init__(self, store):
        self.store = store
        self.root = store.root
        self.publish_lock = threading.RLock()
        self.thread = None
        self.child = None

    def initialize_publication(self):
        if (self.root / "current").exists():
            return
        old = self.root / "output/result.m3u"
        if old.exists() and parse_playlist(old.read_text()):
            self.publish(
                old.read_text(),
                read_channels(
                    self.root / "output/data/channel_results.db", old.read_text()
                ),
                "imported",
            )

    def publish(self, raw, channels, job_id):
        with self.publish_lock:
            name = uuid.uuid4().hex
            directory = self.root / "publications" / name
            directory.mkdir(parents=True)
            m3u, txt, entries = filter_playlist(raw, self.store.rules())
            for filename, value in [
                ("raw.m3u", raw),
                ("result.m3u", m3u),
                ("result.txt", txt),
                ("channels.json", json.dumps(channels, ensure_ascii=False)),
                (
                    "meta.json",
                    json.dumps(
                        {
                            "job_id": job_id,
                            "published_at": time.time(),
                            "channels": len(
                                {(e["category"], e["name"]) for e in entries}
                            ),
                            "lines": len(entries),
                        }
                    ),
                ),
            ]:
                atomic_write(directory / filename, value)
            link = self.root / ("current." + name)
            link.symlink_to(Path("publications") / name, target_is_directory=True)
            os.replace(link, self.root / "current")
            for old in sorted(
                (self.root / "publications").iterdir(),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[10:]:
                shutil.rmtree(old)

    def republish(self):
        with self.publish_lock:
            current = self.root / "current"
            if current.exists():
                self.publish(
                    (current / "raw.m3u").read_text(),
                    json.loads((current / "channels.json").read_text()),
                    json.loads((current / "meta.json").read_text())["job_id"],
                )

    def start(self):
        self.lock_file = open(self.root / "worker.lock", "a")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("已有后台执行器运行，请使用单 worker 启动服务")
        with self.store.connect() as db:
            db.execute(
                "UPDATE jobs SET status='interrupted',finished_at=?,message='服务重启，任务已中断' WHERE status='running'",
                (time.time(),),
            )
        if not self.store.get("bootstrapped"):
            self.store.set(
                "next_run",
                time.time()
                if self.store.get("settings")["update_startup"]
                else next_run(self.store.get("settings")),
            )
            self.store.set("bootstrapped", True)
        elif self.store.get("settings")["update_startup"]:
            self.store.set("next_run", time.time())
        self.initialize_publication()
        self.thread = threading.Thread(
            target=self.loop, name="source-manager-worker", daemon=True
        )
        self.thread.start()

    def loop(self):
        while True:
            try:
                self.tick()
            except Exception as exc:
                print(f"Manager worker error: {exc}", flush=True)
                time.sleep(2)
            time.sleep(0.5)

    def tick(self):
        with self.store.connect() as db:
            queued = db.execute(
                "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
        if queued:
            self.execute(queued[0])
            return
        if time.time() >= self.store.get("next_run", 0):
            try:
                self.store.enqueue()
            except ValueError:
                pass
            self.store.set("next_run", next_run(self.store.get("settings")))

    def prepare(self, job):
        directory = self.root / "jobs" / job["id"]
        config_dir = directory / "config"
        shutil.copytree(
            self.root / "config"
            if (self.root / "config").exists()
            else SOURCE_ROOT / "config",
            config_dir,
            ignore=shutil.ignore_patterns("hls", "local", "*.db"),
        )
        settings = json.loads(job["snapshot"])
        settings["kind"] = job["kind"]
        atomic_write(
            directory / "snapshot.json", json.dumps(settings, ensure_ascii=False)
        )
        template = settings["settings"].get("channel_template", "")
        if template.strip():
            atomic_write(config_dir / "demo.txt", template)
        config = configparser.ConfigParser(interpolation=None)
        config.read(config_dir / "config.ini", encoding="utf-8-sig")
        config.read(config_dir / "user_config.ini", encoding="utf-8-sig")
        if not config.has_section("Settings"):
            config.add_section("Settings")
        for key, value in {
            **settings["settings"],
            "source_file": "config/demo.txt",
            "final_file": "output/result.txt",
            "open_update": True,
            "open_service": False,
            "open_rtmp": False,
            "open_epg": False,
            "open_local": False,
            "open_subscribe": True,
            "open_history": False,
            "open_m3u_result": True,
            "open_auto_disable_source": False,
            "open_unmatch_category": False,
            "open_supply": False,
            "open_update_time": False,
            "open_realtime_write": False,
            "open_stream_screenshot": False,
            "performance_mode": "powersave",
            "speed_test_mode": "quick",
            "resolution_speed_map": "",
            "public_url": "",
            "update_interval": 0,
            "update_times": "",
            "update_startup": True,
        }.items():
            if key != "channel_template":
                config.set("Settings", key, str(value))
        with open(config_dir / "config.ini", "w") as file:
            config.write(file)
        (config_dir / "user_config.ini").unlink(missing_ok=True)
        lines = [
            s["url"] + (" UA=" + json.dumps(s["user_agent"]) if s["user_agent"] else "")
            for s in settings["sources"]
        ]
        atomic_write(config_dir / "subscribe.txt", "\n".join(lines) + "\n")
        blocked = [r["target"] for r in self.store.rules() if r["kind"] == "url"]
        with open(config_dir / "blacklist.txt", "a") as file:
            file.write("\n" + "\n".join(blocked) + "\n")
        return directory

    def consume_events(self, job_id, file):
        if not file.exists():
            return
        for line in file.read_text(errors="replace").splitlines()[-10:]:
            try:
                event = json.loads(line)
                fields = event.get("data", {})
                previous = self.store.job(job_id)
                phase = event.get("phase") or previous["phase"] or "prepare"
                same_phase = phase == previous["phase"]
                self.store.update_job(
                    job_id,
                    phase=phase,
                    current=fields.get(
                        "current", previous["current"] if same_phase else 0
                    ),
                    total=fields.get("total", previous["total"] if same_phase else 0),
                    message=event.get("message", "")[:1000],
                )
            except (ValueError, TypeError):
                continue

    def execute(self, job_id):
        job = self.store.job(job_id)
        if job["cancel"]:
            self.store.update_job(job_id, status="cancelled", finished_at=time.time())
            return
        self.store.update_job(
            job_id, status="running", started_at=time.time(), phase="prepare"
        )
        try:
            directory = self.prepare(job)
            env = dict(os.environ)
            env.update(
                PYTHONPATH=str(SOURCE_ROOT),
                IPTV_API_SKIP_VERSION_CHECK="1",
                IPTV_API_PLAIN_OUTPUT="1",
                MANAGER_PARENT_PID=str(os.getpid()),
            )
            for key in list(env):
                if key in {"PUBLIC_URL", "PUBLIC_PORT", "GITHUB_ACTIONS"}:
                    env.pop(key, None)
            with open(directory / "console.log", "w") as log:
                self.child = subprocess.Popen(
                    [sys.executable, "-m", "manager.engine"],
                    cwd=directory,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                while self.child.poll() is None:
                    self.consume_events(job_id, directory / "events.jsonl")
                    if self.store.job(job_id)["cancel"]:
                        self.terminate()
                        self.store.update_job(
                            job_id,
                            status="cancelled",
                            finished_at=time.time(),
                            message="任务已停止，保留上一版订阅",
                        )
                        return
                    time.sleep(0.5)
                returncode = self.child.returncode
            self.consume_events(job_id, directory / "events.jsonl")
            summary_path = directory / "summary.json"
            summary = (
                json.loads(summary_path.read_text()) if summary_path.exists() else {}
            )
            self.update_sources(summary.get("sources", []), job["kind"])
            if (directory / "channels.json").exists():
                self.store.set("latest_result", job_id)
            if returncode != 0:
                raise RuntimeError("检测任务失败，请查看任务日志")
            with self.publish_lock:
                if self.store.job(job_id)["cancel"]:
                    self.store.update_job(
                        job_id,
                        status="cancelled",
                        finished_at=time.time(),
                        message="任务已停止，保留上一版订阅",
                    )
                    return
                status = "success"
                message = "入口测试通过" if job["kind"] == "check" else "更新并发布完成"
                if job["kind"] == "update":
                    raw = (directory / "output/result.m3u").read_text()
                    if not parse_playlist(raw):
                        status, message = "empty", "没有检测通过的线路，保留上一版订阅"
                    else:
                        self.publish(
                            raw,
                            json.loads((directory / "channels.json").read_text()),
                            job_id,
                        )
                self.store.update_job(
                    job_id,
                    status=status,
                    finished_at=time.time(),
                    phase="complete",
                    message=message,
                    summary=json.dumps(summary, ensure_ascii=False),
                )
        except Exception as exc:
            self.store.update_job(
                job_id, status="failed", finished_at=time.time(), message=str(exc)
            )
        finally:
            self.child = None
            if job["kind"] == "update":
                self.store.set("next_run", next_run(self.store.get("settings")))
            self.cleanup()

    def update_sources(self, sources, kind):
        with self.store.connect() as db:
            for source in sources:
                db.execute(
                    "UPDATE sources SET checked_at=?,status=?,candidates=?,error=?,failures=CASE WHEN ?='ok' THEN 0 ELSE failures+1 END WHERE id=? AND url=?",
                    (
                        time.time(),
                        source["status"],
                        source.get("candidates", 0),
                        source.get("error", ""),
                        source["status"],
                        source["id"],
                        source["url"],
                    ),
                )
                if kind == "update":
                    db.execute(
                        "UPDATE sources SET passed=? WHERE id=? AND url=?",
                        (source.get("passed", 0), source["id"], source["url"]),
                    )

    def terminate(self):
        if self.child is not None and self.child.poll() is None:
            os.killpg(self.child.pid, signal.SIGTERM)
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.child.pid, signal.SIGKILL)
                self.child.wait()

    def cleanup(self):
        with self.store.connect() as db:
            old = db.execute(
                "SELECT id FROM jobs WHERE status NOT IN ('queued','running') ORDER BY created_at DESC LIMIT -1 OFFSET 30"
            ).fetchall()
            for row in old:
                shutil.rmtree(self.root / "jobs" / row[0], ignore_errors=True)
                db.execute("DELETE FROM jobs WHERE id=?", (row[0],))
