"""Tests for fingerprint sanitization, including PR #62 OmO Sisyphus-Junior fix."""
import json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ACCOUNTS_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_acc_test"))
os.environ.setdefault("USAGE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_use_test"))

import wb_proxy as P

class FingerprintSanitizationTests(unittest.TestCase):
    def test_omo_junior_fingerprint_neutralized(self):
        fp = "Sisyphus-Junior - Focused executor from OhMyOpenCode"
        # 1. Exact string rewritten
        self.assertEqual(P.sanitize_text(fp), "Sisyphus-Junior - Focused executor")

        # 2. Case insensitive
        self.assertEqual(P.sanitize_text(fp.lower()), "Sisyphus-Junior - Focused executor")

        # 3. Embedded in context
        text = f"You are a subagent. {fp}. Complete the task."
        expected = "You are a subagent. Sisyphus-Junior - Focused executor. Complete the task."
        self.assertEqual(P.sanitize_text(text), expected)

    def test_omo_partial_tokens_untouched(self):
        # Tokens alone should not be stripped or modified
        self.assertEqual(P.sanitize_text("Sisyphus-Junior alone"), "Sisyphus-Junior alone")
        self.assertEqual(P.sanitize_text("from OhMyOpenCode alone"), "from OhMyOpenCode alone")
        self.assertEqual(P.sanitize_text("Sisyphus master agent"), "Sisyphus master agent")

    def test_claude_code_agent_context_is_neutralized_before_upstream(self):
        phrase = "A user kicked off a Claude Code agent to do a coding task and walked away"
        outbound = P.build_upstream_body({
            "model": "m",
            "messages": [{"role": "system", "content": phrase},
                         {"role": "user", "content": "Continue the task."}],
        })
        system = outbound["messages"][0]["content"]
        self.assertNotIn("Claude Code agent", system)
        self.assertEqual(system, "A user kicked off a coding task and walked away")
        responses_outbound = P.build_upstream_body(P.responses_to_chat({
            "model": "m", "instructions": phrase, "input": "Continue the task.",
        }))
        self.assertNotIn("Claude Code agent", responses_outbound["messages"][0]["content"])

    def test_claude_agent_sdk_identity_is_neutralized(self):
        phrase = "You are a Claude agent, built on Anthropic's Claude Agent SDK"
        self.assertEqual(P.sanitize_text(phrase), "You are a coding agent")
        self.assertEqual(P.sanitize_text(phrase.lower()), "You are a coding agent")

    def test_claude_code_agent_identity_is_neutralized(self):
        phrase = "You are an agent for Claude Code, Anthropic's official CLI for Claude"
        self.assertEqual(P.sanitize_text(phrase), "You are a coding agent")
        self.assertEqual(P.sanitize_text(phrase.lower()), "You are a coding agent")

    def test_sanitize_messages_covers_all_roles_and_tool_args(self):
        fp = "Sisyphus-Junior - Focused executor from OhMyOpenCode"
        clean = "Sisyphus-Junior - Focused executor"
        messages = [
            {"role": "system", "content": f"System: {fp}"},
            {"role": "user", "content": f"User: {fp}"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "run", "arguments": json.dumps({"query": fp})}
                }]
            },
            {"role": "tool", "tool_call_id": "call_1", "content": f"Tool output: {fp}"}
        ]
        sanitized = P.sanitize_messages(messages)
        self.assertIn(clean, sanitized[0]["content"])
        self.assertNotIn("from OhMyOpenCode", sanitized[0]["content"])
        self.assertIn(clean, sanitized[1]["content"])
        self.assertNotIn("from OhMyOpenCode", sanitized[1]["content"])
        args = json.loads(sanitized[2]["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(args["query"], clean)
        self.assertIn(clean, sanitized[3]["content"])
        self.assertNotIn("from OhMyOpenCode", sanitized[3]["content"])

if __name__ == "__main__":
    unittest.main()
