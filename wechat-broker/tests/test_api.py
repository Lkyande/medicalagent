import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, attempt_response, callback_result
from app.wechat_service import WechatService


class BrokerAPITests(unittest.TestCase):
    def test_attempt_response_includes_failure_reason(self):
        response = attempt_response(
            SimpleNamespace(
                id="auth-1",
                status="failed",
                subject_id="user-a",
                tenant_id=None,
                authorizer_appid=None,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                error="api_query_auth failed",
            )
        )

        self.assertEqual(response.status, "failed")
        self.assertEqual(response.error, "api_query_auth failed")

    def test_failed_callback_stays_open_to_show_the_error(self):
        response = callback_result(False, "api_query_auth failed")

        self.assertNotIn("window.close", response.body.decode())

    def test_authorization_start_page_has_safe_navigation_policy(self):
        response = TestClient(app).get(
            "/api/wechat-broker/authorization/start"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["referrer-policy"], "origin")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertIn('target.origin !== "https://mp.weixin.qq.com"', response.text)
        self.assertIn('target.pathname !== "/cgi-bin/componentloginpage"', response.text)

    def test_authorization_start_resolves_only_signed_target(self):
        wechat_url = (
            "https://mp.weixin.qq.com/cgi-bin/componentloginpage"
            "?component_appid=wx-component&pre_auth_code=code"
            "&redirect_uri=https%3A%2F%2Fbroker.example%2Fcallback%3Fstate%3D"
            + "a" * 64
            + "&auth_type=1"
        )
        with patch.multiple(
            settings,
            internal_api_key="a" * 32,
            component_appid="wx-component",
            authorization_callback_url="https://broker.example/callback",
        ):
            start_url = WechatService._authorization_start_url(wechat_url)
            response = TestClient(app).post(
                "/api/wechat-broker/authorization/start/resolve",
                json={"payload": urlsplit(start_url).fragment},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"target": wechat_url})

    def test_callback_requires_wechat_parameters(self):
        response = TestClient(app).get(
            "/api/wechat-broker/authorization/callback"
        )

        self.assertEqual(response.status_code, 422)

    def test_internal_api_rejects_missing_key(self):
        with patch.object(settings, "internal_api_key", "a" * 32):
            response = TestClient(app).get(
                "/api/wechat-broker/internal/v1/bindings/user-a"
            )

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
