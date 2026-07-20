from pydantic_settings import BaseSettings
from dotenv import load_dotenv
import os
from typing import Dict

load_dotenv()


class Settings(BaseSettings):
    jwt_secret: str = "dev-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 7
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    cors_origins: str = "http://localhost:4444,http://localhost:5173"
    sandbox_workspace_root: str = ""
    murtagh_mcp_url: str = ""
    murtagh_knowledge_root: str = ""
    murtagh_mcp_timeout_ms: int = 30000
    education_mcp_url: str = ""
    education_mcp_timeout_ms: int = 120000
    hot_topics_mcp_url: str = ""
    hot_topics_mcp_timeout_ms: int = 60000
    smart_market_base_url: str = ""
    smart_market_timeout_seconds: float = 30.0
    smart_market_api_key: str = ""
    wechat_agent_base_url: str = ""
    wechat_agent_timeout_seconds: float = 30.0
    wechat_agent_preview_wait_seconds: int = 60
    wechat_agent_preview_poll_seconds: float = 5.0
    image_upload_url: str = ""
    image_upload_field_name: str = "file"
    image_upload_timeout_seconds: float = 120.0

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()


def get_sandboxes() -> Dict[str, dict]:
    sandboxes: Dict[str, dict] = {}
    for key, value in os.environ.items():
        if key.startswith("SANDBOX_") and key.endswith("_URL"):
            sandbox_id = key[len("SANDBOX_") : -len("_URL")].lower()
            sandboxes[sandbox_id] = {
                "id": sandbox_id,
                "url": value.rstrip("/"),
                "username": os.environ.get(f"SANDBOX_{sandbox_id.upper()}_USERNAME", "opencode"),
                "password": os.environ.get(f"SANDBOX_{sandbox_id.upper()}_PASSWORD", ""),
                "workspace_root": os.environ.get(
                    f"SANDBOX_{sandbox_id.upper()}_WORKSPACE_ROOT",
                    settings.sandbox_workspace_root,
                ).rstrip("/\\"),
            }
    if not sandboxes:
        sandboxes["default"] = {
            "id": "default",
            "url": os.environ.get("SANDBOX_A_URL", "http://localhost:4096").rstrip("/"),
            "username": os.environ.get("SANDBOX_A_USERNAME", "opencode"),
            "password": os.environ.get("SANDBOX_A_PASSWORD", ""),
            "workspace_root": os.environ.get(
                "SANDBOX_A_WORKSPACE_ROOT", settings.sandbox_workspace_root
            ).rstrip("/\\"),
        }
    return sandboxes
