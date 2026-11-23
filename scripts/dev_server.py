#!/usr/bin/env python3
"""Local dev server with lightweight live-reload for static files.

It also lets the Admin page save data/programs.csv and data/organizations.csv
(POST /__save_csv), so it binds only to this machine.

Run from project root:
    python scripts/dev_server.py

Then open:
    http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WATCH_EXTENSIONS = {".html", ".css", ".js", ".json", ".csv"}
POLL_INTERVAL_SECONDS = 1.0

# /__save_csv writes files, so it is limited to these two paths and to requests
# that come from a page this server served on this machine.
SAVE_ALLOWLIST = frozenset({"data/programs.csv", "data/organizations.csv"})
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1"})


def _is_local(netloc: str, port: int, default_port: int = 80) -> bool:
    """True when `netloc` ("host" or "host:port") is a loopback name on `port`."""
    host, sep, port_text = (netloc or "").strip().lower().rpartition(":")
    if not sep:
        host, port_text = port_text, str(default_port)
    return host in LOOPBACK_HOSTS and port_text == str(port)


def save_refusal(headers, port: int) -> str:
    """Why a /__save_csv request must be refused, or "" when it may go ahead.

    Host must be this server (stops DNS rebinding), Origin, when sent, must be
    this server (stops other sites' pages posting here), and the body must be
    declared as JSON (a cross-site form or no-cors fetch cannot send that).
    """
    if not _is_local(headers.get("Host", ""), port):
        return "Host is not this local server"
    origin = headers.get("Origin")
    if origin is not None:
        parts = urlparse(origin)
        if parts.scheme != "http" or not _is_local(parts.netloc, port):
            return "Origin is not this local server"
    content_type = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return "Content-Type must be application/json"
    return ""


LIVE_RELOAD_SNIPPET = """
<script>
(function () {
  var current = null;

  function check() {
    fetch('/__reload_version', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (current === null) {
          current = data.version;
          return;
        }
        if (data.version !== current) {
          location.reload();
        }
      })
      .catch(function () {
        // Keep polling even if the endpoint is briefly unavailable.
      });
  }

  check();
  setInterval(check, 1000);
})();
</script>
""".strip()


class VersionTracker:
    """Tracks the newest file modification timestamp in the workspace."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()
        self._version = "0"

    def get(self) -> str:
        with self._lock:
            return self._version

    def set(self, value: str) -> None:
        with self._lock:
            self._version = value


def compute_workspace_version(root: Path) -> str:
    latest_mtime = 0.0
    latest_file = ""

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip VCS/cache folders for speed.
        dirnames[:] = [
            d
            for d in dirnames
            if d not in {".git", "__pycache__", ".venv", "venv"}
        ]

        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() not in WATCH_EXTENSIONS:
                continue

            try:
                stat = path.stat()
            except OSError:
                continue

            if stat.st_mtime > latest_mtime:
                latest_mtime = stat.st_mtime
                latest_file = str(path.relative_to(root)).replace("\\", "/")

    # Include file name so multiple files saved in same timestamp still update.
    return f"{latest_mtime:.6f}:{latest_file}"


def start_watcher(tracker: VersionTracker, interval: float) -> None:
    while True:
        version = compute_workspace_version(tracker.root)
        if version != tracker.get():
            tracker.set(version)
            print(f"[reload] {version}")
        time.sleep(interval)


class LiveReloadHandler(SimpleHTTPRequestHandler):
    tracker: VersionTracker

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/__reload_version":
            payload = json.dumps(
                {"version": self.tracker.get()}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        return super().do_GET()

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path != "/__save_csv":
            self.send_response(404)
            self.end_headers()
            return

        refusal = save_refusal(self.headers, self.server.server_address[1])
        if refusal:
            self._send_json(403, {"error": refusal})
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(content_length).decode("utf-8"))
            file_path = data.get("path") if isinstance(data, dict) else None
            csv_content = data.get("content") if isinstance(data, dict) else None
        except (ValueError, UnicodeDecodeError) as e:
            self._send_json(400, {"error": str(e)})
            return

        # Only the two data files the Admin page edits may be written.
        if not isinstance(file_path, str) or file_path not in SAVE_ALLOWLIST:
            self._send_json(400, {"error": "Invalid path"})
            return
        if not isinstance(csv_content, str) or not csv_content.strip():
            self._send_json(400, {"error": "content must be non-empty text"})
            return

        try:
            target_path = Path(self.directory) / file_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            # newline="" keeps the line endings exactly as sent.
            with open(target_path, "w", encoding="utf-8", newline="") as f:
                f.write(csv_content)
        except OSError as e:
            self._send_json(500, {"error": str(e)})
            return
        self._send_json(200, {"success": True})

    def send_head(self):
        parsed = urlparse(self.path)

        if parsed.path.endswith(".html") or parsed.path in {"", "/"}:
            local = parsed.path.lstrip("/") or "index.html"
            target = Path(self.directory) / local
            if target.exists() and target.is_file():
                try:
                    html = target.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    # Fallback to normal behavior for non-UTF8 files.
                    return super().send_head()

                if "__reload_version" not in html:
                    lower = html.lower()
                    if "</body>" in lower:
                        idx = lower.rfind("</body>")
                        html = (
                            html[:idx]
                            + "\n"
                            + LIVE_RELOAD_SNIPPET
                            + "\n"
                            + html[idx:]
                        )
                    else:
                        html = html + "\n" + LIVE_RELOAD_SNIPPET + "\n"

                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return None

        return super().send_head()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Static dev server with live reload"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind: 127.0.0.1 (default) or localhost",
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port to bind (default: 8000)"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=POLL_INTERVAL_SECONDS,
        help=(
            "File watch interval in seconds "
            f"(default: {POLL_INTERVAL_SECONDS})"
        ),
    )
    args = parser.parse_args(argv)
    if args.host not in LOOPBACK_HOSTS:
        # The server can write the data files, so it never listens beyond this machine.
        parser.error(
            f"--host must be one of {sorted(LOOPBACK_HOSTS)}; "
            "this server can write data files and is for local use only"
        )
    return args


def main() -> None:
    args = parse_args()

    tracker = VersionTracker(PROJECT_ROOT)
    tracker.set(compute_workspace_version(PROJECT_ROOT))

    watcher = threading.Thread(
        target=start_watcher,
        args=(tracker, args.interval),
        daemon=True,
    )
    watcher.start()

    # Bind tracker on the handler class, not on functools.partial — partial()
    # is a wrapper object; instances only see attributes on LiveReloadHandler.
    LiveReloadHandler.tracker = tracker
    handler_cls = partial(LiveReloadHandler, directory=str(PROJECT_ROOT))

    server = ThreadingHTTPServer((args.host, args.port), handler_cls)

    print(f"Serving {PROJECT_ROOT}")
    print(f"URL: http://{args.host}:{args.port}")
    print(
        "Live reload is enabled. Save a file and the browser refreshes "
        "automatically."
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
