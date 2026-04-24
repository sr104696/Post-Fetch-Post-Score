#!/usr/bin/env python3
"""
Reddit Gem Finder — local proxy server
Run:  python server.py
Then open http://localhost:8000/reddit_gem_finder.html
  (or just double-click launch.bat)

Why a proxy?
  Reddit's JSON API blocks browser requests that lack a real User-Agent.
  This server adds one, forwards the request, and returns the JSON —
  no CORS issues, no API key needed.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt":  "text/plain; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):

    # ── Logging ───────────────────────────────────────────────────────────
    def log_message(self, fmt, *args):
        print(f"  {self.address_string()}  {fmt % args}", flush=True)

    # ── CORS headers (needed so the browser page can read responses) ──────
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        # Reconstruct the raw path from the request line so that
        # query strings with commas/percent-encoding aren't mangled.
        try:
            raw = self.raw_requestline.decode("utf-8", errors="replace").strip()
            raw_path = raw.split(" ")[1] if " " in raw else self.path
        except Exception:
            raw_path = self.path

        if raw_path.startswith("/proxy/"):
            self._proxy(raw_path[7:])   # strip /proxy/ prefix
        else:
            self._static(raw_path)

    # ── Static file server ────────────────────────────────────────────────
    def _static(self, path):
        path = urllib.parse.unquote(path.lstrip("/")) or "reddit_gem_finder.html"
        # Prevent directory traversal
        safe = os.path.realpath(os.path.join(os.getcwd(), path))
        if not safe.startswith(os.getcwd()):
            self._err(403, "Forbidden"); return
        if not os.path.isfile(safe):
            self._err(404, "Not found"); return

        ext  = os.path.splitext(safe)[1].lower()
        mime = MIME.get(ext, "application/octet-stream")
        with open(safe, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type",   mime)
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    # ── Proxy ─────────────────────────────────────────────────────────────
    def _proxy(self, target):
        if not target.startswith(("http://", "https://")):
            self._err(400, f"Bad URL: {target[:80]}"); return

        print(f"  → proxy: {target[:120]}", flush=True)

        req = urllib.request.Request(
            target,
            headers={
                "User-Agent":      USER_AGENT,
                "Accept":          "application/json",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
            self.send_response(200)
            self.send_header("Content-Type",   "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        except urllib.error.HTTPError as e:
            body = e.read()
            print(f"  ✗ Reddit HTTP {e.code}: {body[:200]}", flush=True)
            if e.code == 429:
                self._err(429, "Reddit rate-limited us — wait a moment and retry.")
            elif e.code == 403:
                self._err(403, "Reddit returned 403 — wait a bit or reduce batch size.")
            else:
                self._err(e.code, f"Reddit HTTP {e.code}: {body[:200].decode('utf-8', errors='replace')}")

        except urllib.error.URLError as e:
            print(f"  ✗ URLError: {e.reason}", flush=True)
            self._err(502, f"Could not reach Reddit: {e.reason}")

        except TimeoutError:
            print("  ✗ Timeout", flush=True)
            self._err(504, "Reddit timed out")

        except Exception as e:
            print(f"  ✗ Unexpected: {e}", flush=True)
            self._err(500, str(e))

    # ── Error helper ──────────────────────────────────────────────────────
    def _err(self, code, msg):
        body = json.dumps({"error": msg, "code": code}).encode()
        self.send_response(code)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print()
    print("  Reddit Gem Finder — proxy server")
    print(f"  Serving files from : {os.getcwd()}")
    print(f"  Listening on       : http://localhost:{PORT}")
    print(f"  Open               : http://localhost:{PORT}/reddit_gem_finder.html")
    print()
    print("  Press Ctrl+C to stop.")
    print()
    try:
        HTTPServer(("localhost", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        sys.exit(0)
