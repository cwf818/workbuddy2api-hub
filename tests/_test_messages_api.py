"""Pure translation tests for the Anthropic-compatible /v1/messages endpoint."""
import json
import os
import sys

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

print("SUMMARY: PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
