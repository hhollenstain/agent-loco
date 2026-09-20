from __future__ import annotations

import pytest

from agent_loco.config import Settings
from agent_loco.runtime.uireview import UiEvidence


def _skipped_ui_evidence(*_args, **_kwargs) -> UiEvidence:
    return UiEvidence(ok=True, notes="skipped live browser in tests")


@pytest.fixture(autouse=True)
def _skip_live_ui_review(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent_loco.runtime.improve.collect_ui_evidence", _skipped_ui_evidence)
    monkeypatch.setattr("agent_loco.runtime.uireview.collect_ui_evidence", _skipped_ui_evidence)
    monkeypatch.setattr("agent_loco.tools.browser.collect_ui_evidence", _skipped_ui_evidence)


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
