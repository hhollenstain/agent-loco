from __future__ import annotations

import pytest

from agent_loco.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        model_name="scripted",
        model_base_url="http://127.0.0.1:9/v1",
        auto_commit=True,
        require_tests=True,
        create_pr=False,
        max_iterations=8,
        command_timeout_seconds=30,
        git_author_name="loco-test",
        git_author_email="loco@test.local",
    )
