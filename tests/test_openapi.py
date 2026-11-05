"""HTTP bodies for new routes come from the OpenAPI spec."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agent_lbw.openapi_models import ScheduleWrite

ROOT = Path(__file__).resolve().parents[1]


def test_generated_schedule_body_includes_the_granted_model() -> None:
    assert "model_id" in ScheduleWrite.model_fields
    body = ScheduleWrite(
        name="Review",
        goal="Review this project.",
        cadence={"kind": "daily", "hour": 9, "minute": 0},
    )
    assert body.model_id == ""
    assert body.kind == "project"


def test_generated_models_match_the_spec() -> None:
    checked = subprocess.run(
        [sys.executable, "scripts/openapi_models.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stderr
