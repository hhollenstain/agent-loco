from __future__ import annotations

import os
from pathlib import Path

from agent_loco.tools.codechange import code_fingerprint


def test_fingerprint_reuses_unchanged_files_and_sees_edits(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text("x = 1\n", encoding="utf-8")
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    first = code_fingerprint(tmp_path)
    assert code_fingerprint(tmp_path) == first
    path.write_text("x = 2\n", encoding="utf-8")
    os.utime(path, ns=(1_700_000_100_000_000_000, 1_700_000_100_000_000_000))
    assert code_fingerprint(tmp_path) != first
