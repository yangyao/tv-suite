import configparser
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from manager.app import create_app
from manager.playlists import channel_key, parse_playlist
from manager.store import DEFAULT_SETTINGS, Store, validate_settings, validate_source
from manager.worker import next_run

PLAYLIST = """#EXTM3U
#EXTINF:-1 group-title="News",Demo
https://example.com/one.m3u8
#EXTINF:-1 group-title="News",Demo
https://example.com/two.m3u8
#EXTINF:-1 group-title="Other",Other
https://example.com/other.m3u8
"""


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = create_app(self.root)
        self.client = self.app.test_client()
        self.store = self.app.extensions["manager_store"]
        self.worker = self.app.extensions["manager_worker"]
        self.token = self.client.get("/api/state").json["csrf"]

    def mutate(self, method, path, body):
        return self.client.open(
            path, method=method, json=body, headers={"X-CSRF-Token": self.token}
        )

    def playlist(self):
        with self.client.get("/m3u") as response:
            return response.data

    def source(self, **overrides):
        return self.store.save_source(
            {"url": "https://example.com/list.m3u", "name": "Example", **overrides}
        )

    def test_local_dashboard_needs_no_login_and_playlist_stays_public(self):
        self.assertEqual(self.client.get("/admin").status_code, 200)
        self.assertEqual(self.client.get("/m3u").status_code, 503)
        self.worker.publish(PLAYLIST, [], "seed")
        response = self.client.get("/m3u")
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"https://example.com/one.m3u8", response.data)
        self.assertEqual(response.headers["Cache-Control"], "no-cache")

    def test_mutations_reject_cross_site_and_missing_token(self):
        self.assertEqual(
            self.client.post(
                "/api/sources", json={"url": "https://example.com"}
            ).status_code,
            403,
        )
        response = self.client.post(
            "/api/sources",
            json={"url": "https://example.com"},
            headers={"Origin": "https://evil.example", "X-CSRF-Token": self.token},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self.mutate(
                "POST", "/api/sources", {"url": "https://example.com"}
            ).status_code,
            201,
        )

    def test_source_crud_duplicate_and_bulk_import(self):
        source_id = self.source()
        self.assertEqual(
            self.mutate(
                "POST", "/api/sources", {"url": "https://example.com/list.m3u"}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.mutate(
                "PUT",
                "/api/sources/" + source_id,
                {"url": "https://example.com/list.m3u", "enabled": False},
            ).status_code,
            200,
        )
        self.assertFalse(self.store.sources()[0]["enabled"])
        response = self.mutate(
            "POST",
            "/api/sources/import",
            {"text": "https://example.com/2\nnot-a-url\nhttps://example.com/2"},
        )
        self.assertEqual(response.json["added"], 1)
        self.assertEqual(len(response.json["errors"]), 2)
        self.assertEqual(
            self.mutate("DELETE", "/api/sources/" + source_id, {}).status_code, 200
        )

    def test_import_disabled_sources_and_preserve_ua(self):
        other = self.root / "import"
        (other / "config").mkdir(parents=True)
        (other / "config/subscribe.txt").write_text(
            '# https://example.com/disabled\nhttps://example.com/a UA="TV Agent"\n'
        )
        store = Store(other)
        rows = {r["url"]: r for r in store.sources()}
        self.assertFalse(rows["https://example.com/disabled"]["enabled"])
        self.assertEqual(rows["https://example.com/a"]["user_agent"], "TV Agent")
        self.assertEqual(len(Store(other).sources()), 2)

    def test_only_one_job_and_snapshot_does_not_change(self):
        source_id = self.source()
        job_id = self.store.enqueue()
        self.store.save_source(
            {"url": "https://example.com/changed", "enabled": True}, source_id
        )
        self.assertEqual(
            json.loads(self.store.job(job_id)["snapshot"])["sources"][0]["url"],
            "https://example.com/list.m3u",
        )
        self.assertEqual(self.mutate("POST", "/api/jobs", {}).status_code, 400)
        self.assertEqual(
            self.mutate("POST", f"/api/jobs/{job_id}/cancel", {}).status_code, 200
        )
        self.worker.execute(job_id)
        self.assertEqual(self.store.job(job_id)["status"], "cancelled")

    def test_atomic_one_job_constraint_with_concurrent_requests(self):
        self.source()
        successes = []

        def enqueue():
            try:
                successes.append(self.store.enqueue())
            except ValueError:
                pass

        threads = [threading.Thread(target=enqueue) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(successes), 1)

    def test_rules_republish_immediately_and_restore_from_raw(self):
        self.worker.publish(PLAYLIST, [], "seed")
        response = self.mutate(
            "PUT",
            "/api/rules",
            {"kind": "url", "target": "https://example.com/one.m3u8", "enabled": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"https://example.com/one.m3u8", self.playlist())
        self.mutate(
            "PUT",
            "/api/rules",
            {"kind": "channel", "target": channel_key("News", "Demo"), "enabled": True},
        )
        self.assertNotIn(b'#EXTINF:-1 group-title="News"', self.playlist())
        self.mutate(
            "PUT",
            "/api/rules",
            {
                "kind": "channel",
                "target": channel_key("News", "Demo"),
                "enabled": False,
            },
        )
        self.assertIn(b"https://example.com/two.m3u8", self.playlist())

    def test_new_generation_preserves_persistent_rules(self):
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO rules VALUES (?,?)",
                ("channel", channel_key("News", "Demo")),
            )
        self.worker.publish(PLAYLIST, [], "first")
        first = os.readlink(self.root / "current")
        self.worker.publish(PLAYLIST, [], "second")
        self.assertNotEqual(first, os.readlink(self.root / "current"))
        self.assertEqual(len(parse_playlist(self.playlist().decode())), 1)

    def test_prepare_freezes_config_and_disables_relay(self):
        self.source()
        job = self.store.job(self.store.enqueue())
        directory = self.worker.prepare(job)
        config = configparser.ConfigParser(interpolation=None)
        config.read(directory / "config/config.ini")
        for key in [
            "open_rtmp",
            "open_service",
            "open_realtime_write",
            "open_unmatch_category",
        ]:
            self.assertFalse(config.getboolean("Settings", key))
        self.assertIn(
            "https://example.com/list.m3u",
            (directory / "config/subscribe.txt").read_text(),
        )

    def test_failure_does_not_replace_existing_publication(self):
        self.worker.publish(PLAYLIST, [], "seed")
        self.source()
        job_id = self.store.enqueue()
        with patch.object(
            self.worker, "prepare", side_effect=RuntimeError("fixture failure")
        ):
            self.worker.execute(job_id)
        self.assertEqual(self.store.job(job_id)["status"], "failed")
        self.assertEqual(
            self.client.get("/api/state").json["publication"]["job_id"], "seed"
        )

    def test_validation_rejects_bad_settings_and_urls(self):
        for url in ["file:///etc/passwd", "http://", "https://example.com\nUA=bad"]:
            with self.assertRaises(ValueError):
                validate_source({"url": url})
        for changed in [
            {"speed_test_limit": 0},
            {"min_speed": float("nan")},
            {"update_times": "25:90"},
            {"open_filter_speed": "False"},
        ]:
            with self.assertRaises(ValueError):
                validate_settings({**DEFAULT_SETTINGS, **changed})
        self.assertEqual(next_run(DEFAULT_SETTINGS, 100), 100 + 12 * 3600)

    def test_progress_uses_reporter_data(self):
        self.source()
        job_id = self.store.enqueue()
        path = self.root / "events.jsonl"
        path.write_text(
            json.dumps(
                {
                    "phase": "speed_test",
                    "data": {"current": 4, "total": 9},
                    "message": "testing",
                }
            )
            + "\n"
        )
        self.worker.consume_events(job_id, path)
        self.assertEqual(self.store.job(job_id)["current"], 4)
        self.assertEqual(self.store.job(job_id)["total"], 9)

    def test_stale_source_measurement_does_not_overwrite_edited_url(self):
        source_id = self.source()
        self.store.save_source({"url": "https://example.com/new"}, source_id)
        self.worker.update_sources(
            [
                {
                    "id": source_id,
                    "url": "https://example.com/list.m3u",
                    "status": "ok",
                    "candidates": 9,
                    "passed": 4,
                }
            ],
            "update",
        )
        self.assertIsNone(self.store.sources()[0]["checked_at"])

    def test_catalog_setting_is_snapshotted_into_job(self):
        self.source()
        settings = self.store.get("settings")
        settings["channel_template"] = "News,#genre#\nNew Channel\n"
        self.assertEqual(self.mutate("PUT", "/api/settings", settings).status_code, 200)
        job = self.store.job(self.store.enqueue())
        directory = self.worker.prepare(job)
        self.assertEqual(
            (directory / "config/demo.txt").read_text(), settings["channel_template"]
        )

    def test_channel_api_sanitizes_infinite_probe_values(self):
        self.worker.publish(
            PLAYLIST,
            [
                {
                    "channel_key": "a",
                    "name": "Demo",
                    "category": "News",
                    "results": [
                        {
                            "url": "https://example.com/a",
                            "speed": float("inf"),
                            "valid": 1,
                        }
                    ],
                }
            ],
            "seed",
        )
        response = self.client.get("/api/channels")
        self.assertIsNone(response.json["channels"][0]["results"][0]["speed"])
        self.assertNotIn(b"Infinity", response.data)

    def test_m3u_and_txt_parser_preserves_options_and_quoted_commas(self):
        entries = parse_playlist(
            '#EXTM3U\r\n#EXTINF:-1 group-title="News, Live",Demo\r\n#EXTVLCOPT:http-user-agent=Agent\r\nhttps://example.com/live\r\n'
        )
        self.assertEqual(entries[0]["category"], "News, Live")
        self.assertEqual(entries[0]["options"], ["#EXTVLCOPT:http-user-agent=Agent"])
        self.assertEqual(
            parse_playlist("News,#genre#\nDemo,https://example.com/live")[0][
                "category"
            ],
            "News",
        )


if __name__ == "__main__":
    unittest.main()
