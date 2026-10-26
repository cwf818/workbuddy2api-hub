"""A successful daily activity is not repeated on the same day.

Several schedule slots, a manual retry and a concurrent sweep can all reach the
same account. Once the day's activity has succeeded (``lastDailyChat`` is
today), every later entry returns a skipped result instead of opening another
web conversation or sending another desktop turn.
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _isolated_dirs import isolated_data_dirs  # noqa: E402

_TMP = isolated_data_dirs("wb-daily-once-")

import wb_accounts  # noqa: E402


def today():
    return time.strftime("%Y-%m-%d 09:00:00")


def yesterday():
    return time.strftime("%Y-%m-%d 09:00:00",
                         time.localtime(time.time() - 86400))


def make_account(last=None, realm="intl"):
    return wb_accounts.Account({
        "uid": "u1", "realm": realm, "accessToken": "t",
        "lastDailyChat": last,
    })


class DailyChatOnceTests(unittest.TestCase):
    def test_today_done_skips_the_whole_activity(self):
        acc = make_account(today())
        with mock.patch.object(acc, "_daily_chat_upstream") as inner:
            res = acc.daily_chat(trigger="scheduler")
        self.assertTrue(res["ok"])
        self.assertTrue(res["skipped"])
        inner.assert_not_called()

    def test_yesterday_does_not_skip(self):
        acc = make_account(yesterday())
        with mock.patch.object(acc, "_daily_chat_upstream",
                               return_value={"ok": True}) as inner:
            res = acc.daily_chat(trigger="scheduler")
        self.assertFalse(res.get("skipped"))
        inner.assert_called_once()

    def test_today_done_skips_the_web_leg(self):
        acc = make_account(today())
        with mock.patch.object(acc, "_daily_chat_web_upstream") as inner:
            res = acc.daily_chat_web(trigger="manual")
        self.assertTrue(res["ok"])
        self.assertTrue(res["skipped"])
        inner.assert_not_called()

    def test_web_leg_success_blocks_the_next_call(self):
        acc = make_account(None)
        acc.last_daily_chat = today()
        with mock.patch.object(acc, "_daily_chat_web_upstream") as inner:
            res = acc.daily_chat_web(trigger="manual")
        self.assertTrue(res["skipped"])
        inner.assert_not_called()

    def test_non_intl_accounts_keep_their_error(self):
        acc = make_account(None, realm="cn")
        res = acc.daily_chat()
        self.assertFalse(res["ok"])
        self.assertIn("international", res["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
