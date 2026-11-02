import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from manager import discovery as d
from manager.app import create_app
from manager.playlists import channel_key, parse_playlist

HK = "https://iptv-org.github.io/iptv/countries/hk.m3u"
TW = "https://iptv-org.github.io/iptv/countries/tw.m3u"
FREE = d.PROVIDERS["free-tv"]["url"]
INDEX = d.PROVIDERS["iptv-org"]["url"]
LIST = '#EXTM3U\n#EXTINF:-1 group-title="News",New TV (720p)\nhttps://example.com/live.m3u8\n'


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = create_app(self.root)
        self.store = self.app.extensions["manager_store"]
        self.worker = self.app.extensions["manager_worker"]
        self.client = self.app.test_client()
        self.headers = {"X-CSRF-Token": self.client.get("/api/state").json["csrf"]}
        self.store.set(
            "discovery_config",
            {
                **d.DEFAULTS,
                "enabled": True,
                "providers": ["iptv-org"],
                "countries": ["hk"],
                "languages": [],
                "new_channel_limit": 2,
                "probe_budget": 3,
            },
        )

    def snapshot(self):
        jid = self.store.enqueue("discover")
        job = self.store.job(jid)
        self.store.update_job(jid, status="cancelled")
        return json.loads(job["snapshot"])

    def scan(self, snapshot=None, content=LIST, valid=True):
        fetcher = Mock()
        fetcher.get.side_effect = lambda url: ((HK if url == INDEX else content), True)
        with (
            patch.object(d, "Fetcher", return_value=fetcher),
            patch.object(
                d,
                "probe",
                return_value={
                    "valid": valid,
                    "resolution": "1280x720",
                    "error": "" if valid else "unavailable",
                },
            ) as probe,
        ):
            result = d.scan(
                snapshot or self.snapshot(), self.root, lambda message: None
            )
        return result, probe

    def test_config_validates_bounds_and_known_providers(self):
        for value in (
            {"probe_budget": True},
            {"probe_budget": 1000},
            {"providers": ["unknown"]},
            {"countries": ["hk", "hk"]},
            {"languages": ["unsupported"]},
            {"unknown": 1},
        ):
            with self.assertRaises(ValueError):
                d.validate_config(value)
        self.assertEqual(
            d.validate_config({"languages": ["eng"]})["languages"], ["eng"]
        )

    def test_extracts_selected_profiles_and_normalizes_mirrors(self):
        settings = {**d.DEFAULTS, "countries": ["hk"], "languages": []}
        rows = d.find_playlists("iptv-org", HK + "\n" + TW, settings)
        self.assertEqual([r[0] for r in rows], [HK])
        self.assertEqual(
            d.canonical(
                "https://gh.aptv.app/https://raw.githubusercontent.com/a/b/main/tv.m3u"
            ),
            "https://raw.githubusercontent.com/a/b/main/tv.m3u",
        )
        self.assertEqual(
            d.canonical("https://cdn.jsdelivr.net/gh/a/b@main/tv.m3u"),
            "https://raw.githubusercontent.com/a/b/main/tv.m3u",
        )
        self.assertEqual(
            d.category_for(
                {"category": "Hong Kong", "name": "News"}, "free-tv", settings
            ),
            "发现·香港",
        )
        self.assertIsNone(
            d.category_for({"category": "USA", "name": "Movies"}, "free-tv", settings)
        )

    def test_normalizes_aptv_wrappers_and_keeps_distinct_cctv_channels(self):
        wrapped = "https://add.aptv.app/https://gh.aptv.app/https://raw.githubusercontent.com/mzky/checklist/refs/heads/master/itvlist.m3u"
        self.assertEqual(
            d.canonical(wrapped),
            "https://raw.githubusercontent.com/mzky/checklist/master/itvlist.m3u",
        )
        self.assertEqual(d.name_key("CCTV-1"), d.name_key("CCTV 1 综合"))
        self.assertNotEqual(d.name_key("CCTV-5"), d.name_key("CCTV-5+"))
        self.assertNotEqual(d.name_key("CCTV-4"), d.name_key("CCTV-4K"))
        self.assertEqual(d.name_key("凤凰资讯"), d.name_key("鳳凰衛視資訊台"))

    def test_imports_and_publishes_tested_new_channels_without_editing_catalog(self):
        original = self.store.get("settings")["channel_template"]
        result, probe = self.scan()
        self.assertEqual(result["added_channels"], 1)
        self.assertEqual(probe.call_count, 1)
        d.apply_result(self.store, result, "scan")
        self.worker.publish("#EXTM3U\n", [], "seed")
        playlist = (self.root / "current/result.m3u").read_text()
        self.assertIn("New TV", playlist)
        self.assertEqual(self.store.get("settings")["channel_template"], original)
        self.assertEqual(len(self.store.sources()), 1)
        self.assertEqual(len(d.published_entries(self.store)), 1)
        token = channel_key("发现·香港", "New TV")
        response = self.client.put(
            "/api/rules",
            json={"kind": "channel", "target": token, "enabled": True},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            parse_playlist((self.root / "current/result.m3u").read_text()), []
        )
        self.client.put(
            "/api/rules",
            json={"kind": "channel", "target": token, "enabled": False},
            headers=self.headers,
        )
        self.assertEqual(
            len(parse_playlist((self.root / "current/result.m3u").read_text())), 1
        )

    def test_reuses_recent_results_and_cools_after_three_failures(self):
        result, _ = self.scan()
        d.apply_result(self.store, result, "first")
        result, probe = self.scan()
        self.assertEqual(probe.call_count, 0)
        for failures in range(1, 4):
            snap = self.snapshot()
            for row in snap["discovery_candidates"]:
                row["next_check"] = 0
            result, _ = self.scan(snap, valid=False)
            row = result["records"][0]
            self.assertEqual(row["failures"], failures)
            self.assertEqual(row["valid"], failures < 3)
            d.apply_result(self.store, result, "retry")
        self.assertGreater(result["records"][0]["next_check"], time.time() + 23 * 3600)
        self.assertEqual(d.published_entries(self.store), [])
        snap = self.snapshot()
        snap["discovery_candidates"][0]["next_check"] = 0
        result, _ = self.scan(snap)
        self.assertTrue(result["records"][0]["valid"])
        self.assertEqual(result["records"][0]["failures"], 0)

    def test_rotating_urls_replace_old_links_without_waiting_for_channel_eviction(self):
        result, _ = self.scan()
        d.apply_result(self.store, result, "first")
        for i in range(3):
            snap = self.snapshot()
            for row in snap["discovery_candidates"]:
                row["next_check"] = 0
            content = LIST.replace("/live.m3u8", f"/rotation{i}.m3u8")
            fetcher = Mock()
            fetcher.get.side_effect = lambda url: (
                HK if url == INDEX else content,
                True,
            )
            with (
                patch.object(d, "Fetcher", return_value=fetcher),
                patch.object(
                    d,
                    "probe",
                    side_effect=lambda row, *args: {
                        "valid": row["url"].endswith(f"rotation{i}.m3u8"),
                        "error": ""
                        if row["url"].endswith(f"rotation{i}.m3u8")
                        else "expired",
                    },
                ),
            ):
                result = d.scan(snap, self.root, lambda message: None)
            d.apply_result(self.store, result, "rotated")
            self.assertLessEqual(len(result["records"]), 2)
            self.assertTrue(
                d.published_entries(self.store)[0]["url"].endswith(f"rotation{i}.m3u8")
            )

    def test_due_streams_still_get_checked_when_catalog_is_temporarily_unavailable(
        self,
    ):
        result, _ = self.scan()
        d.apply_result(self.store, result, "first")
        snap = self.snapshot()
        snap["discovery_candidates"][0]["next_check"] = 0
        fetcher = Mock()
        fetcher.get.side_effect = OSError("offline")
        with (
            patch.object(d, "Fetcher", return_value=fetcher),
            patch.object(
                d, "probe", return_value={"valid": False, "error": "offline"}
            ) as probe,
        ):
            result = d.scan(snap, self.root, lambda message: None)
        self.assertEqual(probe.call_count, 1)
        self.assertTrue(result["records"][0]["valid"])
        self.assertEqual(result["records"][0]["failures"], 1)

    def test_respects_disabled_and_deleted_sources_and_race(self):
        result, _ = self.scan()
        d.apply_result(self.store, result, "first")
        source = self.store.sources()[0]
        self.store.save_source({"url": source["url"], "enabled": False}, source["id"])
        result, probe = self.scan()
        self.assertEqual(probe.call_count, 0)
        d.apply_result(self.store, result, "disabled")
        self.assertFalse(self.store.sources()[0]["enabled"])
        with self.store.connect() as db:
            db.execute("DELETE FROM sources")
        result, _ = self.scan()
        d.apply_result(self.store, result, "deleted")
        self.assertEqual(self.store.sources(), [])
        self.assertEqual(d.published_entries(self.store), [])
        # Deletion during the first scan must also be respected.
        self.store.save_source({"url": HK})
        with self.store.connect() as db:
            db.execute("DELETE FROM discovery_sources")
        result, _ = self.scan()
        with self.store.connect() as db:
            db.execute("DELETE FROM sources")
        d.apply_result(self.store, result, "race")
        self.assertEqual(self.store.sources(), [])

    def test_probe_budget_and_channel_cap(self):
        content = "#EXTM3U\n" + "".join(
            f"#EXTINF:-1,Channel {i}\nhttps://example.com/{i}.m3u8\n" for i in range(10)
        )
        result, probe = self.scan(content=content)
        self.assertEqual(result["added_channels"], 2)
        self.assertEqual(probe.call_count, 2)
        result, probe = self.scan(content=content, valid=False)
        self.assertEqual(probe.call_count, 3)
        self.assertEqual(result["added_channels"], 0)

    def test_content_dedup_and_whole_provider_failure_preserves_old_results(self):
        snap = self.snapshot()
        snap["discovery_config"]["countries"] = ["hk", "tw"]
        fetcher = Mock()
        fetcher.get.side_effect = lambda url: (
            HK + "\n" + TW if url == INDEX else LIST,
            True,
        )
        with (
            patch.object(d, "Fetcher", return_value=fetcher),
            patch.object(d, "probe", return_value={"valid": True}),
        ):
            result = d.scan(snap, self.root, lambda message: None)
        self.assertEqual([r["status"] for r in result["sources"]], ["ok", "duplicate"])
        d.apply_result(self.store, result, "dedup")
        self.assertEqual(len(self.store.sources()), 1)
        snap = self.snapshot()
        fetcher.get.side_effect = OSError("offline")
        with patch.object(d, "Fetcher", return_value=fetcher):
            result = d.scan(snap, self.root, lambda message: None)
        self.assertTrue(result["provider_errors"])
        self.assertTrue(result["records"][0]["valid"])

    def test_unapproved_sources_require_import_and_manual_import_is_allowlisted(self):
        snap = self.snapshot()
        snap["discovery_config"]["auto_import"] = False
        self.store.set("discovery_config", snap["discovery_config"])
        result, probe = self.scan(snap)
        d.apply_result(self.store, result, "candidate")
        self.assertEqual(probe.call_count, 0)
        self.assertEqual(self.store.sources(), [])
        self.assertEqual(
            self.client.post(
                "/api/discovery/import",
                json={"url": "https://example.com/arbitrary"},
                headers=self.headers,
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/discovery/import", json={"url": HK}, headers=self.headers
            ).status_code,
            201,
        )
        result, probe = self.scan()
        self.assertEqual(probe.call_count, 1)

    def test_scheduler_serialization_snapshot_and_cancelled_discovery(self):
        self.store.set("next_run", time.time() + 86400)
        self.worker.tick()
        job = self.store.jobs()[0]
        self.assertEqual(job["kind"], "discover")
        with self.assertRaises(ValueError):
            self.store.enqueue("discover")
        self.store.update_job(job["id"], cancel=1)
        self.worker.execute(job["id"])
        self.assertEqual(self.store.job(job["id"])["status"], "cancelled")
        self.assertEqual(d.rows(self.store, "discovery_sources"), [])

    def test_worker_applies_only_finished_jobs_and_keeps_base_publication(self):
        self.worker.publish(
            '#EXTM3U\n#EXTINF:-1 group-title="Old",Old\nhttps://example.com/old.m3u8\n',
            [],
            "seed",
        )
        result, _ = self.scan()

        def child(*args, cwd, **kwargs):
            Path(cwd, "discovery-result.json").write_text(json.dumps(result))
            Path(cwd, "summary.json").write_text(json.dumps({"checked": 1}))
            return Mock(poll=Mock(return_value=0), returncode=0)

        jid = self.store.enqueue("discover")
        with patch("manager.worker.subprocess.Popen", side_effect=child):
            self.worker.execute(jid)
        self.assertEqual(self.store.job(jid)["status"], "success")
        entries = parse_playlist((self.root / "current/result.m3u").read_text())
        self.assertEqual({e["name"] for e in entries}, {"Old", "New TV"})
        self.worker.republish()
        self.assertEqual(
            len(parse_playlist((self.root / "current/result.m3u").read_text())), 2
        )
        old = (self.root / "current/result.m3u").read_text()
        result["records"][0]["name"] = "Cancelled new name"
        jid = self.store.enqueue("discover")

        def cancelled(*args, **kwargs):
            self.store.update_job(jid, cancel=1)
            return child(*args, **kwargs)

        with patch("manager.worker.subprocess.Popen", side_effect=cancelled):
            self.worker.execute(jid)
        self.assertEqual(self.store.job(jid)["status"], "cancelled")
        self.assertEqual((self.root / "current/result.m3u").read_text(), old)

    @patch("manager.discovery.validate_remote")
    def test_fetcher_conditional_cache_and_size_guard(self, _validate):
        fetch = d.Fetcher(self.root / "http")
        response = Mock(status_code=200, headers={"ETag": "version1"})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = iter([LIST.encode()])
        with patch.object(fetch.session, "get", return_value=response):
            text, changed = fetch.get(HK)
        self.assertTrue(changed)
        response.status_code = 304
        with patch.object(fetch.session, "get", return_value=response) as get:
            again, changed = fetch.get(HK)
            self.assertEqual(
                get.call_args.kwargs["headers"]["If-None-Match"], "version1"
            )
        self.assertEqual(text, again)
        self.assertFalse(changed)
        response.status_code = 200
        response.iter_content.return_value = iter([b"x" * (5 * 1024 * 1024 + 1)])
        with patch.object(fetch.session, "get", return_value=response):
            with self.assertRaises(ValueError):
                fetch.get(HK)

    def test_api_settings_csrf_and_invalid_input(self):
        self.assertEqual(
            self.client.put("/api/discovery/settings", json={}).status_code, 403
        )
        self.assertEqual(
            self.client.put(
                "/api/discovery/settings",
                json={"probe_budget": 200},
                headers=self.headers,
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.put(
                "/api/discovery/settings", json={"enabled": False}, headers=self.headers
            ).status_code,
            200,
        )
        state = self.client.get("/api/state").json["discovery"]
        self.assertFalse(state["config"]["enabled"])
        self.assertIn("free-tv", state["providers"])


if __name__ == "__main__":
    unittest.main()
