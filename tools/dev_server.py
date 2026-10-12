"""Dev harness: serves the UI over HTTP and bridges window.pywebview.api calls to
the real Python Api, so the interface can be exercised in any browser.
Usage: python tools/dev_server.py [file.pdf]   ->  http://127.0.0.1:8765/"""
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
os.environ.setdefault("CALAMO_DATA", os.path.join(ROOT, "build", "devdata"))
import main  # noqa: E402

api = main.Api()
if len(sys.argv) > 1:
    api.startup_path = os.path.abspath(sys.argv[1])
UI = os.path.join(ROOT, "app", "ui")
SHIM = b"""<script>
window.pywebview = { api: new Proxy({}, { get: (_, name) => async (...args) => {
  const r = await fetch('/api/' + name, { method: 'POST', body: JSON.stringify(args) });
  return r.json(); } }) };
addEventListener('DOMContentLoaded', () => setTimeout(() => dispatchEvent(new Event('pywebviewready')), 50));
if (location.search.includes('demo')) addEventListener('load', () => setTimeout(async () => {
  await analyze(false);
  const P = S.pages[0], l = (S.inkCache[0] || [])[1];
  if (l) openInk(P, l, document.querySelector('.ink[data-id="1"]'));
}, 1500));
</script>"""


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=UI, **kw)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            with open(os.path.join(UI, "index.html"), "rb") as f:
                html = f.read().replace(b"<script src=\"app.js\">", SHIM + b"<script src=\"app.js\">")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(html)
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        name = self.path.split("/api/")[-1]
        args = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"[]")
        fn = getattr(api, name, None)
        try:
            res = fn(*args) if fn and not name.startswith("_") else {"ok": False, "error": "n/a in dev: " + name}
        except Exception as e:
            res = {"ok": False, "error": str(e)}
        body = json.dumps(res).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


print("http://127.0.0.1:8765/")
ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
