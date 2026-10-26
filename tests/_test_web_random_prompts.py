"""Random prompts for the web activity, and the built-in pool they draw from.

The daily activity used to send the same "Hi" every day. With the setting on,
one line is drawn from the built-in 300-line pool plus whatever the operator
added. Off by default, so an install that predates the setting keeps sending
"Hi".
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _isolated_dirs import isolated_data_dirs  # noqa: E402

_TMP = isolated_data_dirs("wb-random-prompts-")

import wb_accounts  # noqa: E402
import wb_settings  # noqa: E402
import wb_webprompts  # noqa: E402

ACCOUNTS = os.path.join(_TMP.name, "accounts")


class BuiltinPoolTests(unittest.TestCase):
    def test_pool_has_300_unique_non_empty_lines(self):
        pool = wb_webprompts.builtin_prompts()
        self.assertEqual(len(pool), 300)
        self.assertEqual(len(set(pool)), 300)
        self.assertTrue(all(line.strip() == line and line for line in pool))

    def test_returned_pool_is_a_copy(self):
        pool = wb_webprompts.builtin_prompts()
        pool.clear()
        self.assertEqual(len(wb_webprompts.builtin_prompts()), 300)


class PromptSelectionTests(unittest.TestCase):
    def setUp(self):
        wb_settings.set_daily_chat_web_random_prompt(ACCOUNTS, False)
        wb_settings.set_daily_chat_web_prompts(ACCOUNTS, [])
        self.acc = wb_accounts.Account(
            {"uid": "u1", "realm": "intl", "accessToken": "t"},
            path=os.path.join(ACCOUNTS, "acc.json"))

    def test_off_keeps_the_original_hi(self):
        self.assertEqual(self.acc.daily_chat_web_prompt(), "Hi")

    def test_on_draws_from_the_builtin_pool(self):
        wb_settings.set_daily_chat_web_random_prompt(ACCOUNTS, True)
        seen = set()
        for _ in range(20):
            seen.add(self.acc.daily_chat_web_prompt())
        self.assertTrue(seen)
        self.assertTrue(seen <= set(wb_webprompts.BUILTIN_PROMPTS))

    def test_configured_pool_is_the_draw_scope(self):
        # The picker saves the full selection (builtin lines + custom lines);
        # the draw must use exactly that selection, not "builtin plus custom".
        wb_settings.set_daily_chat_web_random_prompt(ACCOUNTS, True)
        builtin = wb_webprompts.BUILTIN_PROMPTS[0]
        wb_settings.set_daily_chat_web_prompts(ACCOUNTS, [builtin, "自定义一号"])
        captured = []

        def fake_choice(pool):
            captured.append(list(pool))
            return pool[-1]

        with mock.patch.object(wb_accounts.random, "choice", side_effect=fake_choice):
            self.assertEqual(self.acc.daily_chat_web_prompt(), "自定义一号")
        self.assertEqual(captured[0], [builtin, "自定义一号"])

    def test_empty_pool_means_all_builtin(self):
        wb_settings.set_daily_chat_web_random_prompt(ACCOUNTS, True)
        wb_settings.set_daily_chat_web_prompts(ACCOUNTS, [])
        captured = []

        def fake_choice(pool):
            captured.append(list(pool))
            return pool[0]

        with mock.patch.object(wb_accounts.random, "choice", side_effect=fake_choice):
            self.acc.daily_chat_web_prompt()
        self.assertEqual(len(captured[0]), 300)

    def test_an_account_without_settings_stays_on_hi(self):
        acc = wb_accounts.Account({"uid": "u2", "realm": "intl",
                                   "accessToken": "t"})
        self.assertEqual(acc.daily_chat_web_prompt(), "Hi")


class CustomPromptSettingTests(unittest.TestCase):
    def test_cleaning_dedupes_and_drops_long_lines(self):
        long_line = "x" * (wb_settings.DAILY_CHAT_WEB_PROMPT_MAX_LEN + 1)
        stored = wb_settings.set_daily_chat_web_prompts(
            ACCOUNTS, ["a", " a ", "", long_line, "b"])
        self.assertEqual(stored, ["a", "b"])

    def test_newline_string_is_accepted(self):
        stored = wb_settings.set_daily_chat_web_prompts(ACCOUNTS, "a\nb\r\nc")
        self.assertEqual(stored, ["a", "b", "c"])
        self.assertEqual(wb_settings.daily_chat_web_prompts(ACCOUNTS),
                         ["a", "b", "c"])

    def test_count_is_bounded(self):
        stored = wb_settings.set_daily_chat_web_prompts(
            ACCOUNTS, ["p%d" % i for i in range(
                wb_settings.DAILY_CHAT_WEB_PROMPTS_MAX + 10)])
        self.assertEqual(len(stored), wb_settings.DAILY_CHAT_WEB_PROMPTS_MAX)

    def test_random_prompt_is_off_by_default(self):
        wb_settings.set_daily_chat_web_random_prompt(ACCOUNTS, False)
        data = wb_settings.load(ACCOUNTS)
        data.pop("daily_chat_web_random_prompt", None)
        wb_settings.save(ACCOUNTS, data)
        self.assertFalse(wb_settings.daily_chat_web_random_prompt(ACCOUNTS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
