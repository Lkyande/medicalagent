from pathlib import Path

from pydantic_settings import BaseSettings


_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./wechat_broker.db"
    internal_api_key: str = ""
    component_appid: str = ""
    component_appsecret: str = ""
    authorization_callback_url: str = ""
    token_encryption_key: str = ""
    required_function_ids: str = ""
    allowed_return_origins: str = ""
    wechat_api_timeout_seconds: float = 30.0
    ticket_database_url: str = ""
    ticket_database_table: str = "fa_weixin_component_verify_ticket"
    ticket_database_ssl: bool = True
    ticket_database_ssl_verify: bool = True

    model_config = {
        "env_file": _ENV_FILE,
        "env_prefix": "WECHAT_BROKER_",
        "extra": "ignore",
    }


settings = Settings()


def validate_settings() -> None:
    required = {
        "WECHAT_BROKER_DATABASE_URL": settings.database_url,
        "WECHAT_BROKER_INTERNAL_API_KEY": settings.internal_api_key,
        "WECHAT_BROKER_COMPONENT_APPID": settings.component_appid,
        "WECHAT_BROKER_COMPONENT_APPSECRET": settings.component_appsecret,
        "WECHAT_BROKER_AUTHORIZATION_CALLBACK_URL": settings.authorization_callback_url,
        "WECHAT_BROKER_TOKEN_ENCRYPTION_KEY": settings.token_encryption_key,
        "WECHAT_BROKER_TICKET_DATABASE_URL": settings.ticket_database_url,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing configuration: {', '.join(missing)}")
    if len(settings.internal_api_key) < 32:
        raise RuntimeError("WECHAT_BROKER_INTERNAL_API_KEY must contain at least 32 characters")
    if not settings.authorization_callback_url.startswith("https://"):
        raise RuntimeError("WECHAT_BROKER_AUTHORIZATION_CALLBACK_URL must use HTTPS")
