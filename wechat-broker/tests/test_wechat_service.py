import base64
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.fernet import Fernet

from app.config import settings
from app.wechat_service import WechatAuthorizationError, WechatService


class WechatServiceTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.wechat_service.TicketStore")
    async def test_component_token_uses_php_ticket_database(self, ticket_store_type):
        ticket_store_type.return_value.latest_ticket = AsyncMock(return_value="external-ticket")
        query = MagicMock()
        query.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute.return_value = query
        db.add = MagicMock()

        with patch.multiple(
            settings,
            component_appid="wx-component",
            component_appsecret="component-secret",
            authorization_callback_url="https://broker.example/callback",
            token_encryption_key=Fernet.generate_key().decode("ascii"),
            ticket_database_url="mysql+aiomysql://reader:secret@mysql.example/test",
        ):
            service = WechatService()
            service._post_wechat = AsyncMock(
                return_value={"component_access_token": "component-token", "expires_in": 7200}
            )
            token = await service.component_access_token(db)

        self.assertEqual(token, "component-token")
        payload = service._post_wechat.await_args.args[1]
        self.assertEqual(payload["component_verify_ticket"], "external-ticket")
        db.add.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_creates_state_bound_authorization_url(self):
        db = AsyncMock()
        db.add = MagicMock()
        with patch.multiple(
            settings,
            internal_api_key="a" * 32,
            component_appid="wx-component",
            component_appsecret="component-secret",
            authorization_callback_url="https://broker.example/callback",
            token_encryption_key=Fernet.generate_key().decode("ascii"),
            ticket_database_url="mysql+aiomysql://reader:secret@mysql.example/test",
            allowed_return_origins="https://app.example",
        ):
            service = WechatService()
            service.component_access_token = AsyncMock(return_value="component-token")
            service._post_wechat = AsyncMock(
                return_value={"pre_auth_code": "preauth-code", "expires_in": 600}
            )
            attempt, auth_url = await service.create_authorization(
                "user-a", "tenant-a", "https://app.example/result", db
            )

        start_url = urlsplit(auth_url)
        encoded, _signature = start_url.fragment.split(".", 1)
        padding = "=" * (-len(encoded) % 4)
        wechat_url = base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
        query = parse_qs(urlsplit(wechat_url).query)
        redirect_query = parse_qs(urlsplit(query["redirect_uri"][0]).query)
        self.assertEqual(start_url.scheme, "https")
        self.assertEqual(start_url.netloc, "broker.example")
        self.assertEqual(start_url.path, "/api/wechat-broker/authorization/start")
        self.assertEqual(start_url.query, "")
        self.assertEqual(urlsplit(wechat_url).netloc, "mp.weixin.qq.com")
        self.assertEqual(query["pre_auth_code"], ["preauth-code"])
        self.assertEqual(redirect_query["state"], [attempt.state])
        self.assertEqual(attempt.subject_id, "user-a")
        db.add.assert_called_once_with(attempt)
        db.commit.assert_awaited_once()

    def test_rejects_tampered_authorization_start_payload(self):
        with patch.multiple(
            settings,
            internal_api_key="a" * 32,
            component_appid="wx-component",
            authorization_callback_url="https://broker.example/callback",
        ):
            start_url = WechatService._authorization_start_url(
                "https://mp.weixin.qq.com/cgi-bin/componentloginpage"
                "?component_appid=wx-component&pre_auth_code=code"
                "&redirect_uri=https%3A%2F%2Fbroker.example%2Fcallback%3Fstate%3D"
                + "a" * 64
                + "&auth_type=1"
            )
            payload = urlsplit(start_url).fragment
            with self.assertRaises(WechatAuthorizationError):
                WechatService.resolve_authorization_target(payload + "tampered")

    def test_rejects_signed_target_with_foreign_callback(self):
        with patch.multiple(
            settings,
            internal_api_key="a" * 32,
            component_appid="wx-component",
            authorization_callback_url="https://broker.example/callback",
        ):
            start_url = WechatService._authorization_start_url(
                "https://mp.weixin.qq.com/cgi-bin/componentloginpage"
                "?component_appid=wx-component&pre_auth_code=code"
                "&redirect_uri=https%3A%2F%2Fevil.example%2Fcallback%3Fstate%3D"
                + "a" * 64
                + "&auth_type=1"
            )
            with self.assertRaises(WechatAuthorizationError):
                WechatService.resolve_authorization_target(urlsplit(start_url).fragment)

    def test_rejects_unapproved_return_origin(self):
        with patch.object(settings, "allowed_return_origins", "https://app.example"):
            with self.assertRaises(WechatAuthorizationError):
                WechatService._validate_return_url("https://evil.example/callback")


if __name__ == "__main__":
    unittest.main()
