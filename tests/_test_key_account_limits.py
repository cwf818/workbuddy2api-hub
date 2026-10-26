"""Per-key account and model restrictions.

A key may name the accounts it is allowed to use, in addition to the model
patterns it already had. An empty list keeps the key unrestricted, so installs
that never touch the field keep their exact behaviour. The account filter is
enforced in the scheduler, including the conversation-affinity path: a bound
conversation must not route around a restriction the current request carries.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_accounts
import wb_settings


def make_account(uid, realm="intl"):
    return wb_accounts.Account(
        {"uid": uid, "realm": realm, "accessToken": "t", "enabled": True})


class AccountListTests(unittest.TestCase):
    def test_comma_string_list_and_dedupe(self):
        self.assertEqual(
            wb_settings._clean_account_refs("a, b; a\nc"),
            ["a", "b", "c"])
        self.assertEqual(
            wb_settings._clean_account_refs(["a", " a ", "", None, "b"]),
            ["a", "b"])

    def test_length_and_count_are_bounded(self):
        long_ref = "x" * (wb_settings.KEY_ACCOUNT_MAX_LEN + 1)
        stored = wb_settings._clean_account_refs(
            [long_ref] + ["u%d" % i for i in range(wb_settings.KEY_ACCOUNTS_MAX + 5)])
        self.assertNotIn(long_ref, stored)
        self.assertEqual(len(stored), wb_settings.KEY_ACCOUNTS_MAX)

    def test_empty_means_unrestricted(self):
        entry = {"accounts": []}
        self.assertIsNone(wb_settings.key_allowed_accounts(entry))
        self.assertTrue(wb_settings.key_allows_account(entry, "any"))
        self.assertIsNone(wb_settings.key_allowed_accounts(None))

    def test_matching_is_exact(self):
        entry = {"accounts": ["uid-1", "uid-2"]}
        self.assertTrue(wb_settings.key_allows_account(entry, "uid-1"))
        self.assertFalse(wb_settings.key_allows_account(entry, "uid-"))
        self.assertFalse(wb_settings.key_allows_account(entry, "UID-1"))
        self.assertEqual(wb_settings.key_allowed_accounts(entry),
                         {"uid-1", "uid-2"})

    def test_legacy_entry_without_accounts_stays_unrestricted(self):
        cleaned = wb_settings._clean_key_entry(
            {"id": "k1", "name": "old", "key": "sk-1"})
        self.assertEqual(cleaned["accounts"], [])
        self.assertIsNone(wb_settings.key_allowed_accounts(cleaned))

    def test_clean_entry_keeps_the_list(self):
        cleaned = wb_settings._clean_key_entry(
            {"id": "k1", "name": "new", "key": "sk-1",
             "accounts": ["uid-1", "uid-1", "uid-2"]})
        self.assertEqual(cleaned["accounts"], ["uid-1", "uid-2"])


class SchedulerFilterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="key-accounts-")
        self.pool = wb_accounts.AccountPool(self.directory, log=lambda _m: None)
        self.pool.accounts = [make_account("uid-a"), make_account("uid-b"),
                              make_account("uid-c")]

    def test_pick_only_returns_allowed_accounts(self):
        for _ in range(6):
            account = self.pool.pick(realm="intl", allowed={"uid-b"})
            self.assertIsNotNone(account)
            self.assertEqual(account.uid, "uid-b")

    def test_pick_without_restriction_still_rotates(self):
        seen = set()
        for _ in range(9):
            account = self.pool.pick(realm="intl")
            self.assertIsNotNone(account)
            seen.add(account.uid)
        self.assertEqual(seen, {"uid-a", "uid-b", "uid-c"})

    def test_pick_returns_none_when_nobody_is_allowed(self):
        self.assertIsNone(self.pool.pick(realm="intl", allowed={"nobody"}))

    def test_count_ready_honours_the_filter(self):
        self.assertEqual(self.pool.count_ready("intl"), 3)
        self.assertEqual(self.pool.count_ready("intl", allowed={"uid-a", "uid-b"}), 2)
        self.assertEqual(self.pool.count_ready("intl", allowed={"nobody"}), 0)

    def test_affinity_does_not_route_around_the_filter(self):
        # A conversation bound to uid-a must not keep serving uid-a when the
        # current request's key only allows uid-b.
        self.pool.affinity.bind("conv-1", "uid-a")
        account = self.pool.pick_for_session(
            realm="intl", session_key="conv-1", allowed={"uid-b"})
        self.assertIsNotNone(account)
        self.assertEqual(account.uid, "uid-b")
        self.assertEqual(self.pool.affinity.recent("conv-1")[0], "uid-b")


if __name__ == "__main__":
    unittest.main(verbosity=2)
