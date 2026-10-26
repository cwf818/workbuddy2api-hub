"""Reasoning-effort mapping: unsupported levels become the model default.

The catalog tells the gateway which levels a model accepts. Clients with a
fixed picker keep sending levels the model does not declare, and the upstream
rejects the whole request instead of choosing one. The mapping is on by default
and only rewrites the value; the setting turns it off for a byte-faithful
passthrough.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

_startup_dir = tempfile.TemporaryDirectory(prefix="effort-map-")
os.environ["ACCOUNTS_DIR"] = _startup_dir.name
os.environ["WB_PROXY_USAGE_DIR"] = _startup_dir.name
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_proxy
import wb_settings

SPECS = {
    "levels": {
        "supportedEfforts": ["low", "medium", "high"],
        "defaultEffort": "medium",
        "canDisableThinking": False,
    },
    "levels_can_disable": {
        "supportedEfforts": ["low", "high"],
        "defaultEffort": "high",
        "canDisableThinking": True,
    },
    "pinned": {"effort": "high"},
    "unknown": {},
}


def reasoning_for(model):
    return SPECS.get(model, {})


class EffortMapTests(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch.object(wb_proxy, "model_reasoning_meta",
                              side_effect=reasoning_for),
        ]
        for patcher in self._patches:
            patcher.start()
        wb_settings.set_reasoning_effort_map(wb_proxy.ACCOUNTS_DIR, True)

    def tearDown(self):
        for patcher in reversed(self._patches):
            patcher.stop()

    def mapped(self, model, effort, key="reasoning_effort"):
        body = {"model": model, key: effort, "messages": []}
        return wb_proxy.map_reasoning_effort(body, model)[key]

    def test_supported_level_is_kept_verbatim(self):
        self.assertEqual(self.mapped("levels", "low"), "low")
        self.assertEqual(self.mapped("levels", "HIGH"), "HIGH")

    def test_unsupported_level_uses_the_catalog_default(self):
        self.assertEqual(self.mapped("levels", "xhigh"), "medium")

    def test_default_falls_back_to_the_first_supported_level(self):
        with mock.patch.object(wb_proxy, "model_reasoning_meta",
                               return_value={"supportedEfforts": ["low", "high"]}):
            self.assertEqual(self.mapped("levels", "medium"), "low")

    def test_pinned_model_accepts_only_its_level(self):
        self.assertEqual(self.mapped("pinned", "high"), "high")
        self.assertEqual(self.mapped("pinned", "low"), "high")

    def test_thinking_off_is_kept_only_when_the_model_allows_it(self):
        self.assertEqual(self.mapped("levels", "none"), "medium")
        self.assertEqual(self.mapped("levels", "off"), "medium")
        self.assertEqual(self.mapped("levels_can_disable", "none"), "none")

    def test_thinking_off_is_kept_when_the_flag_is_absent(self):
        # deepseek-v4.1-flash declares levels but no canDisableThinking, and
        # "none" has always been a valid opt-out for it.
        with mock.patch.object(
                wb_proxy, "model_reasoning_meta",
                return_value={"supportedEfforts": ["low", "high"],
                              "defaultEffort": "high"}):
            self.assertEqual(self.mapped("levels", "none"), "none")

    def test_camel_case_key_keeps_its_spelling(self):
        body = {"model": "levels", "reasoningEffort": "xhigh", "messages": []}
        out = wb_proxy.map_reasoning_effort(body, "levels")
        self.assertEqual(out["reasoningEffort"], "medium")
        self.assertNotIn("reasoning_effort", out)

    def test_unknown_catalog_metadata_is_passed_through(self):
        self.assertEqual(self.mapped("unknown", "xhigh"), "xhigh")
        self.assertEqual(self.mapped("missing", "xhigh"), "xhigh")

    def test_non_string_and_empty_values_are_passed_through(self):
        self.assertEqual(self.mapped("levels", 3), 3)
        self.assertEqual(self.mapped("levels", "  "), "  ")

    def test_setting_off_keeps_the_request_verbatim(self):
        wb_settings.set_reasoning_effort_map(wb_proxy.ACCOUNTS_DIR, False)
        self.assertEqual(self.mapped("levels", "xhigh"), "xhigh")

    def test_setting_defaults_to_on(self):
        wb_settings.set_reasoning_effort_map(wb_proxy.ACCOUNTS_DIR, True)
        data = wb_settings.load(wb_proxy.ACCOUNTS_DIR)
        data.pop("reasoning_effort_map", None)
        wb_settings.save(wb_proxy.ACCOUNTS_DIR, data)
        self.assertTrue(wb_settings.reasoning_effort_map(wb_proxy.ACCOUNTS_DIR))

    def test_build_upstream_body_applies_the_mapping(self):
        payload = {
            "model": "levels",
            "reasoning_effort": "xhigh",
            "messages": [{"role": "user", "content": "hi"}],
        }
        body = wb_proxy.build_upstream_body(payload)
        self.assertEqual(body["reasoning_effort"], "medium")
        # The caller's payload is not mutated.
        self.assertEqual(payload["reasoning_effort"], "xhigh")


if __name__ == "__main__":
    unittest.main(verbosity=2)
