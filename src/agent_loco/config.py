from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Environment variables use the LOCO_ prefix."""

    model_config = SettingsConfigDict(
        env_prefix="LOCO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    model_name: str = "Qwen2.5-Coder:14b"
    model_base_url: str = "http://127.0.0.1:11434/v1"
    model_api_key: str = "ollama"
    max_iterations: int = Field(default=40, ge=1, le=200)
    auto_commit: bool = True
    require_tests: bool = True
    create_pr: bool = False
    watch_interval_seconds: int = Field(default=300, ge=10)
    command_timeout_seconds: int = Field(default=180, ge=5)
    log_level: str = "INFO"
    git_author_name: str | None = None
    git_author_email: str | None = None
