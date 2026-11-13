import unittest
import httpx
from fastapi import HTTPException
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.responses import StreamingResponse

from app.auth.dependencies import get_current_user
from app.models import User
from app.proxy.opencode_proxy import proxy_to_sandbox
from app.sandbox.manager import get_or_create_workspace


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class ConnectionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.auth.dependencies.verify_token", return_value="user-1")
    async def test_authentication_releases_its_read_transaction(self, _verify_token):
        user = User(id="user-1", email="user@example.com", password_hash="hash")
        db = AsyncMock()
        db.execute.return_value = ScalarResult(user)

        authenticated = await get_current_user(SimpleNamespace(credentials="token"), db)

        self.assertIs(authenticated, user)
        db.commit.assert_awaited_once_with()

    @patch("app.sandbox.manager.get_workspace_root", return_value="/workspace")
    @patch("app.sandbox.manager.get_or_assign_sandbox")
    async def test_existing_workspace_releases_its_read_transaction(self, assign_sandbox, _workspace_root):
        assign_sandbox.return_value = {"id": "sandbox-a", "url": "http://sandbox"}
        workspace = SimpleNamespace(
            id="workspace-1",
            user_id="user-1",
            sandbox_id="sandbox-a",
            directory="/workspace/workspace-1",
        )
        db = AsyncMock()
        db.execute.return_value = ScalarResult(workspace)

        _, result = await get_or_create_workspace(SimpleNamespace(id="user-1"), db)

        self.assertIs(result, workspace)
        db.commit.assert_awaited_once_with()

    @patch("app.proxy.opencode_proxy._require_owned_session", new_callable=AsyncMock, return_value=None)
    async def test_sse_releases_the_database_before_streaming(self, _require_owned_session):
        request = SimpleNamespace(
            method="GET",
            headers={"accept": "text/event-stream"},
            query_params=SimpleNamespace(multi_items=lambda: []),
        )
        db = AsyncMock()

        response = await proxy_to_sandbox(
            "global/event",
            request,
            {"url": "http://sandbox", "username": "opencode", "password": ""},
            SimpleNamespace(id="user-1"),
            SimpleNamespace(directory="/workspace/user-1"),
            db,
        )

        self.assertIsInstance(response, StreamingResponse)
        db.commit.assert_awaited_once_with()

    @patch("app.proxy.opencode_proxy._require_owned_session", new_callable=AsyncMock, return_value=None)
    @patch("app.proxy.opencode_proxy.httpx.AsyncClient")
    async def test_maps_sandbox_connection_failure_to_bad_gateway(self, client_type, _require_owned_session):
        client = client_type.return_value.__aenter__.return_value
        client.request = AsyncMock(side_effect=httpx.ConnectError("sandbox offline"))
        request = SimpleNamespace(
            method="GET",
            headers={"accept": "application/json"},
            query_params=SimpleNamespace(multi_items=lambda: []),
            body=AsyncMock(return_value=b""),
        )

        with self.assertRaises(HTTPException) as raised:
            await proxy_to_sandbox(
                "global/health",
                request,
                {"url": "http://sandbox", "username": "opencode", "password": ""},
                SimpleNamespace(id="user-1"),
                SimpleNamespace(directory="/workspace/user-1"),
                AsyncMock(),
            )

        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
