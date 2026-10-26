"""Per-account task participation (issue #521).

`enabled` controls API dispatch; `tasksEnabled` controls check-in, growth
tasks, streak bonus, cat travel and the intl daily activity. Accounts written
before the field existed follow `enabled`, and disabling an account stops its
tasks unless the caller names the task flag explicitly.

Run with: python tests/_test_task_participation.py
"""
import base64
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

_startup_dir = tempfile.TemporaryDirectory(prefix="task-participation-")
os.environ["ACCOUNTS_DIR"] = _startup_dir.name
os.environ["WB_PROXY_USAGE_DIR"] = _startup_dir.name
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_accounts
import wb_proxy


def jwt(uid):
    def seg(obj):
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return (seg({"alg": "none", "typ": "JWT"}) + "."
            + seg({"uid": uid, "exp": int(time.time()) + 86400}) + ".x")


def account(uid="uid-task", **extra):
    data = {
        "uid": uid,
        "accessToken": jwt(uid),
        "refreshToken": "",
        "expiresAt": int(time.time()) + 86400,
        "realm": "cn",
        "enabled": True,
    }
    data.update(extra)
    return wb_accounts.Account(data)


class AccountFlagTests(unittest.TestCase):
    def test_missing_field_follows_enabled(self):
        self.assertTrue(account().tasks_enabled)
        self.assertFalse(account(enabled=False).tasks_enabled)
        self.assertFalse(account(enabled=False).task_active())

    def test_explicit_override_survives_disabled(self):
        acc = account(enabled=False, tasksEnabled=True)
        self.assertFalse(acc.enabled)
        self.assertTrue(acc.task_active())
        self.assertFalse(acc.ready())

    def test_public_and_export_carry_the_flag(self):
        acc = account(tasksEnabled=False)
        self.assertFalse(acc.public()["tasksEnabled"])
        self.assertFalse(acc.to_dict()["tasksEnabled"])

    def test_import_row_carries_the_flag(self):
        row = {"uid": "uid-import", "accessToken": jwt("uid-import"),
               "realm": "cn", "tasksEnabled": False}
        kwargs = wb_accounts.normalise_import_row(row)
        self.assertFalse(kwargs["tasksEnabled"])
        default = wb_accounts.normalise_import_row(
            {"uid": "uid-default", "accessToken": jwt("uid-default"),
             "realm": "cn"})
        self.assertTrue(default["tasksEnabled"])


class PoolToggleTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        for name, data in (
                ("uid-a", {"uid": "uid-a", "accessToken": jwt("uid-a"),
                           "realm": "cn", "enabled": False,
                           "tasksEnabled": True}),
                ("uid-b", {"uid": "uid-b", "accessToken": jwt("uid-b"),
                           "realm": "cn", "enabled": True,
                           "tasksEnabled": False}),
                ("uid-c", {"uid": "uid-c", "accessToken": jwt("uid-c"),
                           "realm": "cn", "enabled": True})):
            with open(os.path.join(self.dir.name, name + ".json"), "w",
                      encoding="utf-8") as fh:
                json.dump(data, fh)
        self.pool = wb_accounts.AccountPool(self.dir.name)
        self.pool.load()

    def tearDown(self):
        self.dir.cleanup()

    def test_task_accounts_filters_by_realm_and_flag(self):
        self.assertEqual(sorted(a.uid for a in self.pool.task_accounts("cn")),
                         ["uid-a", "uid-c"])

    def test_disable_stops_tasks_and_can_be_overridden(self):
        self.pool.set_enabled("uid-c", False)
        self.assertFalse(self.pool.get("uid-c").tasks_enabled)
        self.pool.set_tasks_enabled("uid-c", True)
        self.assertFalse(self.pool.get("uid-c").enabled)
        self.assertTrue(self.pool.get("uid-c").task_active())
        self.pool.set_enabled("uid-c", True)
        self.assertTrue(self.pool.get("uid-c").tasks_enabled)

    def test_explicit_both_flags(self):
        self.pool.set_enabled("uid-c", False, tasks_enabled=True)
        self.assertFalse(self.pool.get("uid-c").enabled)
        self.assertTrue(self.pool.get("uid-c").task_active())


class RouteTests(unittest.TestCase):
    class Handler(wb_proxy.Handler):
        def __init__(self):
            self.captured = None

        def _json_panel(self, code, obj):
            self.captured = (code, obj)

        def _error(self, code, message, err_type="server_error"):
            self.captured = (code, {"error": message})

    def _call(self, payload):
        handler = self.Handler()
        handler._route_accounts_set(payload)
        return handler.captured

    def _pool(self, directory):
        with open(os.path.join(directory, "uid-route.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"uid": "uid-route",
                       "accessToken": jwt("uid-route"),
                       "realm": "cn", "enabled": True}, fh)
        pool = wb_accounts.AccountPool(directory)
        pool.load()
        return pool

    def test_tasks_enabled_can_be_toggled_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            pool = self._pool(directory)
            with mock.patch.object(wb_proxy, "POOL", pool):
                code, body = self._call({"uid": "uid-route",
                                         "tasksEnabled": False})
            self.assertEqual(code, 200)
            self.assertTrue(body["account"]["enabled"])
            self.assertFalse(body["account"]["tasksEnabled"])

    def test_disable_follows_and_explicit_task_flag_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            pool = self._pool(directory)
            with mock.patch.object(wb_proxy, "POOL", pool):
                code, body = self._call({"uid": "uid-route",
                                         "enabled": False})
                self.assertEqual(code, 200)
                self.assertFalse(body["account"]["enabled"])
                self.assertFalse(body["account"]["tasksEnabled"])
                code, body = self._call({"uid": "uid-route",
                                         "tasksEnabled": True})
                self.assertEqual(code, 200)
                self.assertFalse(body["account"]["enabled"])
                self.assertTrue(body["account"]["tasksEnabled"])


class CheckinRouteTests(unittest.TestCase):
    """「一键签到」不传 uid 时也要按任务开关选号 (#521)。

    成长任务、猫猫旅行、连登管家、网页活跃都按 `task_active()` 选号了，
    只有签到路由漏了：`tasksEnabled=False` 的账号被顺手打了一遍上游。
    """

    class Handler(wb_proxy.Handler):
        def __init__(self):
            self.captured = None

        def _json_panel(self, code, obj):
            self.captured = (code, obj)

    def test_a_task_disabled_account_is_not_checked_in(self):
        with tempfile.TemporaryDirectory() as directory:
            for uid, tasks in (("uid-on", True), ("uid-off", False)):
                with open(os.path.join(directory, uid + ".json"), "w",
                          encoding="utf-8") as fh:
                    json.dump({"uid": uid, "accessToken": jwt(uid), "realm": "cn",
                               "enabled": True, "tasksEnabled": tasks}, fh)
            pool = wb_accounts.AccountPool(directory)
            pool.load()
            called = []

            def fake_checkin(account, trigger="unknown"):
                called.append(account.uid)
                return {"ok": True, "code": 0, "msg": "ok"}

            handler = self.Handler()
            with mock.patch.object(wb_proxy, "POOL", pool), \
                    mock.patch.object(wb_accounts.Account, "checkin", fake_checkin):
                handler._route_accounts_checkin({})
            self.assertEqual(called, ["uid-on"])

    def test_an_explicit_uid_is_still_honoured(self):
        # 显式点名单个账号与其它手动入口一致：任务开关不挡手动执行。
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "uid-off.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"uid": "uid-off", "accessToken": jwt("uid-off"),
                           "realm": "cn", "enabled": True, "tasksEnabled": False}, fh)
            pool = wb_accounts.AccountPool(directory)
            pool.load()
            called = []

            def fake_checkin(account, trigger="unknown"):
                called.append(account.uid)
                return {"ok": True, "code": 0, "msg": "ok"}

            handler = self.Handler()
            with mock.patch.object(wb_proxy, "POOL", pool), \
                    mock.patch.object(wb_accounts.Account, "checkin", fake_checkin):
                handler._route_accounts_checkin({"uid": "uid-off"})
            self.assertEqual(called, ["uid-off"])


if __name__ == "__main__":
    unittest.main()

