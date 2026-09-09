"""Serve site/ locally and forward /api/* to a running API.

Dev only, and deliberately so: in production CloudFront does this forwarding, so the page
must call /api/... with no host in it. Without a local equivalent the page would need a
"which environment am I in" branch, which is a second code path that only ever runs on my
laptop -- the D35 shape.

    python scripts\\dev_site.py            # proxies to a local `uvicorn api:app`
    $env:RAILCAST_API = "https://xxxx.lambda-url.eu-west-1.on.aws"; python scripts\\dev_site.py
"""

import functools
import json
import os
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UPSTREAM = os.environ.get("RAILCAST_API", "http://127.0.0.1:8000").rstrip("/")
PORT = int(os.environ.get("RAILCAST_PORT", "8765"))
ROOT = Path(__file__).resolve().parent.parent / "site"


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/"):
            return self.proxy()
        return super().do_GET()

    def proxy(self):
        url = UPSTREAM + self.path[len("/api"):]
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                body, status = r.read(), r.status
        except urllib.error.HTTPError as e:
            body, status = e.read(), e.code
        except Exception as e:
            body = json.dumps({"detail": f"{type(e).__name__}: {e}"}).encode()
            status = 502
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"  {self.path} -> {args[1] if len(args) > 1 else ''}")


if __name__ == "__main__":
    print(f"site  http://localhost:{PORT}\n/api  -> {UPSTREAM}")
    ThreadingHTTPServer(("", PORT), functools.partial(Handler, directory=str(ROOT))).serve_forever()
