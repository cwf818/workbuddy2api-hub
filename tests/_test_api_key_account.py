"""静态 API Key 账号：CodeBuddy 平台签发的 ck_/pt_ 凭据接入账号池。

这类凭据与 OAuth token 是两条鉴权路径，行为差异都要钉住：

  1. 识别：`ck_` / `pt_` 前缀才是静态 Key；JWT 与其它串不是；
  2. 落盘形状：不是 JWT 也能导入，uid 从 key 的 <id> 段派生，realm 恒为 cn，
     expiresAt 恒为 0；
  3. 不参与 OAuth 生命周期：refresh 直接 False 且不把账号标成过期，
     token 为空之外永不 `token_expired`；
  4. 无个人成长体系：签到 / 活跃上报一律拒绝，`uses_enterprise_billing()` 为真；
  5. 出站头：Authorization 与 X-API-Key 两个都发（只发前者时上游只下发 54 条
     而不是 56 条模型，且 HTTP 仍是 200、没有任何提示）；
  6. 脱敏：`credential_mask()` 返回 `ck_<id>.***`，OAuth 账号返回空串。

Run with the current interpreter (python tests/run_all.py api_key_account).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wb_accounts

#: 合成样例，不是真实凭证。前缀与段数对齐 ck_<id>.<secret>。
KEY = "ck_sampleid123.samplesecret456"


def make_api_key_account(uid="apikey-sampleid123", **extra):
    data = {"uid": uid, "accessToken": KEY, "realm": "cn",
            "source": wb_accounts.SOURCE_API_KEY}
    data.update(extra)
    return wb_accounts.Account(data)


class RecognitionTests(unittest.TestCase):
    def test_ck_and_pt_are_api_keys(self):
        self.assertTrue(wb_accounts.is_api_key_token("ck_a.b"))
        self.assertTrue(wb_accounts.is_api_key_token("pt_a.b"))

    def test_jwt_and_junk_are_not(self):
        # 三段式 JWT（真实 OAuth token 的形态）不能被当成静态 Key。
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSJ9.signature"
        for token in (jwt, "", "sk_live", "ck", "CK_upper.case", None, 123):
            self.assertFalse(wb_accounts.is_api_key_token(token), token)

    def test_uid_derived_from_key_id_segment(self):
        self.assertEqual(wb_accounts.api_key_id(KEY), "sampleid123")
        self.assertEqual(wb_accounts.api_key_id("pt_xyz.abc"), "xyz")
        # 点之前的部分会拼进文件名，所以非法字符必须被剔掉。`ck_a/../b.c`
        # 在第一个点处截断，只剩 `a/`，斜杠被剔掉 → `a`；路径穿越的片段
        # 压根到不了文件名。
        self.assertEqual(wb_accounts.api_key_id("ck_a/../b.c"), "a")
        self.assertEqual(wb_accounts.api_key_id("nope"), "nope")


class CredentialShapeTests(unittest.TestCase):
    def test_realm_forced_to_cn(self):
        # 落盘写着 intl 也不能照单全收：国际版出口不认这套签发。
        account = wb_accounts.Account({"uid": "k1", "accessToken": KEY,
                                       "realm": "intl"})
        self.assertEqual(account.realm, "cn")

    def test_no_expiry_no_refresh_token(self):
        account = make_api_key_account()
        self.assertEqual(account.expires_at, 0)
        self.assertEqual(account.refresh_token, "")
        self.assertTrue(account.is_api_key)

    def test_no_expiry_means_never_time_evicted(self):
        account = make_api_key_account()
        self.assertTrue(account.ready())

    def test_source_defaults_to_api_key(self):
        account = wb_accounts.Account({"uid": "k2", "accessToken": KEY})
        self.assertEqual(account.source, wb_accounts.SOURCE_API_KEY)

    def test_oauth_account_is_unaffected(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSIsImV4cCI6OTk5OTk5OTk5OX0.sig"
        account = wb_accounts.Account({"uid": "o1", "accessToken": jwt})
        self.assertFalse(account.is_api_key)
        self.assertEqual(account.source, "oauth")
        self.assertGreater(account.expires_at, 0)


class LifecycleTests(unittest.TestCase):
    def test_refresh_is_a_no_op_and_keeps_the_account_usable(self):
        account = make_api_key_account()
        # 静态 Key 无法 refresh；返回 False 是对的，但把账号标成过期会把一个
        # 健康的号踢出池子并要求重新登录。
        self.assertFalse(account.refresh())
        self.assertFalse(account.token_expired)
        self.assertTrue(account.ready())

    def test_no_personal_growth_programmes(self):
        account = make_api_key_account()
        self.assertFalse(account.can_checkin())
        self.assertFalse(account.can_report_activity())
        self.assertIn("API key", account.checkin().get("error", ""))
        self.assertIn("API key", account.report_activity().get("error", ""))

    def test_billing_uses_the_enterprise_endpoint(self):
        account = make_api_key_account()
        self.assertTrue(account.uses_enterprise_billing())

    def test_tier_is_not_concluded_from_personal_summary(self):
        # 个人计费摘要端点对静态 Key 直接 401；把「没答上」写成「免费」会让
        # PRO 模型被无谓地挡掉。
        account = make_api_key_account()
        self.assertIsNone(account._fetch_tier_summary())


class OutboundHeaderTests(unittest.TestCase):
    def test_both_auth_headers_are_sent(self):
        account = make_api_key_account()
        headers = account.headers(purpose="chat")
        self.assertEqual(headers.get("Authorization"), "Bearer " + KEY)
        # 只发 Authorization 时 /v3/config 下发 54 条而不是 56 条，且不报错。
        self.assertEqual(headers.get("X-API-Key"), KEY)

    def test_oauth_account_does_not_send_x_api_key(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSJ9.sig"
        account = wb_accounts.Account({"uid": "o2", "accessToken": jwt})
        headers = account.headers(purpose="chat")
        self.assertNotIn("X-API-Key", headers)

    def test_billing_headers_also_send_x_api_key(self):
        account = make_api_key_account()
        headers = account.headers(purpose="billing")
        self.assertEqual(headers.get("X-API-Key"), KEY)


class MaskTests(unittest.TestCase):
    def test_api_key_masks_to_prefix_and_id(self):
        account = make_api_key_account()
        self.assertEqual(account.credential_mask(), "ck_sampleid123.***")

    def test_mask_never_carries_the_secret(self):
        account = make_api_key_account()
        masked = account.credential_mask()
        self.assertNotIn("samplesecret456", masked)
        self.assertNotIn("samplesecret456", repr(account.public()))

    def test_oauth_account_exposes_no_mask(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSJ9.sig"
        account = wb_accounts.Account({"uid": "o3", "accessToken": jwt})
        self.assertEqual(account.credential_mask(), "")


class ImportTests(unittest.TestCase):
    def test_import_accepts_a_bare_api_key_without_uid(self):
        kwargs = wb_accounts.normalise_import_row({"accessToken": KEY})
        self.assertEqual(kwargs["uid"], "apikey-sampleid123")
        self.assertEqual(kwargs["realm"], "cn")
        self.assertEqual(kwargs["expiresAt"], 0)
        self.assertEqual(kwargs["source"], wb_accounts.SOURCE_API_KEY)

    def test_import_ignores_a_conflicting_realm(self):
        kwargs = wb_accounts.normalise_import_row(
            {"accessToken": KEY, "realm": "intl"})
        self.assertEqual(kwargs["realm"], "cn")

    def test_import_still_rejects_a_broken_jwt(self):
        # 非静态 Key 且不是三段式的串，仍然拒绝：那是「这串东西确实是 OAuth
        # token」唯一可靠的本地判据。
        with self.assertRaises(ValueError):
            wb_accounts.normalise_import_row({"accessToken": "not-a-jwt"})

    def test_import_keeps_an_explicit_uid(self):
        kwargs = wb_accounts.normalise_import_row(
            {"accessToken": KEY, "uid": "custom-id"})
        self.assertEqual(kwargs["uid"], "custom-id")

    def test_export_round_trip_keeps_the_type(self):
        account = make_api_key_account()
        exported = wb_accounts.account_to_export(account)
        self.assertTrue(exported["apiKey"])
        kwargs = wb_accounts.normalise_import_row(exported)
        self.assertEqual(kwargs["uid"], account.uid)
        self.assertEqual(kwargs["realm"], "cn")


if __name__ == "__main__":
    unittest.main(verbosity=2)
