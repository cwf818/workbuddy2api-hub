"""Keepalive lead time must exceed the sweep interval, else renewals arrive late.

The 22:00 sweep runs once a day. With the old fixed 7200-second lead a token was
only renewed early when it expired between 22:00 and midnight; every other
expiry time was noticed one sweep *after* it had already lapsed, leaving the
account between 0 and 24 hours in an expired state.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_scheduler


DAY = 86400
# A day boundary at a non-zero epoch: 0 means "expiry unknown" to the predicate,
# so a sample at 00:00 must not be built out of zeros.
DAY_START = 1699920000
SWEEP_OFFSET = 22 * 3600         # keepalive_hours defaults to [22]
# One expiry time per hour of the day, 00:00 .. 23:00.
ALL_HOURS = [DAY_START + h * 3600 for h in range(24)]


def renewed_before_expiry(expiry, lead):
    """True when the last sweep before `expiry` renews it while still valid."""
    sweep = (expiry // DAY) * DAY + SWEEP_OFFSET
    if sweep >= expiry:
        sweep -= DAY
    if expiry - sweep <= 0:
        return False
    return wb_scheduler.keepalive_due(expiry, now=sweep, lead=lead)


class KeepaliveWindowTests(unittest.TestCase):
    def test_old_two_hour_lead_misses_most_expiry_times(self):
        """Regression anchor: 7200s only covers expiries right after the sweep."""
        early = [h % DAY for h in ALL_HOURS if renewed_before_expiry(h, 7200)]
        # Only an expiry at 23:00 is left with less than two hours at 22:00.
        # An expiry at 21:00 still had 23 hours at the previous sweep, so it is
        # discovered one sweep later -- already lapsed.
        self.assertEqual(early, [23 * 3600])
        self.assertEqual(len(early), 1)          # 1 of 24 expiry hours

    def test_default_lead_renews_every_expiry_before_it_lapses(self):
        """48h > the 24h sweep interval, so every expiry is caught early."""
        lead = wb_scheduler.keepalive_lead_seconds()
        missed = [h for h in ALL_HOURS if not renewed_before_expiry(h, lead)]
        self.assertEqual(missed, [])

    def test_lead_just_above_the_sweep_interval_is_enough(self):
        """24h + 1s is the mathematical minimum; 48h leaves a retry window."""
        lead = DAY + 1
        missed = [h for h in ALL_HOURS if not renewed_before_expiry(h, lead)]
        self.assertEqual(missed, [])

    def test_lead_shorter_than_the_interval_can_miss(self):
        """Guard the property itself: 23h is below the interval and does miss."""
        missed = [h for h in ALL_HOURS if not renewed_before_expiry(h, 23 * 3600)]
        self.assertTrue(missed, "a lead below the sweep interval must be able to miss")

    def test_unknown_expiry_is_renewed(self):
        """`if exp and ...` skipped these forever; opaqueness is not an excuse."""
        for value in (0, 0.0, None, "", "not-a-number", -5):
            self.assertTrue(
                wb_scheduler.keepalive_due(value, now=1700000000, lead=7200),
                "expiry %r must be treated as due" % (value,),
            )

    def test_fresh_token_is_left_alone(self):
        now = 1700000000
        self.assertFalse(wb_scheduler.keepalive_due(now + 6 * DAY, now=now, lead=48 * 3600))
        self.assertTrue(wb_scheduler.keepalive_due(now + 3600, now=now, lead=48 * 3600))

    def test_lead_defaults_to_48_hours(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WB_KEEPALIVE_LEAD_HOURS", None)
            self.assertEqual(wb_scheduler.keepalive_lead_seconds(), 48 * 3600)

    def test_lead_reads_the_environment_variable(self):
        with mock.patch.dict(os.environ, {"WB_KEEPALIVE_LEAD_HOURS": "6"}):
            self.assertEqual(wb_scheduler.keepalive_lead_seconds(), 6 * 3600)
        with mock.patch.dict(os.environ, {"WB_KEEPALIVE_LEAD_HOURS": " 2.5 "}):
            self.assertEqual(wb_scheduler.keepalive_lead_seconds(), 9000)

    def test_invalid_lead_falls_back_to_the_default(self):
        for raw in ("", "   ", "abc", "0", "-3", "10000", "nan"):
            with mock.patch.dict(os.environ, {"WB_KEEPALIVE_LEAD_HOURS": raw}):
                self.assertEqual(
                    wb_scheduler.keepalive_lead_seconds(), 48 * 3600,
                    "WB_KEEPALIVE_LEAD_HOURS=%r must fall back" % (raw,),
                )


if __name__ == "__main__":
    unittest.main()
