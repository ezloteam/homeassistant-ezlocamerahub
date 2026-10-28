"""A stand-in for ha-camerasd: serves /app/standalone/status on its API address.

The supervisor gives the engine a clean environment, so the test switches are
files next to the cache directory: the fake appends its environment to
env.jsonl, exits at once if "fail" exists, and exits after the number of
seconds in "exit_after". It also prints a log line holding credentials.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sys
import threading

control = os.path.dirname(os.environ["HA_CAMERASD_STANDALONE_CACHE"])
with open(os.path.join(control, "env.jsonl"), "a") as fh:
    fh.write(json.dumps(dict(os.environ)) + "\n")
print(
    "INFO starting url=rtsp://admin:hunter2@10.0.0.9/stream password=hunter2",
    flush=True,
)
if os.path.exists(os.path.join(control, "fail")):
    sys.exit(3)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"provisioned": True, "hub": {"serial": "1"}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


host, port = os.environ["HA_CAMERASD_STANDALONE_API_ADDR"].rsplit(":", 1)
server = ThreadingHTTPServer((host, int(port)), Handler)
exit_after_file = os.path.join(control, "exit_after")
if os.path.exists(exit_after_file):
    with open(exit_after_file) as fh:
        threading.Timer(float(fh.read()), lambda: os._exit(1)).start()
server.serve_forever()
