import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        except Exception:
            traceback.print_exc()

    def do_HEAD(self):
        try:
            self.send_response(200)
            self.end_headers()
        except Exception:
            traceback.print_exc()

    def log_message(self, format, *args):
        pass


def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Listening on 0.0.0.0:{port}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()


def start_web_server_in_thread():
    t = threading.Thread(target=start_web_server, daemon=True)
    t.start()
    return t