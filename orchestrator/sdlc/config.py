from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CORS_ORIGINS = "http://localhost:5174,http://insights.pragmattie-sync.localhost"
DEFAULT_GITHUB_API_URL = "https://api.github.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "mysql+pymysql://pragmattie_sync:pragmattie_sync@db:3306/pragmattie_sync"
    cors_origins: str = DEFAULT_CORS_ORIGINS
    github_token: str = Field(default="", repr=False)
    github_repo: str = ""
    github_api_url: str = DEFAULT_GITHUB_API_URL
    anthropic_api_key: str = Field(default="", repr=False)
    risk_model: str = "claude-sonnet-5"
    triage_model: str = "claude-haiku-4-5-20251001"
    risk_max_output_tokens: int = 1500
    agent_timeout_seconds: float = 60

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
