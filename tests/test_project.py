from __future__ import annotations

from pathlib import Path

from agent_loco.runtime.project import (
    infer_test_command,
    load_goals,
    load_project,
    mark_goal_done,
    render_tree,
)


def test_infer_pytest(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert infer_test_command(tmp_path) == "pytest -q"


def test_infer_npm(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node test.js"}}', encoding="utf-8")
    assert infer_test_command(tmp_path) == "npm test"


def test_load_and_complete_checkbox_goal(tmp_path: Path) -> None:
    loco = tmp_path / ".loco"
    loco.mkdir()
    (loco / "config.yaml").write_text(
        "name: demo\ntest_command: python3 -c 'print(0)'\ngoals_file: goals.md\n",
        encoding="utf-8",
    )
    (loco / "goals.md").write_text("- [ ] Fix the adder\n- [ ] Later\n", encoding="utf-8")
    project = load_project(tmp_path)
    assert project.name == "demo"
    assert project.test_command == "python3 -c 'print(0)'"
    assert load_goals(tmp_path, "goals.md") == ["Fix the adder", "Later"]
    assert mark_goal_done(tmp_path, "goals.md", "Fix the adder")
    assert load_goals(tmp_path, "goals.md") == ["Later"]


def test_render_tree_includes_nested_source(tmp_path: Path) -> None:
    nested = tmp_path / "src" / "demo_app"
    nested.mkdir(parents=True)
    (nested / "calc.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    tree = render_tree(tmp_path)
    assert "src/" in tree
    assert "demo_app/" in tree
    assert "calc.py" in tree
    assert ".git" not in tree
