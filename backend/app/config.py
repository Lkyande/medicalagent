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
