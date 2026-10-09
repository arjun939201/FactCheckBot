from functools import lru_cache
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_name: str = "Fact Check"
    app_version: str = "1.2.0"
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
    rate_limit_per_minute: int = Field(default=20, gt=1, le=120)
    media_vision_calls_per_request: int = Field(default=6, gt=0, le=20)
    media_vision_retry_attempts: int = Field(default=2, ge=0, le=4)
    media_vision_retry_max_delay: float = Field(default=6.0, gt=0, le=30)
    max_request_bytes: int = Field(default=40 * 1024 * 1024, gt=1024, le=100 * 1024 * 1024)
    groq_max_instruction_chars: int = Field(default=28000, gt=4000, le=60000)
    groq_claim_input_chars: int = Field(default=9000, gt=1000, le=20000)
    groq_evidence_excerpt_chars: int = Field(default=1400, gt=300, le=5000)
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    @model_validator(mode="after")
    def validate_production_settings(self):
        if self.app_env.strip().lower() != "production":
            return self

        problems = []
        if not self.groq_api_key or not self.groq_api_key.strip():
            problems.append("GROQ_API_KEY must be configured")
        if not self.database_url.lower().startswith(("postgresql://", "postgres://")):
            problems.append("DATABASE_URL must point to managed PostgreSQL in production")
        origins = self.cors_origin_list
        if not origins or "*" in origins or any(not x.startswith(("https://", "http://")) for x in origins):
            problems.append("CORS_ORIGINS must contain explicit http(s) origins and must not contain *")
        hosts = self.allowed_host_list
        if not hosts or "*" in hosts or any("://" in x or "/" in x for x in hosts):
            problems.append("ALLOWED_HOSTS must contain explicit hostnames and must not contain *")
        if problems:
            raise ValueError("Invalid production configuration: " + "; ".join(problems))
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [x.strip() for x in self.allowed_hosts.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
