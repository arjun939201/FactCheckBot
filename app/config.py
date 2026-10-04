from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_name: str = "Fact Check"
    app_version: str = "1.1.0"
    groq_api_key: str | None = None
    # Keep the known-good current fallback as the default primary so a stale
    # retired model does not cause every request to pay for a failed retry.
    groq_model: str = "openai/gpt-oss-120b"
    groq_fallback_model: str | None = None
    groq_vision_model: str | None = None
    groq_vision_fallback_model: str | None = None
    groq_transcription_model: str = "whisper-large-v3-turbo"
    database_url: str = "sqlite:///./factcheck.db"
    request_timeout: float = Field(default=20.0, gt=0, le=120)
    max_article_chars: int = Field(default=30000, gt=1000, le=100000)
    max_history_items: int = Field(default=200, gt=1, le=1000)
    cors_origins: str = "*"
    allowed_hosts: str = "*"
    search_timeout: float = Field(default=10.0, gt=0, le=60)
    max_search_results: int = Field(default=12, gt=1, le=30)
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [x.strip() for x in self.allowed_hosts.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
