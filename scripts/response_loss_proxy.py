"""Local browser test fixture: forward to :8765, drop the first accepted POST reply.

Run explicitly during UI verification. It does not stop workers or delete tasks.
Only loopback is bound; close with Ctrl+C. Created tasks remain in the demo DB.
"""

import argparse
import json
import socket
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--output", type=Path, default=Path(".local/quality/response-loss.json"))
    args = parser.parse_args()
    lock = threading.Lock()
    records = []

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def forward(self):
            if not self.path.startswith("/") or self.path.startswith("//"):
                self.send_error(400)
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            request = Request(
                "http://127.0.0.1:8765" + self.path,
                data=body if self.command == "POST" else None,
                method=self.command,
                headers={
                    k: v
                    for k, v in self.headers.items()
                    if k.lower() not in {"host", "connection", "accept-encoding"}
                },
            )
            try:
                upstream = urlopen(request, timeout=15)
            except HTTPError as exc:
                upstream = exc
            data = upstream.read()
            drop = False
            if self.command == "POST" and self.path == "/api/tasks" and upstream.status == 202:
                with lock:
                    drop = not records
                    records.append(
                        {
                            "at": datetime.now(timezone.utc).isoformat(),
                            "response": json.loads(data),
                            "dropped": drop,
                            "idempotency_key": self.headers.get("Idempotency-Key"),
                            "params": json.loads(body),
                        }
                    )
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
            if drop:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(upstream.status)
            for key in ("Content-Type", "Content-Disposition"):
                if value := upstream.headers.get(key):
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        do_GET = forward
        do_POST = forward

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Proxy)
    print(f"Response-loss fixture: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
