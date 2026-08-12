from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://watcher:watcher@localhost:5432/watcher"
    redis_url: str = "redis://localhost:6379/0"

    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_access_token: str = ""

    anthropic_api_key: str = ""
    model_name: str = "claude-sonnet-5"

    confidence_high: float = 0.85
    confidence_low: float = 0.60
    reply_budget_ms: int = 5000


settings = Settings()
