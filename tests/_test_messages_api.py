"""Pure translation tests for the Anthropic-compatible /v1/messages endpoint."""
import json
import os
import sys
from io import BytesIO
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ACCOUNTS_DIR", os.path.join(os.path.dirname(__file__), "_acc"))
os.environ.setdefault("USAGE_DIR", os.path.join(os.path.dirname(__file__), "_use"))

import wb_proxy as P

PASS = FAIL = 0


def check(label, condition, extra=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  [PASS] " + label)
    else:
        FAIL += 1
        print("  [FAIL] " + label + ("  " + str(extra) if extra else ""))


request = {
    "model": "m",
    "system": [{"type": "text", "text": "Be concise."}],
    "max_tokens": 123,
    "tools": [{"name": "lookup", "description": "Find a value",
               "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}],
    "tool_choice": {"type": "any"},
    "messages": [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1",
                                            "name": "lookup", "input": {"q": "x"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                       "content": "found"}]},
    ],
}
chat = P.messages_to_chat(request)
check("system becomes a system message", chat["messages"][0]["role"] == "system")
check("max_tokens is preserved", chat.get("max_tokens") == 123)
check("Anthropic tools become function tools", chat["tools"][0]["name"] == "lookup")
check("any tool choice becomes required", chat.get("tool_choice") == "required")
check("tool-use IDs survive", chat["messages"][1]["tool_calls"][0]["id"] == "toolu_1")
check("tool results become OpenAI tool messages", chat["messages"][2]["tool_call_id"] == "toolu_1")

result = P.chat_to_messages({
    "choices": [{"finish_reason": "tool_calls", "message": {
        "content": "I will look it up.",
        "tool_calls": [{"id": "call_1", "function": {"name": "lookup", "arguments": '{"q":"x"}'}}],
    }}],
    "usage": {"prompt_tokens": 8, "completion_tokens": 4},
}, "m")
check("tool calls use Anthropic stop reason", result["stop_reason"] == "tool_use")
check("text content is returned", result["content"][0]["text"] == "I will look it up.")
check("tool arguments are JSON objects", result["content"][1]["input"] == {"q": "x"})
check("usage names match Anthropic", result["usage"] == {"input_tokens": 8, "output_tokens": 4})

print()
print("[2] fragmented upstream tool arguments are preserved for Anthropic tools")
def chunk(delta, finish=None):
    choice = {"delta": delta}
    if finish:
        choice["finish_reason"] = finish
    return ("data: " + json.dumps({"choices": [choice]}) + "\n").encode()

streamed = P.aggregate_stream(iter([
    chunk({"tool_calls": [{"index": 0, "id": "call_read",
                           "function": {"name": "Read", "arguments": '{"file_'}}]}),
    chunk({"tool_calls": [{"index": 0,
                           "function": {"arguments": 'path":"C:/repo/a.py"}'}}]}, "tool_calls"),
]), "m", None)
read_result = P.chat_to_messages(streamed, "m")
check("fragmented Read arguments retain file_path",
      read_result["content"][0]["input"] == {"file_path": "C:/repo/a.py"},
      read_result["content"][0])
try:
    P.chat_to_messages({"choices": [{"message": {"tool_calls": [{
        "function": {"name": "Read", "arguments": '{"file_path":'}
    }]}}]}, "m")
    invalid_arguments_rejected = False
except ValueError:
    invalid_arguments_rejected = True
check("malformed tool arguments fail explicitly instead of becoming empty input",
      invalid_arguments_rejected)

class CaptureMessagesStream:
    path = "/v1/messages"

    def __init__(self):
        self.wfile = BytesIO()

    def send_response(self, _code):
        pass

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass


captured = CaptureMessagesStream()
saved_record_usage = P.record_usage
usage_records = []
P.record_usage = lambda *_args, **kwargs: usage_records.append(kwargs)
try:
    P.Handler._messages_stream_response(captured, iter([
        chunk({"tool_calls": [{"index": 0, "id": "call_read",
                               "function": {"name": "Read",
                                            "arguments": '{"file_path":"C:/repo/a.py"}'}}]},
              "tool_calls"),
    ]), "m", None, SimpleNamespace(uid="test-account"), P.time.time() - 0.01)
finally:
    P.record_usage = saved_record_usage

frames = []
for frame in captured.wfile.getvalue().decode().strip().split("\n\n"):
    data_line = next((line for line in frame.splitlines() if line.startswith("data: ")), "")
    if data_line:
        frames.append(json.loads(data_line[6:]))
tool_start = next(frame for frame in frames
                  if frame.get("type") == "content_block_start"
                  and frame.get("content_block", {}).get("type") == "tool_use")
tool_delta = next(frame for frame in frames
                  if frame.get("type") == "content_block_delta"
                  and frame.get("delta", {}).get("type") == "input_json_delta")
check("tool-use start event has an empty input object",
      tool_start["content_block"]["input"] == {}, tool_start)
check("tool-use JSON delta carries file_path",
      json.loads(tool_delta["delta"]["partial_json"]) == {"file_path": "C:/repo/a.py"},
      tool_delta)
check("stream usage records time to first chunk and generation duration",
      bool(usage_records) and usage_records[0].get("ttft_ms") is not None
      and usage_records[0].get("gen_ms") is not None,
      usage_records)

print()
print("[3] Messages requests use the shared upstream identity sanitization")
fingerprint = "Sisyphus-Junior - Focused executor from OhMyOpenCode"
dirty = P.messages_to_chat({
    "model": "m",
    "system": "You are Claude Code, Anthropic's official CLI for Claude. " + fingerprint,
    "messages": [
        {"role": "assistant", "content": [{
            "type": "tool_use", "id": "toolu_hidden", "name": "run",
            "input": {"header": "x-anthropic-billing-header: hidden", "agent": fingerprint},
        }]},
        {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "toolu_hidden",
            "content": "cc_entrypoint=claude; cc_version=2.0.0; " + fingerprint,
        }]},
    ],
})
outbound = P.build_upstream_body(dirty)
serialized = json.dumps(outbound, ensure_ascii=False)
check("system agent signature is rewritten",
      "Anthropic's official CLI for Claude" not in outbound["messages"][0]["content"])
check("framework fingerprint is removed from every outbound field",
      "from OhMyOpenCode" not in serialized)
check("Anthropic billing header fingerprint is removed from tool arguments",
      "x-anthropic-billing-header" not in serialized)
check("Claude Code entrypoint marker is removed from tool results",
      "cc_entrypoint=" not in serialized)
check("Claude Code version marker is removed when it is the only fingerprint",
      P.sanitize_text("cc_version=2.0.0") == "")

print("SUMMARY: PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
