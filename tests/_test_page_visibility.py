"""The hidden-top-bar-page list persists as one list of page keys.

    python tests/_test_page_visibility.py

The settings tab's "顶部页签显示" checkboxes need one stable persisted list to
read and write, and nothing else. It lives in accounts/settings.json beside the
other panel settings, so it inherits the same lock and the same atomic replace,
and the UI needs no second persistence subsystem.

The normalisation carries most of these cases. The panel only ever sends a list
of well-formed keys, but the file is hand-editable and this module deliberately
does not know which pages exist - the pages live in dashboard.html - so the safe
reading of anything malformed is "nothing hidden". `settings` is the one key
dropped on the way in: it is where the switch itself lives, and a stored file
that hides it would leave the panel with no way back.

No network access required.
"""
import json
import os
import subprocess
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from _isolated_dirs import isolated_data_dirs  # noqa: E402

# Its own accounts/ + usage/ before wb_proxy reads ACCOUNTS_DIR at import.
_TMP = isolated_data_dirs("wb-page-visibility-")

import wb_proxy as proxy  # noqa: E402
import wb_settings as S  # noqa: E402


class FakeRequest(object):
    """Just enough of the handler for the save route under test."""

    _handle_settings_save = proxy.Handler._handle_settings_save

    def __init__(self, payload):
        self.payload = payload
        self.answering = []

    def _payload_or_error(self, allow_list=False):
        return self.payload

    def _error(self, status, message, kind=""):
        self.answering.append(("error", status, message))
        return status, message

    def _json(self, status, payload):
        self.answering.append(("json", status, payload))
        return status, payload


class HiddenPagesTests(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.join(_TMP.name, "accounts")
        os.makedirs(self.dir, exist_ok=True)
        proxy.ACCOUNTS_DIR = self.dir
        path = S.settings_path(self.dir)
        if os.path.exists(path):
            os.unlink(path)

    def _write(self, data):
        with open(S.settings_path(self.dir), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def _read(self):
        with open(S.settings_path(self.dir), encoding="utf-8") as fh:
            return json.load(fh)

    def _save(self, payload):
        """Run the save route; return the errors it answered with."""
        request = FakeRequest(payload)
        request._handle_settings_save()
        return [entry for entry in request.answering if entry[0] == "error"]

    def _view(self):
        return proxy.runtime_settings_view()["hidden_pages"]

    # --- the stored value -------------------------------------------------

    def test_missing_key_hides_nothing(self):
        self.assertFalse(os.path.exists(S.settings_path(self.dir)))
        self.assertEqual(S.hidden_pages(self.dir), [])
        self.assertEqual(self._view(), [])

    def test_a_list_round_trips_in_order(self):
        self.assertEqual(S.set_hidden_pages(self.dir, ["tasks", "logs"]),
                         ["tasks", "logs"])
        self.assertEqual(S.hidden_pages(self.dir), ["tasks", "logs"])
        self.assertEqual(self._read()["hidden_pages"], ["tasks", "logs"])
        self.assertEqual(self._view(), ["tasks", "logs"])

    def test_setting_an_empty_list_shows_everything_again(self):
        S.set_hidden_pages(self.dir, ["tasks"])
        self.assertEqual(S.set_hidden_pages(self.dir, []), [])
        self.assertEqual(S.hidden_pages(self.dir), [])
        self.assertEqual(self._read()["hidden_pages"], [])
        self.assertEqual(self._view(), [])

    def test_unknown_page_keys_are_kept_not_rejected(self):
        # This module has no page registry on purpose: a key it has never heard
        # of is the panel's business to ignore, and rejecting it here would mean
        # touching wb_settings.py every time a page is added upstream.
        S.set_hidden_pages(self.dir, ["a-page-added-later"])
        self.assertEqual(S.hidden_pages(self.dir), ["a-page-added-later"])

    def test_the_settings_page_is_never_hidden(self):
        # Both directions: it cannot be stored, so the file can never hold a
        # state that leaves the switch with no way back to itself.
        self.assertEqual(S.set_hidden_pages(self.dir, ["settings"]), [])
        self.assertEqual(S.hidden_pages(self.dir), [])
        self.assertEqual(self._read()["hidden_pages"], [])
        self.assertEqual(S.set_hidden_pages(self.dir, ["tasks", "settings"]), ["tasks"])

    def test_duplicates_collapse(self):
        S.set_hidden_pages(self.dir, ["logs", "logs", "tasks", "logs"])
        self.assertEqual(S.hidden_pages(self.dir), ["logs", "tasks"])

    def test_surrounding_whitespace_is_trimmed(self):
        S.set_hidden_pages(self.dir, ["  logs ", "\ttasks\n"])
        self.assertEqual(S.hidden_pages(self.dir), ["logs", "tasks"])

    def test_malformed_values_hide_nothing(self):
        for stored in ("tasks", "settings", 1, 0, 1.0, True, False, None, {},
                       [None], [{}], [[]], [True], ["TASKS"],
                       ["../etc"], ["has space"], [""], ["-leading"], ["a" * 40]):
            with self.subTest(stored=stored):
                self._write({"hidden_pages": stored})
                self.assertEqual(S.hidden_pages(self.dir), [])
                self.assertEqual(self._view(), [])

    def test_a_string_is_not_iterated_into_page_keys(self):
        # "tasks" iterated as a sequence would hide "t", "a", "s", "k" - five
        # keys no page answers to. It reads as nothing hidden instead.
        self._write({"hidden_pages": "tasks"})
        self.assertEqual(S.hidden_pages(self.dir), [])

    def test_a_hand_written_list_is_cleaned_on_read(self):
        self._write({"hidden_pages": ["logs", "logs", 7, "settings", "ok_page", "bad key"]})
        self.assertEqual(S.hidden_pages(self.dir), ["logs", "ok_page"])

    def test_an_over_long_list_is_bounded(self):
        many = ["page-%d" % i for i in range(S.MAX_HIDDEN_PAGES + 20)]
        S.set_hidden_pages(self.dir, many)
        self.assertEqual(len(S.hidden_pages(self.dir)), S.MAX_HIDDEN_PAGES)
        self.assertEqual(S.hidden_pages(self.dir), many[:S.MAX_HIDDEN_PAGES])

    def test_a_new_process_sees_the_persisted_list(self):
        """A restart re-reads the file, so the choice is not process-local."""
        S.set_hidden_pages(self.dir, ["tasks", "logs"])
        code = ("import sys; sys.path.insert(0, %r); import wb_settings as S; "
                "print(S.hidden_pages(%r))" % (ROOT, self.dir))
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "['tasks', 'logs']")

    # --- living beside the other settings ---------------------------------

    def test_saving_the_list_preserves_unrelated_settings(self):
        self._write({
            "user_custom_top": {"keep": 1},
            "limits": {"global": {"reserve_credits": 7}},
            "panel_password": {"salt": "ab", "digest": "cd", "rounds": 1},
            "key_before_hidden": True,
        })
        S.set_hidden_pages(self.dir, ["logs"])
        stored = self._read()
        self.assertEqual(stored["user_custom_top"], {"keep": 1})
        self.assertEqual(stored["limits"], {"global": {"reserve_credits": 7}})
        self.assertEqual(stored["panel_password"],
                         {"salt": "ab", "digest": "cd", "rounds": 1})
        self.assertIs(stored["key_before_hidden"], True)
        self.assertEqual(stored["hidden_pages"], ["logs"])

    def test_another_setting_write_keeps_the_list(self):
        S.set_hidden_pages(self.dir, ["logs"])
        S.set_accounts_collapsed(self.dir, True)
        self.assertEqual(S.hidden_pages(self.dir), ["logs"])
        self.assertIs(S.accounts_collapsed(self.dir), True)

    def test_concurrent_writes_leave_a_readable_file(self):
        self._write({"user_custom_top": {"keep": 1}})
        errors = []
        choices = [["tasks"], ["logs", "agents"], [], ["analytics"]]

        def write(pages):
            try:
                for _ in range(25):
                    S.set_hidden_pages(self.dir, pages)
            except Exception as exc:  # reported through the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(pages,)) for pages in choices * 2]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        stored = self._read()  # a truncated file would raise here
        self.assertIsInstance(stored.get("hidden_pages"), list)
        self.assertEqual(stored.get("user_custom_top"), {"keep": 1})

    # --- the save route ---------------------------------------------------

    def test_save_route_stores_a_list_of_keys(self):
        self.assertEqual(self._save({"hidden_pages": ["tasks"]}), [])
        self.assertEqual(self._view(), ["tasks"])
        self.assertEqual(self._read()["hidden_pages"], ["tasks"])
        self.assertEqual(self._save({"hidden_pages": []}), [])
        self.assertEqual(self._view(), [])

    def test_save_route_normalises_what_it_stores(self):
        self.assertEqual(self._save({"hidden_pages": ["logs", "logs", "settings", "bad key"]}), [])
        self.assertEqual(self._read()["hidden_pages"], ["logs"])

    def test_save_route_rejects_anything_that_is_not_a_list_of_strings(self):
        # A bare string is the one worth naming: accepted, it would iterate
        # character by character and store five junk keys.
        for bad in ("tasks", "logs", 1, 0, None, True, {}, {"tasks": 1},
                    ["tasks", 1], [None], [[]], [{"page": "tasks"}]):
            with self.subTest(value=bad):
                errors = self._save({"hidden_pages": bad})
                self.assertEqual(len(errors), 1, errors)
                self.assertEqual(errors[0][1], 400)
                self.assertEqual(S.hidden_pages(self.dir), [])

    def test_save_route_patch_leaves_other_settings_alone(self):
        self._write({"user_custom_top": {"keep": 1}, "key_before_hidden": True})
        self.assertEqual(self._save({"hidden_pages": ["tasks"]}), [])
        stored = self._read()
        self.assertEqual(stored["user_custom_top"], {"keep": 1})
        self.assertIs(stored["key_before_hidden"], True)

    def test_save_route_ignores_a_payload_that_omits_the_list(self):
        S.set_hidden_pages(self.dir, ["tasks"])
        self.assertEqual(self._save({"user_custom_top": {"keep": 2}}), [])
        self.assertEqual(S.hidden_pages(self.dir), ["tasks"])

    def test_the_view_carries_the_normalised_list(self):
        self._write({"hidden_pages": ["logs", "logs", "settings"]})
        self.assertEqual(self._view(), ["logs"])

    # --- where it lives ---------------------------------------------------

    def test_the_setting_lives_in_the_isolated_tree(self):
        S.set_hidden_pages(self.dir, ["tasks"])
        path = os.path.abspath(S.settings_path(self.dir))
        self.assertTrue(path.startswith(os.path.abspath(_TMP.name)), path)
        self.assertFalse(path.startswith(os.path.abspath(os.path.join(ROOT, "accounts"))),
                         path)
        self.assertEqual(os.path.dirname(path), os.path.abspath(self.dir))


if __name__ == "__main__":
    unittest.main(verbosity=2)
