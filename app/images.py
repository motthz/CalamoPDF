"""The page pictures travel to the window over a small local web server instead of the
JavaScript bridge. Through the bridge every picture became a text of up to a few
megabytes that the window had to take in all at once, freezing scrolling and zoom while
it did; from a web address the window loads the pictures by itself, several at a time,
and drops the ones no longer needed. Only this computer can reach the server (127.0.0.1)
and only with the secret in the address, chosen at every start."""
import secrets
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class _Server(ThreadingHTTPServer):
    daemon_threads = True


def start(sessions):
    """sessions(): the open documents. Returns the base address of the pictures."""
    token = secrets.token_urlsafe(16)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            parts = u.path.strip("/").split("/")
            if len(parts) != 2 or parts[0] != token or parts[1] != "page":
                self.send_error(404)
                return
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                sid, index, scale = int(q["sid"]), int(q["i"]), float(q["s"])
                fmt = q.get("f", "auto") if q.get("f") in ("png", "jpeg", "auto") else "auto"
                s = next((x for x in sessions() if id(x) == sid), None)
                if s is None or not 0 <= index < len(s.doc):
                    self.send_error(404)
                    return
                mime, data = s.render_bytes(index, max(0.05, min(scale, 8.0)), fmt)
            except Exception:
                traceback.print_exc()
                self.send_error(500)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass                          # the window no longer wanted it (scrolled away)

    srv = _Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, name="calamo-images", daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/{token}/page"
