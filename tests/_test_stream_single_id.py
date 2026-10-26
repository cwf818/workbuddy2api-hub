"""One streaming reply must carry exactly one chunk id.

Real upstreams emit the reasoning trace under their own id (`01a1...`) while
the gateway rewrites the answer text into frames carrying its own
`chatcmpl-wb-*` id. The reply therefore left the gateway as two interleaved
streams: the reasoning half never carried a finish_reason, so a client that
keys stream state by chunk id waited on it forever - the desktop client sat on
"still generating" with the reasoning pane frozen even though the gateway had
finished the turn in seconds (both halves had arrived).

The suite runs a real gateway process-in-thread against a mock upstream whose
frames use the upstream id throughout, then asserts the client-facing stream:

  - every frame carries the same id;
  - that id is the gateway's own `chatcmpl-wb-*` one, not the upstream's;
  - the single stream carries the reasoning frames, the answer text and the
    finish frame, so no client can end up waiting on an unterminated half;
  - the trailing usage frame reuses the same id.

Run with: python tests/_test_stream_single_id.py
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

import _lifecycle as life  # noqa: E402  (shared spare-port pick)
from _isolated_dirs import isolated_data_dirs  # noqa: E402  (own data root)

_TMP = isolated_data_dirs("wb-stream-single-id-")
ACCOUNTS = os.environ["ACCOUNTS_DIR"]
USAGE_DIR = os.environ["WB_PROXY_USAGE_DIR"]
os.environ["HOME"] = _TMP.name
os.environ["USERPROFILE"] = _TMP.name
os.environ["WB_PROXY_KEY"] = "TESTKEY"

MODEL = "deepseek-v4.1-flash"
UPSTREAM_ID = "01a162ca7ab97a7787bea10000000000"


def jwt(uid="acct-stream-single-id"):
    def seg(obj):
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return seg({"alg": "none", "typ": "JWT"}) + "." + seg(
        {"uid": uid, "exp": int(time.time()) + 86400 * 30}) + ".x"


with open(os.path.join(ACCOUNTS, "acct1.json"), "w", encoding="utf-8") as fh:
    json.dump({
        "uid": "acct-stream-single-id",
        "accessToken": jwt(),
        "refreshToken": "",
        "expiresAt": int(time.time()) + 86400 * 30,
        "realm": "intl",
        "domain": "www.workbuddy.ai",
        "platform": "CLI",
        "product": "workbuddy",
        "enabled": True,
    }, fh)

# The shape the real upstream produces: the reasoning trace and the answer both
# ride the upstream id, the answer text being the part this gateway rewrites.
CHUNKS = [
    {"id": UPSTREAM_ID, "model": MODEL,
     "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]},
    {"id": UPSTREAM_ID, "model": MODEL,
     "choices": [{"index": 0, "delta": {"reasoning_content": "The user"}}]},
    {"id": UPSTREAM_ID, "model": MODEL,
     "choices": [{"index": 0, "delta": {"reasoning_content": " says hi."}}]},
    {"id": UPSTREAM_ID, "model": MODEL,
     "choices": [{"index": 0, "delta": {"content": "Hello"}}]},
    {"id": UPSTREAM_ID, "model": MODEL,
     "choices": [{"index": 0, "delta": {"content": " there"}}]},
    {"id": UPSTREAM_ID, "model": MODEL,
     "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
     "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}},
]

UPSTREAM_PORT = life.free_port()
MOCK = {"requests": []}


def sse(chunks):
    out = [("data: " + json.dumps(c) + "\n\n").encode("utf-8") for c in chunks]
    out.append(b"data: [DONE]\n\n")
    return b"".join(out)


class MockUpstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}
        MOCK["requests"].append({"path": self.path, "body": body})
        if self.path.endswith("/chat/completions"):
            payload = sse(CHUNKS)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
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


def request(method, path, body=None, headers=None, timeout=30):
    url = "http://127.0.0.1:%d%s" % (GATEWAY_PORT, path)
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def wait_ready():
    for _ in range(60):
        try:
            code, _h, _b = request("GET", "/health", timeout=2)
            if code == 200:
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

    print("[1] every frame of one reply carries the same id")
    code, _headers, body = request(
        "POST", "/v1/chat/completions",
        {"model": MODEL, "stream": True, "reasoning_effort": "high",
         "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer TESTKEY"})
    frames = parse_frames(body)
    objs = [f for f in frames if isinstance(f, dict)]
    check("200 with a parsed SSE stream", code == 200 and objs, (code, body[:200]))
    check("upstream received the request", bool(MOCK["requests"]), MOCK["requests"])

    ids = []
    for f in objs:
        if f.get("id") and f.get("id") not in ids:
            ids.append(f.get("id"))
    check("exactly one id across the whole reply", len(ids) == 1, ids)
    check("the id is the gateway's own, not the upstream's",
          ids and ids[0] != UPSTREAM_ID and str(ids[0]).startswith("chatcmpl-wb-"),
          ids)

    check("the reasoning frames are on that id",
          any("reasoning_content" in ((f.get("choices") or [{}])[0].get("delta") or {})
              for f in objs), objs[:3])
    check("the answer text is on that id",
          any((((f.get("choices") or [{}])[0].get("delta") or {}).get("content") or "")
              for f in objs), objs)
    finish = [f for f in objs
              if any((c or {}).get("finish_reason") for c in (f.get("choices") or []))]
    check("the finish frame is on that id", len(finish) == 1
          and finish[0].get("id") == ids[0], finish)
    check("[DONE] is the last frame", frames and frames[-1] == "[DONE]",
          frames[-1] if frames else None)

    print()
    print("[2] a client keyed by id sees one terminated stream")
    streams = {}
    order = []
    for f in objs:
        cid = f.get("id")
        if cid not in streams:
            streams[cid] = {"reasoning": 0, "content": 0, "finish": None}
            order.append(cid)
        d = ((f.get("choices") or [{}])[0].get("delta") or {})
        if d.get("reasoning_content"):
            streams[cid]["reasoning"] += 1
        if d.get("content"):
            streams[cid]["content"] += 1
        for c in (f.get("choices") or []):
            if (c or {}).get("finish_reason"):
                streams[cid]["finish"] = c["finish_reason"]
    check("the client sees one stream", len(order) == 1, order)
    only = streams[order[0]] if order else {}
    check("that stream carries reasoning", only.get("reasoning", 0) == 2, only)
    check("that stream carries the answer", only.get("content", 0) == 2, only)
    check("that stream is terminated", only.get("finish") == "stop", only)

    print()
    print("[3] the trailing usage frame reuses the same id")
    usage = [f for f in objs if isinstance(f.get("usage"), dict)]
    check("one usage frame", len(usage) == 1, usage)
    check("usage frame id matches", usage and usage[0].get("id") == ids[0],
          usage[0].get("id") if usage else None)
finally:
    gateway.shutdown()
    upstream.shutdown()

print()
if FAIL:
    print("FAILED (%d failures)" % FAIL)
    sys.exit(1)
print("OK")
