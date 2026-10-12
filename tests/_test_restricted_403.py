"""上游风控限制 403（code 11140）必须换号，而不是透传给客户端。

实测正文：{"code":11140,"msg":"request illegal",...,"displayMsg":{"en":"Your
account has been restricted due to unusual activity","zh":"账号存在异常行为，
已被限制使用"}}。这在重试循环里原本落进内容审核分支：降级提示词重试一次，
然后 break——被限制的账号既不冷却、也继续被选用，客户端一次次拿到 403，
对话就这样被打死（2026-10-11 生产实测）。

要钉住的行为：

  1. 风控 403 与内容审核 403 分流：前者冷却账号 2 小时并轮到下一个账号；
  2. 内容审核 403 的原路径不变（降级重试、透传，不给账号上长冷却）；
  3. 一切 >=500 的未知状态码（实测 550）也按瞬时故障轮转；
  4. 池里只有这一个账号时，仍把限制原因交给客户端（没有别的可试）。

Run with the current interpreter (python tests/run_all.py restricted).
"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error

_TMP = tempfile.mkdtemp(prefix="wb-restrict403-")
os.environ["ACCOUNTS_DIR"] = os.path.join(_TMP, "accounts")
os.environ["WB_PROXY_USAGE_DIR"] = _TMP
os.makedirs(os.environ["ACCOUNTS_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_accounts
import wb_proxy as P


RESTRICT_BODY = json.dumps({
    "code": 11140, "msg": "request illegal", "requestId": "1" * 32,
    "displayMsg": {
        "en": "Your account has been restricted due to unusual activity",
        "zh": "账号存在异常行为，已被限制使用",
    },
}).encode("utf-8")

CONTENT_BODY = json.dumps({
    "code": 10001, "msg": "content rejected",
}).encode("utf-8")


def http_error(code, body):
    return urllib.error.HTTPError("https://upstream/chat", code, "err", {},
                                  io.BytesIO(body))


class FakeResp(object):
    pass


def make_account(uid):
    return wb_accounts.Account({"uid": uid, "accessToken": "t-" + uid,
                                "realm": "intl"})


class StubPool(object):
    """pick_for_session 走真实的 ready() 语义：冷却中的账号不再被选中。"""

    def __init__(self, accounts):
        self.accounts = accounts
        self.affinity = wb_accounts.SessionAffinity()

    def count_ready(self, realm, model=None):
        return sum(1 for a in self.accounts
                   if a.realm == realm and a.ready(model=model))

    def pick_for_session(self, realm=None, session_key=None, exclude=(),
                         model=None, page=None):
        for a in self.accounts:
            if a.uid in exclude:
                continue
            if realm and a.realm != realm:
                continue
            if a.ready(model=model):
                return a
        return None

    def apply_remaining_weights(self, weights=None):
        return None

    def apply_daily_token_limit(self, value=None, usage=None):
        return value or 0

    def apply_daily_credit_limit(self, value=None, credits=None, free_models=None):
        return value or 0

    def apply_model_daily_token_limit(self, value=None, per_model=None):
        return value or 0


class ScriptedUpstream(object):
    """按尝试顺序吐预设结果：异常抛出、FakeResp 返回。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, req, timeout=None, proxy=None):
        self.calls.append(req)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


PAYLOAD = {"model": "deepseek-v4.1-flash",
           "messages": [{"role": "user", "content": "hi"}]}


class Restricted403DetectionTests(unittest.TestCase):
    def test_real_restriction_body_matches(self):
        self.assertTrue(P.restriction_is_account_level(RESTRICT_BODY.decode("utf-8")))
        # 400 字节截断（旧读法）也能命中 code 与 en 措辞。
        self.assertTrue(P.restriction_is_account_level(
            RESTRICT_BODY.decode("utf-8")[:400]))
        # code 写成字符串、带空格的变体。
        self.assertTrue(P.restriction_is_account_level('{"code": "11140"}'))

    def test_content_and_junk_bodies_do_not_match(self):
        self.assertFalse(P.restriction_is_account_level(CONTENT_BODY.decode("utf-8")))
        self.assertFalse(P.restriction_is_account_level(""))
        self.assertFalse(P.restriction_is_account_level(None))
        # hex requestId 里可能出现 "11140" 子串，裸字符串不算数。
        self.assertFalse(P.restriction_is_account_level(
            '{"msg":"x","requestId":"11140abcdef"}'))


class Restricted403DispatchTests(unittest.TestCase):
    def setUp(self):
        self.old_pool = P.POOL

    def tearDown(self):
        P.POOL = self.old_pool

    def _run(self, accounts, script):
        P.POOL = StubPool(accounts)
        upstream = ScriptedUpstream(script)
        real_urlopen = wb_accounts.urlopen
        wb_accounts.urlopen = upstream
        try:
            result = P.open_upstream(dict(PAYLOAD), target_realm="intl")
            raised = None
        except Exception as exc:
            result = None
            raised = exc
        finally:
            wb_accounts.urlopen = real_urlopen
        return result, raised

    def test_restricted_account_is_cooled_and_next_account_serves(self):
        restricted = make_account("r1")
        healthy = make_account("h1")
        resp, raised = self._run(
            [restricted, healthy],
            [http_error(403, RESTRICT_BODY), FakeResp()])
        self.assertIsNone(raised)
        self.assertEqual(resp[1].uid, "h1")
        # 被限制的账号进 2 小时冷却，不再被选用。
        self.assertGreaterEqual(restricted.cooldown_until - time.time(), 7000)
        self.assertEqual(restricted.last_error, "HTTP 403 (account restricted)")
        self.assertIn("11140", restricted.last_error_detail)
        # 健康账号没有被牵连。
        self.assertEqual(healthy.cooldown_until, 0.0)

    def test_lone_restricted_account_still_reports_the_reason(self):
        restricted = make_account("r-only")
        resp, raised = self._run(
            [restricted],
            [http_error(403, RESTRICT_BODY), http_error(403, RESTRICT_BODY)])
        self.assertIsNone(resp)
        self.assertIsInstance(raised, P.ContentRejected)
        self.assertEqual(getattr(raised, "account_uid", None), "r-only")
        self.assertIn("11140", raised.detail)

    def test_content_403_keeps_its_old_path(self):
        a = make_account("c1")
        b = make_account("c2")
        resp, raised = self._run(
            [a, b],
            [http_error(403, CONTENT_BODY), http_error(403, CONTENT_BODY)])
        # 内容审核 403 不给账号上长冷却（这不是账号的坏）。
        self.assertEqual(a.cooldown_until, 0.0)
        self.assertEqual(b.cooldown_until, 0.0)
        self.assertIsNone(resp)
        self.assertIsInstance(raised, P.ContentRejected)

    def test_unknown_5xx_rotates_instead_of_passing_through(self):
        flaky = make_account("f1")
        healthy = make_account("f2")
        resp, raised = self._run(
            [flaky, healthy],
            [http_error(550, b""), FakeResp()])
        self.assertIsNone(raised)
        self.assertEqual(resp[1].uid, "f2")
        self.assertEqual(flaky.fails, 1)


if __name__ == "__main__":
    unittest.main()
