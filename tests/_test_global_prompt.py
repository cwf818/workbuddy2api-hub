"""Global prompt: an inline system prompt the panel can edit without a file.

The gateway already had passthrough/custom/append modes, but the custom text
could only come from a file path. `prompt.text` adds an inline body that wins
over the file, so a container deployment can change the prompt without mounting
anything. Passthrough stays the default and changes nothing.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _isolated_dirs import isolated_data_dirs  # noqa: E402

_TMP = isolated_data_dirs("wb-global-prompt-")

import wb_proxy  # noqa: E402
import wb_prompt  # noqa: E402
import wb_settings  # noqa: E402

ACCOUNTS = os.path.join(_TMP.name, "accounts")


class LoadPromptTests(unittest.TestCase):
    def test_inline_text_wins_over_the_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as fh:
            fh.write("from file")
            path = fh.name
        try:
            self.assertEqual(
                wb_prompt.load_prompt("custom", path, "from panel"),
                "from panel")
        finally:
            os.unlink(path)

    def test_empty_inline_text_falls_back_to_the_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as fh:
            fh.write("from file")
            path = fh.name
        try:
            self.assertEqual(wb_prompt.load_prompt("custom", path, "   "),
                             "from file")
        finally:
            os.unlink(path)

    def test_no_file_and_no_text_uses_the_builtin_default(self):
        self.assertEqual(wb_prompt.load_prompt("append", "", ""),
                         wb_prompt.DEFAULT_PROMPT)


class PromptConfigTests(unittest.TestCase):
    def test_defaults_keep_passthrough_and_empty_text(self):
        cfg = wb_settings.prompt_config(ACCOUNTS)
        self.assertEqual(cfg["mode"], "passthrough")
        self.assertEqual(cfg["text"], "")

    def test_inline_text_round_trips(self):
        stored = wb_settings.set_prompt_config(
            ACCOUNTS, {"mode": "append", "text": "gateway prompt"})
        self.assertEqual(stored["text"], "gateway prompt")
        self.assertEqual(wb_settings.prompt_config(ACCOUNTS)["text"],
                         "gateway prompt")

    def test_text_type_and_length_are_validated(self):
        with self.assertRaises(ValueError):
            wb_settings.validate_prompt_patch({"text": 3})
        too_long = "x" * (wb_settings.PROMPT_TEXT_MAX + 1)
        with self.assertRaises(ValueError):
            wb_settings.validate_prompt_patch({"text": too_long})

    def test_unknown_mode_is_rejected_on_save_and_falls_back_on_read(self):
        # The save path is strict; a hand-edited file is read leniently.
        with self.assertRaises(ValueError):
            wb_settings.validate_prompt_patch({"mode": "nope"})
        data = wb_settings.load(ACCOUNTS)
        data["prompt"] = {"mode": "nope", "text": ""}
        wb_settings.save(ACCOUNTS, data)
        self.assertEqual(wb_settings.prompt_config(ACCOUNTS)["mode"],
                         "passthrough")


class ApplyPromptTests(unittest.TestCase):
    def setUp(self):
        self.old_dir = wb_proxy.ACCOUNTS_DIR
        wb_proxy.ACCOUNTS_DIR = ACCOUNTS
        wb_proxy.PROMPT_DEGRADE.reset()
        wb_settings.set_prompt_config(ACCOUNTS, {"mode": "passthrough",
                                                 "text": ""})

    def tearDown(self):
        wb_proxy.ACCOUNTS_DIR = self.old_dir
        wb_proxy.PROMPT_DEGRADE.reset()
        wb_settings.set_prompt_config(ACCOUNTS, {"mode": "passthrough",
                                                 "text": ""})

    def test_passthrough_keeps_client_messages(self):
        messages = [{"role": "system", "content": "client"},
                    {"role": "user", "content": "hi"}]
        self.assertIs(wb_proxy.apply_prompt_mode(messages), messages)

    def test_append_inserts_the_inline_text(self):
        wb_settings.set_prompt_config(ACCOUNTS, {"mode": "append",
                                                 "text": "GATEWAY"})
        out = wb_proxy.apply_prompt_mode(
            [{"role": "system", "content": "client"},
             {"role": "user", "content": "hi"}])
        self.assertEqual([m["content"] for m in out[:3]],
                         ["client", "GATEWAY", "hi"])

    def test_custom_replaces_the_client_system(self):
        wb_settings.set_prompt_config(ACCOUNTS, {"mode": "custom",
                                                 "text": "GATEWAY"})
        out = wb_proxy.apply_prompt_mode(
            [{"role": "system", "content": "client"},
             {"role": "user", "content": "hi"}])
        self.assertEqual(out[0], {"role": "system", "content": "GATEWAY"})
        self.assertEqual(out[1], {"role": "user", "content": "hi"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
