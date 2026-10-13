from __future__ import annotations

from pathlib import Path

from agent_loco.runtime.related import format_goal_files, gameplay_scene, goal_files, goal_scene

GOAL = (
    'ERROR: Node not found: "Player" (relative to "/root/MainScene").\n'
    "    [0] _process (res://scripts/main_game.gd:60)\n"
)


def _godot_repo(root: Path) -> None:
    (root / "project.godot").write_text('run/main_scene="res://scenes/menu.tscn"\n')
    (root / "scripts").mkdir()
    (root / "scripts" / "main_game.gd").write_text("extends Node2D\n")
    (root / "scenes").mkdir()
    (root / "scenes" / "menu.tscn").write_text('[gd_scene format=3]\n\n[node name="Menu"]\n')
    (root / "scenes" / "main_scene.tscn").write_text(
        "[gd_scene format=3]\n\n"
        '[ext_resource type="Script" path="res://scripts/main_game.gd" id="1"]\n\n'
        '[node name="MainScene" type="Node2D"]\n'
        'script = ExtResource("1")\n\n'
        '[node name="GameArea" type="Node2D" parent="."]\n\n'
        '[node name="Player" type="Sprite2D" parent="GameArea"]\n'
    )


def test_goal_files_find_the_script_and_the_scene_that_owns_it(tmp_path: Path) -> None:
    _godot_repo(tmp_path)
    names = [path.relative_to(tmp_path.resolve()).as_posix() for path in goal_files(tmp_path, GOAL)]
    assert names == ["scripts/main_game.gd", "scenes/main_scene.tscn"]
    assert goal_scene(tmp_path, GOAL) == "res://scenes/main_scene.tscn"
    text = format_goal_files(tmp_path, GOAL)
    assert text.startswith("Files named by the goal:")
    assert 'parent="GameArea"' in text


def test_gameplay_scene_is_the_level_start_opens(tmp_path: Path) -> None:
    _godot_repo(tmp_path)
    (tmp_path / "scripts" / "menu.gd").write_text(
        "func _on_start_pressed() -> void:\n"
        '\tget_tree().change_scene_to_file("res://scenes/main_scene.tscn")\n',
        encoding="utf-8",
    )
    (tmp_path / "scenes" / "menu.tscn").write_text(
        "[gd_scene format=3]\n\n"
        '[ext_resource type="Script" path="res://scripts/menu.gd" id="1"]\n\n'
        '[node name="Menu" type="Control"]\n'
        'script = ExtResource("1")\n',
        encoding="utf-8",
    )
    goal = "The main level and player are a blank screen after you start"
    assert gameplay_scene(tmp_path, goal) == "res://scenes/main_scene.tscn"
    assert gameplay_scene(tmp_path, "Make the game more fun") is None


def test_goal_files_are_empty_when_the_goal_names_nothing(tmp_path: Path) -> None:
    _godot_repo(tmp_path)
    assert goal_files(tmp_path, "Make the game more fun") == []
    assert goal_scene(tmp_path, "Make the game more fun") is None
    assert format_goal_files(tmp_path, "Make the game more fun") == ""
