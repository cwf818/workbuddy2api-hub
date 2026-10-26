"""给 key 勾选的账号绑定必须真的落盘。

# 缺陷

面板保存 API Key 走 `/settings/save` → `_handle_settings_save`。那里把每条
key **重建成一个新 dict** 再交给 `wb_settings.set_api_keys()`：

    cleaned.append({
        "id": ..., "name": ..., "key": ..., "realm": ..., "models": ...,
        "enabled": ..., "created_at": ...,
    })

`accounts`（账号白名单）和 `concurrency_limit`（并发上限）没有出现在这个
dict 里。`set_api_keys` 用 `_clean_key_entry()` 归一，读不到就回落成
`accounts=[]`、`concurrency_limit=0`。

于是：面板里给 key 勾选账号、点保存，界面看着成功，`settings.json` 里
accounts 永远是 `[]`，请求照样被派到任意账号。用户侧看到的就是
「选了账号保存之后没有用」。

# 与已有缺陷是同一类

`_test_api_key_save_merge.py` 已经为 `realm`/`enabled`/`name`/`models`
立过这条规则：「补丁里没出现就沿用已存值」，并在注释里点了 issue #92
（models 被吞）。`accounts`/`concurrency_limit` 只是当时没被一起覆盖。

所以本文件的第二组用例守的是**同一条不变量**：修复不能让「省略即保留」
退化 —— 一个不认识 accounts 的旧面板提交时，不能把已经存好的绑定清空。

# 运行

    python tests/_test_key_account_binding_save.py

不需要网络，也不碰真实账号目录（每个用例一个临时目录）。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_proxy as proxy
import wb_settings as S


class FakeRequest(object):
    """够用的 handler 桩：只借 _handle_settings_save 那一段逻辑。"""

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


class KeyAccountBindingSaveTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="wb-keybinding-")
        proxy.ACCOUNTS_DIR = self.dir
        proxy.API_KEY = None
        proxy.API_KEY_FILE_SET = False

    def _save(self, payload):
        request = FakeRequest(payload)
        request._handle_settings_save()
        for kind, status, _ in request.answering:
            if kind == "error":
                self.fail("save rejected with %s" % status)
        return proxy.runtime_settings_view()

    def _live(self):
        return {e["id"]: e for e in proxy.runtime_settings_view()["api_keys"]}

    # --- 缺陷本身 ---------------------------------------------------------

    def test_saving_accounts_persists_them(self):
        """勾选账号后保存，accounts 必须留下来（这是被修的缺陷）。"""
        self._save({"api_keys": [
            {"id": "", "name": "只走甲", "realm": "", "models": [],
             "enabled": True, "key": "key-aaaa1111",
             "accounts": ["uid-a"]},
        ], "deleted_api_key_ids": []})

        stored = S.api_keys(self.dir)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["accounts"], ["uid-a"])
        # 路由侧读的是 key_allowed_accounts()，顺带钉住它真能看到限制。
        self.assertEqual(S.key_allowed_accounts(stored[0]), {"uid-a"})

    def test_saving_multiple_accounts_and_dedupe(self):
        self._save({"api_keys": [
            {"id": "", "name": "甲乙", "realm": "", "models": [],
             "enabled": True, "key": "key-bbbb2222",
             "accounts": ["uid-a", "uid-a", "uid-b"]},
        ], "deleted_api_key_ids": []})

        stored = S.api_keys(self.dir)[0]
        self.assertEqual(stored["accounts"], ["uid-a", "uid-b"])

    def test_saving_concurrency_limit_persists_it(self):
        """并发上限此前和 accounts 一起被吞。"""
        self._save({"api_keys": [
            {"id": "", "name": "限并发", "realm": "", "models": [],
             "enabled": True, "key": "key-cccc3333",
             "concurrency_limit": 3},
        ], "deleted_api_key_ids": []})

        self.assertEqual(S.api_keys(self.dir)[0]["concurrency_limit"], 3)

    def test_binding_survives_a_later_unrelated_edit(self):
        """改个名字不该把绑定弄丢。"""
        self._save({"api_keys": [
            {"id": "", "name": "原名", "realm": "", "models": [],
             "enabled": True, "key": "key-dddd4444",
             "accounts": ["uid-a"], "concurrency_limit": 2},
        ], "deleted_api_key_ids": []})
        entry_id = S.api_keys(self.dir)[0]["id"]

        self._save({"api_keys": [
            {"id": entry_id, "name": "改名了", "realm": "", "models": [],
             "enabled": True, "key": "key-dddd4444",
             "accounts": ["uid-a"], "concurrency_limit": 2},
        ], "deleted_api_key_ids": []})

        stored = S.api_keys(self.dir)[0]
        self.assertEqual(stored["name"], "改名了")
        self.assertEqual(stored["accounts"], ["uid-a"])
        self.assertEqual(stored["concurrency_limit"], 2)

    # --- 不变量：省略即保留 ----------------------------------------------

    def test_omitted_accounts_keeps_the_stored_binding(self):
        """不认识 accounts 的旧面板提交时，不能把已有绑定清空。

        这条和 realm/models/enabled 的规则完全一致：缺字段 = 沿用已存值，
        而不是 = 清空。否则修好保存路径的代价是换一个「静默清空」回来。
        """
        S.set_api_keys(self.dir, [
            {"id": "k1", "name": "只走甲", "key": "key-aaaa1111",
             "accounts": ["uid-a"], "concurrency_limit": 4},
        ])
        # 旧客户端：只知道 id/name/key，连 accounts 这个键都没有。
        self._save({"api_keys": [
            {"id": "k1", "name": "只走甲", "key": ""},
        ], "deleted_api_key_ids": []})

        stored = S.api_keys(self.dir)[0]
        self.assertEqual(stored["accounts"], ["uid-a"])
        self.assertEqual(stored["concurrency_limit"], 4)

    def test_explicit_empty_accounts_clears_the_binding(self):
        """显式传空数组 = 解除限制，这是用户的明确意图，不能被「保留」吃掉。"""
        S.set_api_keys(self.dir, [
            {"id": "k1", "name": "只走甲", "key": "key-aaaa1111",
             "accounts": ["uid-a"]},
        ])
        self._save({"api_keys": [
            {"id": "k1", "name": "只走甲", "key": "", "accounts": []},
        ], "deleted_api_key_ids": []})

        stored = S.api_keys(self.dir)[0]
        self.assertEqual(stored["accounts"], [])
        self.assertIsNone(S.key_allowed_accounts(stored))


if __name__ == "__main__":
    unittest.main(verbosity=2)
