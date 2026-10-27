from __future__ import annotations

from pathlib import Path

from tests.support import init_git_repo

from agent_loco.config import Settings
from agent_loco.llm.client import AssistantTurn, ScriptedClient, ToolCall
from agent_loco.runtime.improve import run_cycle
from agent_loco.runtime.plan import format_plan, ground_plan, parse_plan

PLAN = (
    '{"steps": ['
    '{"do": "Read where add is defined", "file": "app.py", "check": "the return line is known"},'
    '{"do": "Return the sum", "file": "app.py", "check": "python3 check.py passes"}'
    "]}"
)


def test_ground_plan_starts_from_the_scene_instead_of_a_null_check(tmp_path: Path) -> None:
    scene = tmp_path / "scenes"
    scene.mkdir()
    (scene / "main.tscn").write_text('[gd_scene]\n[node name="MainScene" type="Node2D"]\n')
    planned = parse_plan(
        '{"steps": [{"do": "Add a null check around Player", "file": "scripts/main.gd", '
        '"check": "no crash"}]}'
    )
    goal = 'ERROR: Node not found: "Player" (relative to "/root/MainScene") at scripts/main.gd:60'
    grounded = ground_plan(planned, goal, tmp_path)
    assert grounded is not None
    assert grounded.steps[0].file == "scenes/main.tscn"
    assert "do not add a null check" in grounded.steps[0].do.lower()
    assert all(step.file != "scripts/main.gd" for step in grounded.steps)


def test_parse_plan_reads_steps_and_formats_checks() -> None:
    plan = parse_plan(f"Here you go:\n```json\n{PLAN}\n```")
    assert plan is not None
    assert [step.file for step in plan.steps] == ["app.py", "app.py"]
    text = format_plan(plan)
    assert "1. Read where add is defined [app.py] Check: the return line is known" in text
    assert "2. Return the sum" in text
    assert parse_plan("no json here") is None


def _write(content: str, call_id: str) -> AssistantTurn:
    return AssistantTurn(
        text=None,
        tool_calls=[
            ToolCall(
                id=call_id,
                name="write_file",
                arguments={"path": "app.py", "content": content},
            )
        ],
    )


def test_cycle_makes_a_new_plan_after_two_missed_retries(
    tmp_path: Path, settings: Settings
) -> None:
    (tmp_path / "app.py").write_text("def add(left, right):\n    return left + right\n")
    (tmp_path / "check.py").write_text("from app import add\nassert add(2, 3) == 5\n")
    loco = tmp_path / ".loco"
    loco.mkdir()
    (loco / "config.yaml").write_text(
        "name: fixture\ntest_command: python3 check.py\nmax_repair_attempts: 3\n"
        "publish:\n  enabled: false\n",
        encoding="utf-8",
    )
    init_git_repo(tmp_path)
    body = "def add(left, right):\n    return left + right\n"
    replanned = '{"steps": [{"do": "Add a docstring to add", "file": "app.py", "check": "seen"}]}'
    llm = ScriptedClient(
        [
            AssistantTurn(text=PLAN),
            _write(body + "# one\n", "w1"),
            AssistantTurn(text="Done."),
            _write(body + "# one\n# step\n", "w1b"),
            AssistantTurn(text="Step two done."),
            AssistantTurn(text='{"met": false, "reason": "add has no docstring"}'),
            _write(body + "# two\n", "w2"),
            AssistantTurn(text="Done."),
            AssistantTurn(text='{"met": false, "reason": "add still has no docstring"}'),
            AssistantTurn(text=replanned),
            _write('def add(left, right):\n    """Sum."""\n    return left + right\n', "w3"),
            AssistantTurn(text="Done."),
            AssistantTurn(text='{"met": true, "reason": "docstring added"}'),
        ]
    )
    planned = settings.model_copy(update={"plan_goals": True, "auto_commit": False})
    result = run_cycle(tmp_path, planned, llm, goal="Document add in app.py")
    assert result.status == "success", result.reason
    steps = [event.get("message", "") for event in result.events if event["kind"] == "step"]
    assert "Two retries missed the goal; making a new plan from what failed..." in steps
    assert "Plan 1/1: Add a docstring to add [app.py]" in steps
    replan_prompt = llm.calls[9][-1]["content"]
    assert "The last plan was carried out" in replan_prompt
    assert "add still has no docstring" in replan_prompt
    assert "Carrying out the new plan." in steps
    assert "Do only step 1 of 1" in llm.calls[10][1]["content"]
    assert "Add a docstring to add" in llm.calls[10][1]["content"]


def test_hidden_directory_edit_counts_as_changing_the_planned_file(
    tmp_path: Path, settings: Settings
) -> None:
    plugin = tmp_path / ".omp-plugin"
    plugin.mkdir()
    (plugin / "marketplace.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "check.py").write_text("print('ok')\n", encoding="utf-8")
    loco = tmp_path / ".loco"
    loco.mkdir()
    (loco / "config.yaml").write_text(
        "name: fixture\ntest_command: python3 check.py\nmax_repair_attempts: 0\n"
        "publish:\n  enabled: false\n",
        encoding="utf-8",
    )
    init_git_repo(tmp_path)
    plan = (
        '{"steps": [{"do": "Add a name field", "file": ".omp-plugin/marketplace.json", '
        '"check": "name is present"}]}'
    )
    llm = ScriptedClient(
        [
            AssistantTurn(text=plan),
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="w1",
                        name="write_file",
                        arguments={
                            "path": ".omp-plugin/marketplace.json",
                            "content": '{"name": "honk"}\n',
                        },
                    )
                ],
            ),
            AssistantTurn(text="Added the name."),
            AssistantTurn(text='{"met": true, "reason": "name field is present"}'),
        ]
    )
    planned = settings.model_copy(update={"plan_goals": True, "auto_commit": False})
    result = run_cycle(tmp_path, planned, llm, goal="Name the marketplace")
    assert result.status == "success", result.reason
    steps = [event.get("message", "") for event in result.events if event["kind"] == "step"]
    assert not any("did not change" in message for message in steps)
    assert '"name": "honk"' in (plugin / "marketplace.json").read_text(encoding="utf-8")


def test_cycle_plans_the_goal_and_hands_the_plan_to_agent_and_reviewer(
    tmp_path: Path, settings: Settings
) -> None:
    (tmp_path / "app.py").write_text("def add(left, right):\n    return left - right\n")
    (tmp_path / "check.py").write_text("from app import add\nassert add(2, 3) == 5\n")
    loco = tmp_path / ".loco"
    loco.mkdir()
    (loco / "config.yaml").write_text(
        "name: fixture\ntest_command: python3 check.py\nmax_repair_attempts: 0\n"
        "publish:\n  enabled: false\n",
        encoding="utf-8",
    )
    init_git_repo(tmp_path)
    llm = ScriptedClient(
        [
            AssistantTurn(text=PLAN),
            _write("def add(left, right):\n    return left - right  # seen\n", "w0"),
            AssistantTurn(text="Read the return line."),
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="write_file",
                        arguments={
                            "path": "app.py",
                            "content": "def add(left, right):\n    return left + right\n",
                        },
                    )
                ],
            ),
            AssistantTurn(text="Fixed add."),
            AssistantTurn(text='{"met": true, "reason": "add returns the sum"}'),
        ]
    )
    planned = settings.model_copy(update={"plan_goals": True, "auto_commit": False})
    result = run_cycle(tmp_path, planned, llm, goal="Fix add in app.py so check.py passes")
    assert result.status == "success", result.reason
    agent_prompt = llm.calls[1][1]["content"]
    assert "Do only step 1 of 2" in agent_prompt
    assert "Plan:" in agent_prompt
    assert "Files named by the goal:" in agent_prompt
    assert "--- app.py ---" in agent_prompt
    review_prompt = llm.calls[-1][-1]["content"]
    assert "every step's check holds" in review_prompt
    assert "Return the sum" in review_prompt


def test_cycle_stops_when_the_same_miss_comes_back(tmp_path: Path, settings: Settings) -> None:
    (tmp_path / "app.py").write_text("def add(left, right):\n    return left + right\n")
    (tmp_path / "check.py").write_text("from app import add\nassert add(2, 3) == 5\n")
    loco = tmp_path / ".loco"
    loco.mkdir()
    (loco / "config.yaml").write_text(
        "name: fixture\ntest_command: python3 check.py\nmax_repair_attempts: 4\n"
        "publish:\n  enabled: false\n",
        encoding="utf-8",
    )
    init_git_repo(tmp_path)
    body = "def add(left, right):\n    return left + right\n\n# guarded\n"
    plan = (
        '{"cause": "Player is missing", "steps": ['
        '{"do": "Point the script at the scene node", "file": "app.py", '
        '"check": "the error stops"}]}'
    )
    miss = "The diff only adds a null check and the node is not found"
    llm = ScriptedClient(
        [
            AssistantTurn(text=plan),
            _write(body, "w1"),
            AssistantTurn(text="The task is complete."),
            AssistantTurn(text='{"met": false, "reason": "' + miss + '"}'),
            _write(body + "\n# still guarded\n", "w2"),
            AssistantTurn(text="The task is complete."),
            AssistantTurn(text="Noted."),
            AssistantTurn(text="Noted again."),
            AssistantTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="agents",
                        name="write_file",
                        arguments={
                            "path": "AGENTS.md",
                            "content": "# How to run\n\ngodot --path .\n",
                        },
                    )
                ],
            ),
            AssistantTurn(text="Documented how to run."),
            AssistantTurn(
                text=None,
                tool_calls=[ToolCall(id="tests", name="run_tests", arguments={})],
            ),
            AssistantTurn(text="Tests still pass."),
            AssistantTurn(text='{"met": false, "reason": "' + miss + ' again"}'),
        ]
    )
    planned = settings.model_copy(update={"plan_goals": True, "auto_commit": False})
    result = run_cycle(tmp_path, planned, llm, goal="Fix the crash in app.py")
    assert result.status == "failed"
    steps = [event.get("message", "") for event in result.events if event["kind"] == "step"]
    interesting = [
        message
        for message in steps
        if message.startswith("Goal ") or message.startswith("The same")
    ]
    assert any(message.startswith("Goal retry 1/") for message in steps), interesting
    assert not any(message.startswith("Goal retry 2/") for message in steps), interesting
    assert "The same miss came back. Stopping instead of repeating it." in steps
