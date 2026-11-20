#!/usr/bin/env python3
"""Headless-Chrome smoke test: serves the site with a fixture data.js and
checks the rendered DOM. Exit 0 = all pass, 1 = a check failed, 2 = no Chrome.

Set CHROME to override the browser path.
"""
import functools
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "data.fixture.js"
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

failures = []
_state = {"port": None, "chrome": None}


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def find_chrome():
    env = os.environ.get("CHROME")
    if env:
        return env
    if os.path.exists(MAC_CHROME):
        return MAC_CHROME
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def check(name, condition):
    """Record a failure and keep going."""
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        failures.append(name)


def dump_dom(path):
    """Rendered HTML of http://127.0.0.1:<port>/<path>."""
    with tempfile.TemporaryDirectory() as profile:
        result = subprocess.run(
            [
                _state["chrome"], "--headless=new", "--disable-gpu",
                "--virtual-time-budget=5000", "--dump-dom",
                f"--user-data-dir={profile}",
                f"http://127.0.0.1:{_state['port']}/{path}",
            ],
            capture_output=True, text=True, timeout=120,
        )
    return result.stdout


def stage_site(dest):
    for pattern in ("*.html", "*.js", "*.css", "favicon.svg"):
        for f in ROOT.glob(pattern):
            shutil.copy(f, dest)
    shutil.copy(FIXTURE, Path(dest) / "data.js")


def main():
    chrome = find_chrome()
    if not chrome:
        print("Chrome not found; set CHROME to its path", file=sys.stderr)
        return 2
    _state["chrome"] = chrome

    with tempfile.TemporaryDirectory() as site:
        stage_site(site)
        handler = functools.partial(QuietHandler, directory=site)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        _state["port"] = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            dom = dump_dom("afterschool.html")
            check("afterschool: count line", "of <strong>2</strong> afterschool programs" in dom)
            check("afterschool: two cards", dom.count('class="program-card') == 2)
            dom = dump_dom("index.html")
            check("camps: count line", "of <strong>4</strong> camps" in dom)
            check("xss: no injected img element", '<img src="x"' not in dom)
            check("xss: title untouched", "<title>Summer Camps" in dom)
            check("xss: name shown as text", "&lt;img src=x" in dom)
            check("xss: description shown as text", "&lt;b&gt;bold&lt;/b&gt;" in dom)
            check("unsafe url: no javascript link", 'href="javascript:' not in dom)
            check("safe url: link kept", 'href="https://example.org/camp"' in dom)
        finally:
            server.shutdown()

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
