import functools, http.server, json, shutil, subprocess, tempfile, threading
from pathlib import Path
import unittest
from manager.store import Store
from manager.worker import Worker, SOURCE_ROOT


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class ManagerIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "FFmpeg and FFprobe are required",
    )
    def test_real_detection_and_empty_result_preserves_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            media = root / "media"
            media.mkdir()
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=320x180:rate=25",
                    "-t",
                    "8",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-g",
                    "25",
                    "-f",
                    "hls",
                    "-hls_time",
                    "2",
                    "-hls_list_size",
                    "0",
                    "-hls_flags",
                    "omit_endlist",
                    str(media / "stream.m3u8"),
                ],
                check=True,
            )
            server = http.server.ThreadingHTTPServer(
                ("127.0.0.1", 0), functools.partial(Handler, directory=media)
            )
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_port}"
            (media / "source.m3u").write_text(
                f'#EXTM3U\n#EXTINF:-1 group-title="Test",Demo\n{base}/stream.m3u8\n'
            )
            (media / "empty.m3u").write_text("#EXTM3U\n")
            data = root / "data"
            shutil.copytree(SOURCE_ROOT / "config", data / "config")
            (data / "config/demo.txt").write_text("Test,#genre#\nDemo\n")
            p = data / "config/config.ini"
            s = p.read_text()
            s = s.replace("ipv6_support = False", "ipv6_support = True")
            p.write_text(s)
            store = Store(data)
            worker = Worker(store)
            sid = store.save_source({"url": base + "/source.m3u"})
            settings = store.get("settings")
            settings.update(open_filter_speed=False, open_filter_resolution=True)
            store.set("settings", settings)
            jid = store.enqueue()
            worker.execute(jid)
            job = store.job(jid)
            assert job["status"] == "success", job
            pub = (data / "current/result.m3u").read_text()
            assert base + "/stream.m3u8" in pub
            summary = json.loads(job["summary"])
            assert summary["sources"][0]["passed"] == 1, summary
            store.save_source({"url": base + "/empty.m3u"}, sid)
            jid = store.enqueue()
            worker.execute(jid)
            assert store.job(jid)["status"] == "empty", store.job(jid)
            assert (data / "current/result.m3u").read_text() == pub

            server.shutdown()
            server.server_close()
