import json
import math
import os
import secrets
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, send_file

from manager.preview import register_preview
from manager.store import Store, validate_settings
from manager.worker import Worker, next_run


def finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    return value


def create_app(data_dir=None, start_worker=False):
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/admin/static",
    )
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024
    store = Store(data_dir or os.environ.get("MANAGER_DATA_DIR", "/data"))
    worker = Worker(store)
    app.extensions.update(manager_store=store, manager_worker=worker)
    csrf = secrets.token_urlsafe(32)

    @app.before_request
    def protect_mutation():
        if request.path.startswith("/api/") and request.method not in {
            "GET",
            "HEAD",
            "OPTIONS",
        }:
            origin = request.headers.get("Origin")
            if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
                return jsonify(error="请求来源不匹配，请从后台页面操作"), 403
            if not secrets.compare_digest(
                request.headers.get("X-CSRF-Token", ""), csrf
            ):
                return jsonify(error="页面已过期，请刷新后重试"), 403
            if not request.is_json:
                return jsonify(error="请求格式应为 JSON"), 415
            if not isinstance(request.get_json(silent=True), dict):
                return jsonify(error="请求内容必须是 JSON 对象"), 400

    @app.after_request
    def headers(response):
        if request.path.startswith(("/api/", "/admin")):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
        )
        if request.path == "/preview":
            response.headers["Cache-Control"] = "no-store"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data: http: https:; media-src 'self' blob: http: https:; "
                "connect-src 'self' http: https:; worker-src 'self' blob:; "
                "frame-ancestors 'none'; base-uri 'none'"
            )
        return response

    @app.errorhandler(ValueError)
    def value_error(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(KeyError)
    def missing(exc):
        return jsonify(error=str(exc.args[0])), 404

    @app.errorhandler(sqlite3.IntegrityError)
    def duplicate(_exc):
        return jsonify(error="这个订阅地址已经存在"), 409

    @app.get("/")
    def index():
        return redirect("/admin")

    @app.get("/admin")
    def admin():
        return render_template("index.html")

    @app.get("/api/state")
    def state():
        current = store.root / "current/meta.json"
        publication = json.loads(current.read_text()) if current.exists() else None
        return jsonify(
            csrf=csrf,
            settings=store.get("settings"),
            sources=store.sources(),
            jobs=store.jobs(),
            publication=publication,
            next_run=store.get("next_run"),
            subscription=request.host_url.rstrip("/") + "/m3u",
            catalog=(store.root / "config/demo.txt").read_text()
            if (store.root / "config/demo.txt").exists()
            else (Path(__file__).resolve().parents[1] / "config/demo.txt").read_text(),
        )

    @app.post("/api/sources")
    def add_source():
        return jsonify(id=store.save_source(request.get_json())), 201

    @app.put("/api/sources/<source_id>")
    def edit_source(source_id):
        return jsonify(id=store.save_source(request.get_json(), source_id))

    @app.delete("/api/sources/<source_id>")
    def delete_source(source_id):
        with store.connect() as db:
            if not db.execute("DELETE FROM sources WHERE id=?", (source_id,)).rowcount:
                raise KeyError("入口不存在")
        return jsonify(ok=True)

    @app.post("/api/sources/import")
    def import_sources():
        lines = request.get_json().get("text", "").splitlines()
        if len(lines) > 100:
            raise ValueError("每次最多导入 100 行")
        added, errors = 0, []
        for index, line in enumerate(lines, 1):
            if not line.strip() or line.strip().startswith("#"):
                continue
            try:
                store.save_source({"url": line.strip()})
                added += 1
            except (ValueError, sqlite3.IntegrityError) as exc:
                errors.append(
                    {
                        "line": index,
                        "message": "地址已存在"
                        if isinstance(exc, sqlite3.IntegrityError)
                        else str(exc),
                    }
                )
        return jsonify(added=added, errors=errors)

    @app.put("/api/settings")
    def settings():
        settings = validate_settings(request.get_json())
        store.set("settings", settings)
        store.set("next_run", next_run(settings))
        return jsonify(settings=settings)

    @app.post("/api/jobs")
    def enqueue():
        body = request.get_json()
        return jsonify(
            id=store.enqueue(body.get("kind", "update"), body.get("source_id"))
        ), 202

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel(job_id):
        with worker.publish_lock:
            job = store.job(job_id)
            if job["status"] not in {"queued", "running"}:
                raise ValueError("任务已结束")
            store.update_job(job_id, cancel=1)
        return jsonify(ok=True)

    @app.get("/api/jobs/<job_id>/log")
    def log(job_id):
        store.job(job_id)
        path = store.root / "jobs" / job_id / "console.log"
        text = ""
        if path.exists():
            with open(path, "rb") as file:
                file.seek(max(0, path.stat().st_size - 65536))
                text = file.read().decode("utf-8", errors="replace")
        return jsonify(text=text)

    @app.get("/api/channels")
    def channels():
        latest = store.get("latest_result")
        path = (
            store.root / "jobs" / latest / "channels.json"
            if latest
            else store.root / "current/channels.json"
        )
        if not path.exists():
            path = store.root / "current/channels.json"
        rows = json.loads(path.read_text()) if path.exists() else []
        query = request.args.get("search", "").strip().casefold()
        category = request.args.get("category", "")
        rules = store.rules()
        blocked = {r["target"] for r in rules if r["kind"] == "url"}
        hidden = {r["target"] for r in rules if r["kind"] == "channel"}
        categories = sorted({c["category"] for c in rows})
        result = []
        for row in rows:
            if (query and query not in (row["name"] + row["category"]).casefold()) or (
                category and category != row["category"]
            ):
                continue
            row["hidden"] = row["channel_key"] in hidden
            for source in row["results"]:
                source["blocked"] = source["url"] in blocked
            result.append(row)
        return jsonify(finite({"channels": result, "categories": categories}))

    @app.put("/api/rules")
    def rules():
        body = request.get_json()
        kind, target, enabled = (
            body.get("kind"),
            body.get("target"),
            body.get("enabled"),
        )
        if (
            kind not in {"url", "channel"}
            or not isinstance(target, str)
            or not target
            or len(target) > 4096
            or not isinstance(enabled, bool)
        ):
            raise ValueError("屏蔽规则格式无效")
        with store.connect() as db:
            if enabled:
                db.execute("INSERT OR IGNORE INTO rules VALUES (?,?)", (kind, target))
            else:
                db.execute(
                    "DELETE FROM rules WHERE kind=? AND target=?", (kind, target)
                )
        worker.republish()
        return jsonify(ok=True)

    @app.get("/m3u")
    @app.get("/txt")
    def playlist():
        filename = "result.m3u" if request.path == "/m3u" else "result.txt"
        path = store.root / "current" / filename
        if not path.exists():
            return "订阅尚未生成，请在后台添加入口并执行更新。", 503
        response = send_file(
            path,
            mimetype="audio/x-mpegurl" if filename.endswith("m3u") else "text/plain",
            conditional=False,
        )
        response.headers["Cache-Control"] = "no-cache"
        return response

    register_preview(app, store, csrf)

    if start_worker:
        worker.start()
    return app
