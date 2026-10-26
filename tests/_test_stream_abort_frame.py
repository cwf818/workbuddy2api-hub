"""A stream that dies after the first chunk must say so, not send [DONE].

Issue #549: the Chat Completions path caught a mid-body read error and
appended `data: [DONE]`, which every client reads as a normal finish. The
truncated answer then looked complete. This suite drives a real gateway
against a mock upstream that sends one chunk, promises more, and closes.

Run with: python tests/_test_stream_abort_frame.py
No network, no credentials: only loopback sockets on ephemeral ports.
"""
import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import _lifecycle as life  # noqa: E402
from _isolated_dirs import isolated_data_dirs  # noqa: E402

_TMP = isolated_data_dirs("wb-stream-abort-")
ACCOUNTS = os.environ["ACCOUNTS_DIR"]
USAGE_DIR = os.environ["WB_PROXY_USAGE_DIR"]
os.environ["HOME"] = _TMP.name
os.environ["USERPROFILE"] = _TMP.name
os.environ["WB_PROXY_KEY"] = "TESTKEY"

MODEL = "deepseek-v4.1-flash"


def jwt(uid="acct-stream-abort-1"):
    def seg(obj):
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return seg({"alg": "none", "typ": "JWT"}) + "." + seg(
        {"uid": uid, "exp": int(time.time()) + 86400 * 30}) + ".x"


with open(os.path.join(ACCOUNTS, "acct1.json"), "w", encoding="utf-8") as fh:
    json.dump({
        "uid": "acct-stream-abort-1",
        "accessToken": jwt(),
        "refreshToken": "",
        "expiresAt": int(time.time()) + 86400 * 30,
        "realm": "intl",
        "domain": "www.workbuddy.ai",
        "platform": "CLI",
        "product": "workbuddy",
        "enabled": True,
    }, fh)

UPSTREAM_PORT = life.free_port()
MOCK = {"stream_calls": 0}


class MockUpstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if self.path.endswith("/chat/completions"):
            MOCK["stream_calls"] += 1
            chunk = {"id": "up-1", "model": MODEL,
                     "choices": [{"index": 0, "delta": {"content": "partial answer"}}]}
            payload = ("data: " + json.dumps(chunk) + "\n\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            # Promise more than is sent: iter_sse_lines turns the missing
            # tail into IncompleteRead after the first chunk is delivered.
            self.send_header("Content-Length", str(len(payload) + 4096))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
            self.close_connection = True
            return
        payload = b'{"data":{}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        payload = b'{"data":{}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


upstream = ThreadingHTTPServer(("127.0.0.1", UPSTREAM_PORT), MockUpstream)
threading.Thread(target=upstream.serve_forever, daemon=True).start()

import wb_identity  # noqa: E402
import wb_proxy as P  # noqa: E402

_ENDPOINTS = dict(wb_identity._ENDPOINTS)
for key in list(_ENDPOINTS):
    _ENDPOINTS[key] = ("http://127.0.0.1:%d" % UPSTREAM_PORT, _ENDPOINTS[key][1])
wb_identity._ENDPOINTS.update(_ENDPOINTS)

GATEWAY_PORT = life.free_port()


class Args(object):
    host = "127.0.0.1"
    port = GATEWAY_PORT
    api_key = "TESTKEY"
    system_prompt = "You are a helpful assistant."
    user_agent = None
    usage_dir = USAGE_DIR
    accounts_dir = ACCOUNTS
    import_desktop = False
    lan = False
    panel_password = None
    info = None


P._apply_cli_overrides(Args())
P._bootstrap_runtime(Args())
gateway = ThreadingHTTPServer(("127.0.0.1", GATEWAY_PORT), P.Handler)
threading.Thread(target=gateway.serve_forever, daemon=True).start()

PASS = FAIL = 0


def check(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  [PASS] %s" % label)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (label, detail))


def wait_ready():
    for _ in range(60):
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/health" % GATEWAY_PORT, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def parse_frames(raw):
    frames = []
    for block in raw.decode("utf-8", "replace").split("\n\n"):
        for line in block.splitlines():
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                frames.append("[DONE]")
            elif data:
                try:
                    frames.append(json.loads(data))
                except Exception:
                    frames.append({"__raw__": data})
    return frames


try:
    if not wait_ready():
        print("  [FAIL] gateway did not start")
        sys.exit(1)

    req = urllib.request.Request(
        "http://127.0.0.1:%d/v1/chat/completions" % GATEWAY_PORT,
        data=json.dumps({"model": MODEL, "stream": True,
                         "messages": [{"role": "user", "content": "hi"}]}).encode("utf-8"),
        method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer TESTKEY")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            code, body = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        code, body = exc.code, exc.read()

    frames = parse_frames(body)
    text = "".join(
        (ch.get("delta") or {}).get("content") or ""
        for f in frames if isinstance(f, dict)
        for ch in (f.get("choices") or []))
    errors = [f for f in frames if isinstance(f, dict) and f.get("error")]

    check("200 with a parsed SSE stream", code == 200 and frames, (code, body[:200]))
    check("the delivered chunk reached the client", "partial answer" in text, text)
    check("a terminal error frame was sent", len(errors) == 1, frames)
    check("the error names the upstream abort",
          bool(errors) and errors[0]["error"].get("code") == "upstream_stream_error",
          errors)
    check("no [DONE] pretends the turn finished", "[DONE]" not in frames, frames)
    check("the stream was not retried after bytes were sent",
          MOCK["stream_calls"] == 1, MOCK["stream_calls"])
finally:
    gateway.shutdown()
    upstream.shutdown()

print("SUMMARY: PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)

