"""Tests for the bundled research skill."""

from pathlib import Path

import yaml

from agent_loco.runtime.project import write_default_project_files
from agent_loco.runtime.skills import (
    bundled_skills_root,
    compose_system_prompt,
    enabled_skill_names,
    list_skills,
    save_enabled_skills,
)


def test_init_enables_research_skill(tmp_path: Path) -> None:
    write_default_project_files(tmp_path)
    config = yaml.safe_load((tmp_path / ".loco" / "config.yaml").read_text(encoding="utf-8"))
    enabled = config.get("skills", {}).get("enabled", [])
    assert "research" in enabled
    assert "implement" in enabled
    assert enabled_skill_names(tmp_path) == ["implement", "research", "tdd", "ui"]


def test_research_skill_is_bundled_and_injected(tmp_path: Path) -> None:
    skill_path = bundled_skills_root() / "research" / "SKILL.md"
    assert skill_path.is_file()
    skills = {item.name: item for item in list_skills(tmp_path)}
    assert "research" in skills
    assert skills["research"].origin == "bundled"
    assert "web_search" in skills["research"].body
    assert "fetch_url" in skills["research"].body
    write_default_project_files(tmp_path)
    save_enabled_skills(tmp_path, ["research"])
    prompt = compose_system_prompt(tmp_path)
    assert "### research" in prompt
    assert "official docs" in prompt
