from __future__ import annotations

from pathlib import Path

from agent_loco.runtime.project import (
    default_guidelines,
    guidelines_are_custom,
    infer_test_command,
    load_goals,
    load_guidelines,
    load_project,
    mark_goal_done,
    render_tree,
    save_guidelines,
    write_default_project_files,
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


def test_load_goals_keeps_indented_issue_context(tmp_path: Path) -> None:
    loco = tmp_path / ".loco"
    loco.mkdir()
    (loco / "goals.md").write_text(
        "- [ ] #9 Ship it\n"
        "  https://github.com/acme/demo/issues/9\n"
        "\n"
        "  Implement this GitHub issue. Do the work it describes; do not only summarize it.\n"
        "\n"
        "  ## Why\n"
        "  Do the thing\n"
        "- [ ] Later\n",
        encoding="utf-8",
    )
    goals = load_goals(tmp_path, "goals.md")
    assert len(goals) == 2
    assert goals[0].startswith("#9 Ship it")
    assert "## Why" in goals[0]
    assert "Do the thing" in goals[0]
    assert goals[1] == "Later"
    assert mark_goal_done(tmp_path, "goals.md", goals[0])
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


def test_load_project_preserves_zero_repair_attempts(tmp_path: Path) -> None:
    loco = tmp_path / ".loco"
    loco.mkdir()
    (loco / "config.yaml").write_text(
        "name: demo\nmax_repair_attempts: 0\n",
        encoding="utf-8",
    )
    project = load_project(tmp_path)
    assert project.max_repair_attempts == 0


def test_init_ignores_run_logs(tmp_path: Path) -> None:
    created = write_default_project_files(tmp_path)
    nested = tmp_path / ".loco" / ".gitignore"
    root_ignore = tmp_path / ".gitignore"
    assert nested.exists()
    assert "*" in nested.read_text(encoding="utf-8")
    assert root_ignore.exists()
    assert ".loco/" in root_ignore.read_text(encoding="utf-8")
    assert root_ignore in created or nested in created
    guidelines = tmp_path / ".loco" / "guidelines.md"
    assert guidelines in created
    assert "You are loco" in guidelines.read_text(encoding="utf-8")
    config = (tmp_path / ".loco" / "config.yaml").read_text(encoding="utf-8")
    assert "max_repair_attempts: 4" in config


def test_guidelines_default_until_overridden(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert load_guidelines(empty) == default_guidelines()
    assert guidelines_are_custom(empty) is False
    saved = save_guidelines(empty, "Stay in Python. Prefer pytest.")
    assert "Stay in Python" in saved
    assert guidelines_are_custom(empty) is True
    assert load_guidelines(empty).strip() == "Stay in Python. Prefer pytest."
    restored = save_guidelines(empty, "")
    assert restored == default_guidelines()
    assert guidelines_are_custom(empty) is False
    assert not (empty / ".loco" / "guidelines.md").exists()
