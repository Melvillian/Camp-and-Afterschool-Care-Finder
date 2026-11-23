import contextlib
import http.client
import io
import json
import sys
import tempfile
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import dev_server as d

CSV = "org_id,org_name\na,Alpha\n"


class Quiet(d.LiveReloadHandler):
    def log_message(self, *args):
        pass


class SaveCsv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(self.root)))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, path="data/programs.csv", content=CSV, headers=None, body=None, url="/__save_csv"):
        send = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        for key, value in (headers or {}).items():
            if value is None:
                send.pop(key, None)
            else:
                send[key] = value
        if body is None:
            body = json.dumps({"path": path, "content": content})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest("POST", url, skip_host=True)
            data = body.encode("utf-8")
            for key, value in send.items():
                conn.putheader(key, value)
            conn.putheader("Content-Length", str(len(data)))
            conn.endheaders(data)
            return conn.getresponse().status
        finally:
            conn.close()

    def written(self):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file())

    def test_saves_the_two_data_files(self):
        self.assertEqual(self.post("data/programs.csv"), 200)
        self.assertEqual(self.post("data/organizations.csv", headers={"Origin": f"http://localhost:{self.port}",
                                                                      "Host": f"localhost:{self.port}"}), 200)
        self.assertEqual(self.written(), ["data/organizations.csv", "data/programs.csv"])
        self.assertEqual((self.root / "data/programs.csv").read_bytes(), CSV.encode())

    def test_keeps_line_endings_as_sent(self):
        self.assertEqual(self.post(content="a,b\r\n1,2\r\n"), 200)
        self.assertEqual((self.root / "data/programs.csv").read_bytes(), b"a,b\r\n1,2\r\n")

    def test_content_type_may_carry_a_charset(self):
        self.assertEqual(self.post(headers={"Content-Type": "application/json; charset=utf-8"}), 200)

    def test_refuses_any_other_path(self):
        for path in ("index.html", "data.js", "data/sources.csv", "data/ingest_config.json", "scripts/dev_server.py",
                     "data/../data/programs.csv", "./data/programs.csv", "/data/programs.csv", "data/programs.csv ",
                     "DATA/PROGRAMS.CSV", "data/staging/2027/x.csv", ".claude/settings.json", "", None, 7,
                     ["data/programs.csv"]):
            self.assertEqual(self.post(path), 400, path)
        self.assertEqual(self.written(), [])

    def test_refuses_other_content_types(self):
        for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data", "", None):
            self.assertEqual(self.post(headers={"Content-Type": ctype}), 403, ctype)
        self.assertEqual(self.written(), [])

    def test_refuses_a_foreign_origin(self):
        for origin in ("https://evil.example", f"http://evil.example:{self.port}", "null",
                       f"http://localhost:{self.port + 1}", f"https://localhost:{self.port}", "http://localhost",
                       f"http://localhost.evil.example:{self.port}", f"http://127.0.0.1.evil.example:{self.port}"):
            self.assertEqual(self.post(headers={"Origin": origin}), 403, origin)
        self.assertEqual(self.written(), [])

    def test_refuses_a_foreign_host(self):
        for host in (f"evil.example:{self.port}", "evil.example", f"localhost:{self.port + 1}", "localhost",
                     f"192.168.1.5:{self.port}", f"localhost.evil.example:{self.port}", "", None):
            self.assertEqual(self.post(headers={"Host": host}), 403, host)
        self.assertEqual(self.written(), [])

    def test_refuses_bad_bodies(self):
        self.assertEqual(self.post(body="{not json"), 400)
        self.assertEqual(self.post(body="[]"), 400)
        self.assertEqual(self.post(content=""), 400)
        self.assertEqual(self.post(content=None), 400)
        self.assertEqual(self.post(content={"a": 1}), 400)
        self.assertEqual(self.written(), [])

    def test_other_post_paths_are_not_found(self):
        self.assertEqual(self.post(url="/__save_anything"), 404)
        self.assertEqual(self.post(url="/data/programs.csv"), 404)
        self.assertEqual(self.written(), [])


class Host(unittest.TestCase):
    def test_loopback_hosts_are_accepted(self):
        self.assertEqual(d.parse_args([]).host, "127.0.0.1")
        self.assertEqual(d.parse_args(["--host", "localhost"]).host, "localhost")

    def test_other_hosts_are_refused(self):
        for host in ("0.0.0.0", "192.168.1.5", "", "::", "example.org"):
            with self.subTest(host=host), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    d.parse_args(["--host", host])


if __name__ == "__main__":
    unittest.main()
