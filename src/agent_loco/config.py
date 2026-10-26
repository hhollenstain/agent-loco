from __future__ import annotations

from pydantic import Field, field_validator
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
    kiwifs_url: str = "http://127.0.0.1:3333"
    kiwifs_api_key: str = ""
    kiwifs_prefix: str = ""
    max_iterations: int = Field(default=40, ge=1, le=200)
    context_window: int | None = Field(default=None, ge=512, le=1_048_576)
    auto_commit: bool = True
    require_tests: bool = True
    create_pr: bool = True
    watch_interval_seconds: int = Field(default=300, ge=10)
    command_timeout_seconds: int = Field(default=180, ge=5)
    plan_goals: bool = True
    log_level: str = "INFO"
    git_author_name: str | None = None
    git_author_email: str | None = None

    @field_validator("context_window", mode="before")
    @classmethod
    def _blank_context_window(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    def __init__(self, **data):
        super().__init__(**data)
        self._validate_required_settings()

    def _validate_required_settings(self) -> None:
        """Validate that all required environment variables are set.

        Raises:
            ValueError: If any required setting is missing.

        Required settings are critical for the application to function:
        - LLM connection (model_name, model_base_url)
        - Minimum operational thresholds
        """
        required_vars = [
            ("LOCO_MODEL_NAME", self.model_name),
            ("LOCO_MODEL_BASE_URL", self.model_base_url),
        ]

        missing = [name for name, value in required_vars if not value]

        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}.\n"
                f"Set these before starting the agent. Example:\n"
                f"  export LOCO_MODEL_NAME='your-model-name'\n"
                f"  export LOCO_MODEL_BASE_URL='http://your-server:11434/v1'"
            )
