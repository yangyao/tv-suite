import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from manager.app import create_app
from manager.preview import Relay, validate_remote

PLAYLIST = """#EXTM3U
#EXTINF:-1 group-title="News" tvg-logo="https://example.com/logo.png",Demo
https://example.com/live.m3u8
#EXTINF:-1 group-title="News",Demo
https://example.com/backup.m3u8
"""


class PreviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = create_app(Path(temp.name))
        self.client = self.app.test_client()
        self.worker = self.app.extensions["manager_worker"]
        self.worker.publish(PLAYLIST, [], "preview-test")
        result = self.client.get("/api/preview/channels").json
        self.channel = result["channels"][0]
        self.headers = {"X-CSRF-Token": result["csrf"]}

    def test_catalog_groups_only_published_lines_and_honors_blocks(self):
        self.assertEqual(len(self.channel["sources"]), 2)
        self.assertEqual(self.channel["logo"], "https://example.com/logo.png")
        self.client.put(
            "/api/rules",
            json={
                "kind": "url",
                "target": "https://example.com/live.m3u8",
                "enabled": True,
            },
            headers=self.headers,
        )
        rows = self.client.get("/api/preview/channels").json["channels"]
        self.assertEqual(
            [s["url"] for s in rows[0]["sources"]], ["https://example.com/backup.m3u8"]
        )
        self.client.put(
            "/api/rules",
            json={"kind": "channel", "target": self.channel["id"], "enabled": True},
            headers=self.headers,
        )
        self.assertEqual(self.client.get("/api/preview/channels").json["channels"], [])

    def test_preview_csp_and_mutation_protection(self):
        response = self.client.get("/preview")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "worker-src 'self' blob:", response.headers["Content-Security-Policy"]
        )
        self.assertNotIn(
            "connect-src", self.client.get("/admin").headers["Content-Security-Policy"]
        )
        self.assertEqual(
            self.client.post("/api/preview/start", json={}).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                "/api/preview/start",
                json={"owner": "a" * 24, "channel": "unknown"},
                headers=self.headers,
            ).status_code,
            400,
        )

    @patch("manager.preview.validate_remote", side_effect=lambda url: url)
    def test_session_limits_replace_and_stop(self, _validate):
        relay = Relay()
        source = self.channel["sources"][0]
        _, first = relay.start(source, "one")
        _, second = relay.start(source, "one")
        self.assertNotIn(first, relay.sessions)
        self.assertIn(second, relay.sessions)
        relay.start(source, "two")
        with self.assertRaises(ValueError):
            relay.start(source, "three")
        relay.stop(second)
        relay.start(source, "three")
        with self.app.test_request_context():
            self.assertEqual(relay.fetch(first, "0")[1], 410)

    def test_rejects_private_and_non_http_targets(self):
        for url in ("file:///tmp/test", "http://user:secret@example.com/a"):
            with self.assertRaises(ValueError):
                validate_remote(url)
        for address in ("127.0.0.1", "192.168.0.1", "169.254.169.254", "::1"):
            with patch(
                "manager.preview.socket.getaddrinfo",
                return_value=[(None, None, None, None, (address, 80))],
            ):
                with self.assertRaises(ValueError):
                    validate_remote("http://example.com/a")

    @patch("manager.preview.validate_remote", side_effect=lambda url: url)
    def test_manifest_rewrites_segments_keys_maps_and_variants(self, _validate):
        relay = Relay()
        _, token = relay.start(self.channel["sources"][0], "one")
        session = relay.sessions[token]
        result = relay.rewrite(
            '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXT-X-MAP:URI="init.mp4"\n../segment.ts\n#EXT-X-MEDIA:URI="audio/list.m3u8"\n',
            "https://example.com/hls/main.m3u8",
            token,
            session,
        )
        self.assertEqual(result.count("/api/preview/relay/"), 4)
        self.assertIn("https://example.com/segment.ts", session["refs"].values())
        self.assertIn("https://example.com/hls/key", session["refs"].values())
        self.assertNotIn('URI="key"', result)

    @patch("manager.preview.validate_remote", side_effect=lambda url: url)
    @patch("manager.preview.requests.get")
    def test_relay_stream_cleanup_range_and_redirect_validation(self, get, _validate):
        relay = Relay()
        _, token = relay.start(self.channel["sources"][0], "one")
        response = Mock(
            status_code=206,
            headers={"Content-Type": "video/mp2t", "Content-Range": "bytes 0-5/10"},
        )
        response.iter_content.return_value = iter([b"abc", b"def"])
        get.return_value = response
        with self.app.test_request_context(headers={"Range": "bytes=0-5"}):
            result = relay.fetch(token, "0")
            self.assertEqual(b"".join(result.response), b"abcdef")
            self.assertEqual(result.headers["Content-Range"], "bytes 0-5/10")
        response.close.assert_called()
        self.assertEqual(get.call_args.kwargs["headers"]["Range"], "bytes=0-5")
        self.assertFalse(relay.sessions[token]["responses"])
        redirected = Mock(
            status_code=302, headers={"Location": "http://localhost/private"}
        )
        get.return_value = redirected
        _validate.side_effect = ["https://example.com/live.m3u8", ValueError("private")]
        with self.app.test_request_context():
            with self.assertRaises(ValueError):
                relay.fetch(token, "0")
        redirected.close.assert_called()
        self.assertEqual(get.call_count, 2)

    @patch("manager.preview.validate_remote", side_effect=lambda url: url)
    @patch("manager.preview.requests.get")
    def test_abandoned_stream_releases_capacity_without_consuming_body(
        self, get, _validate
    ):
        relay = Relay()
        _, token = relay.start(self.channel["sources"][0], "one")
        for _ in range(12):
            response = Mock(status_code=200, headers={"Content-Type": "video/mp2t"})
            response.iter_content.return_value = iter([b"segment"])
            get.return_value = response
            with self.app.test_request_context():
                result = relay.fetch(token, "0")
                self.assertIsNotNone(result.response)
                result.close()
                result.close()
            self.assertFalse(relay.sessions[token]["responses"])
            response.close.assert_called()

    @patch("manager.preview.validate_remote", side_effect=lambda url: url)
    def test_start_is_allowlisted_and_stop_is_idempotent(self, _validate):
        response = self.client.post(
            "/api/preview/start",
            json={
                "channel": self.channel["id"],
                "source": 0,
                "owner": "test-window-123456",
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        token = response.json["token"]
        for _ in range(2):
            self.assertEqual(
                self.client.post(
                    "/api/preview/stop", json={"token": token}, headers=self.headers
                ).status_code,
                200,
            )
        self.assertEqual(self.client.get(response.json["url"]).status_code, 410)


if __name__ == "__main__":
    unittest.main()
