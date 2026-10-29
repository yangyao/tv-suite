import asyncio
import json
import os
import time
from pathlib import Path

from manager.playlists import parse_playlist, read_channels
from manager.store import atomic_write


def check_source(source):
    import requests

    started = time.monotonic()
    with requests.get(
        source["url"],
        headers={"User-Agent": source.get("user_agent") or "TVSuite/1.0"},
        timeout=(5, 10),
        stream=True,
    ) as response:
        response.raise_for_status()
        chunks = bytearray()
        for chunk in response.iter_content(65536):
            chunks.extend(chunk)
            if len(chunks) > 5 * 1024 * 1024:
                raise ValueError("订阅超过 5 MB，请拆分后导入")
            if time.monotonic() - started > 20:
                raise TimeoutError("订阅下载超时")
        entries = parse_playlist(chunks.decode("utf-8-sig", errors="replace"))
    if not entries:
        raise ValueError("入口可以连接，但未解析出 M3U 或 TXT 频道")
    return {
        "id": source["id"],
        "url": source["url"],
        "status": "ok",
        "candidates": len(entries),
        "error": "",
    }


def run():
    parent = int(os.environ.get("MANAGER_PARENT_PID", "0"))
    if parent:
        import signal
        import threading

        def watch_parent():
            while True:
                if os.getppid() != parent:
                    os.killpg(os.getpid(), signal.SIGTERM)
                    return
                time.sleep(1)

        threading.Thread(target=watch_parent, daemon=True).start()
    snapshot = json.loads(Path("snapshot.json").read_text())
    sources = snapshot["sources"]
    if snapshot["kind"] == "check":
        try:
            result = check_source(sources[0])
        except Exception as exc:
            result = {
                "id": sources[0]["id"],
                "url": sources[0]["url"],
                "status": "failed",
                "candidates": 0,
                "error": str(exc),
            }
        print(
            result.get("error")
            or f"入口测试通过：解析出 {result['candidates']} 条候选",
            flush=True,
        )
        atomic_write(
            "summary.json",
            json.dumps({"sources": [result], "check": True}, ensure_ascii=False),
        )
        return 0 if result["status"] == "ok" else 1
    from main import UpdateSource
    from utils.reporting import Reporter
    from utils.tools import sanitize_filename_from_url

    with open("events.jsonl", "a", encoding="utf-8", buffering=1) as events:
        reporter = Reporter(
            event_callback=lambda event: events.write(
                json.dumps(event, ensure_ascii=False) + "\n"
            ),
            enable_console=True,
        )
        updater = UpdateSource(reporter=reporter)
        try:
            asyncio.run(updater.run_once())
        finally:
            reporter.close()
    playlist = Path("output/result.m3u")
    text = playlist.read_text() if playlist.exists() else ""
    channels = read_channels("output/data/channel_results.db", text)
    valid_urls = {r["url"] for c in channels for r in c["results"] if r.get("valid")}
    source_stats = []
    for source in sources:
        raw = Path("output/log/subscribe") / (
            sanitize_filename_from_url(source["url"]) + ".txt"
        )
        entries = (
            parse_playlist(raw.read_text(errors="replace")) if raw.exists() else []
        )
        source_stats.append(
            {
                "id": source["id"],
                "url": source["url"],
                "status": "ok" if entries else "failed",
                "candidates": len(entries),
                "passed": len({e["url"] for e in entries} & valid_urls),
                "error": ""
                if entries
                else "拉取失败、内容为空或格式无法解析，请查看任务日志",
            }
        )
    summary = {
        "sources": source_stats,
        "metrics": updater.run_metrics,
        "outcome": updater.run_outcome,
        "channels": len({(e["category"], e["name"]) for e in parse_playlist(text)}),
        "lines": len(parse_playlist(text)),
    }
    atomic_write("channels.json", json.dumps(channels, ensure_ascii=False))
    atomic_write("summary.json", json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
