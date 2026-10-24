"""Scheduled bot tasks stored in the app's `.loco/schedules.json`."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from agent_loco.logging import utcnow_iso

log = logging.getLogger("loco")

SCHEDULES_FILENAME = "schedules.json"
FILES_DIRNAME = "schedule-files"
MAX_RUNS = 30
MAX_NAME = 80
MAX_GOAL = 20_000
MAX_OUTPUT_FILES = 40
_OPEN = frozenset({"queued", "running"})
_KINDS = frozenset({"hourly", "daily", "weekly"})
_TARGETS = frozenset({"project", "files"})
_SKIP_OUTPUT = frozenset({".loco", ".git", "__pycache__", "node_modules", ".venv", "venv"})
_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_LOCK = threading.Lock()


def schedules_path(root: Path) -> Path:
    return Path(root) / ".loco" / SCHEDULES_FILENAME


def human_cadence(cadence: dict) -> str:
    """Sentence a person can read, such as 'Weekdays at 8:00 AM'."""
    kind = cadence.get("kind")
    minute = int(cadence.get("minute") or 0)
    if kind == "hourly":
        if minute == 0:
            return "Every hour"
        return f"Every hour at :{minute:02d}"
    clock = _clock_label(int(cadence.get("hour") or 0), minute)
    if kind == "daily":
        return f"Every day at {clock}"
    if kind != "weekly":
        return ""
    names = [_WEEKDAYS[day] for day in sorted(int(day) for day in cadence.get("weekdays") or [])]
    if len(names) == 7:
        return f"Every day at {clock}"
    if names == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
        return f"Weekdays at {clock}"
    if names == ["Saturday", "Sunday"]:
        return f"Weekends at {clock}"
    if len(names) == 1:
        return f"Every {names[0]} at {clock}"
    if len(names) == 2:
        return f"Every {names[0]} and {names[1]} at {clock}"
    if not names:
        return ""
    return f"Every {', '.join(names[:-1])}, and {names[-1]} at {clock}"


def cron_expression(cadence: dict) -> str:
    """Five-field cron. Weekdays use 0 for Sunday and 1 for Monday."""
    minute = int(cadence["minute"])
    kind = cadence["kind"]
    if kind == "hourly":
        return f"{minute} * * * *"
    hour = int(cadence["hour"])
    if kind == "daily":
        return f"{minute} {hour} * * *"
    days = sorted((int(day) + 1) % 7 for day in cadence["weekdays"])
    listed = ",".join(str(day) for day in days)
    return f"{minute} {hour} * * {listed}"


def next_occurrence(cadence: dict, after: datetime) -> datetime:
    """Next run strictly after `after`, using that clock's local time."""
    local_after = _as_local(after)
    minute = int(cadence["minute"])
    kind = cadence["kind"]
    if kind == "hourly":
        candidate = local_after.replace(minute=minute, second=0, microsecond=0)
        if candidate <= local_after:
            candidate += timedelta(hours=1)
        return candidate
    hour = int(cadence["hour"])
    if kind == "daily":
        candidate = local_after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_after:
            candidate += timedelta(days=1)
            candidate = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate
    wanted = {int(day) for day in cadence.get("weekdays") or []}
    if not wanted:
        raise ValueError("Pick at least one day.")
    for offset in range(8):
        day = local_after + timedelta(days=offset)
        candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate.weekday() in wanted and candidate > local_after:
            return candidate
    raise ValueError("Pick at least one day.")


def normalize_cadence(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Choose how often the schedule runs.")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in _KINDS:
        raise ValueError("Choose hourly, daily, or weekly.")
    try:
        minute = int(raw.get("minute", 0))
        hour = int(raw.get("hour", 9))
    except (TypeError, ValueError) as exc:
        raise ValueError("Choose a valid time.") from exc
    if not 0 <= minute <= 59 or not 0 <= hour <= 23:
        raise ValueError("Choose a valid time.")
    weekdays = _weekdays(raw.get("weekdays"))
    if kind == "weekly" and not weekdays:
        raise ValueError("Pick at least one day.")
    if not weekdays:
        weekdays = [0, 1, 2, 3, 4]
    return {"kind": kind, "minute": minute, "hour": hour, "weekdays": weekdays}


def list_schedules(root: Path) -> list[dict]:
    with _LOCK:
        items = [entry for entry in _read(root)["schedules"] if isinstance(entry, dict)]
    items.sort(key=lambda entry: str(entry.get("created_at") or ""), reverse=True)
    return [_public(entry) for entry in items]


def get_schedule(root: Path, schedule_id: str) -> dict:
    for item in list_schedules(root):
        if item["id"] == schedule_id:
            return item
    raise KeyError(schedule_id)


def file_task_goal(goal: str) -> str:
    """Tell the agent this run saves documents instead of changing a software project."""
    return (
        "This folder is for documents, not a software project. "
        "Do the task below and save the result as files in this folder "
        "(CSV, markdown, or other documents). "
        "Do not add tests, git, or a pull request.\n\n" + goal.strip()
    )


def output_files(folder: Path, *, limit: int = MAX_OUTPUT_FILES) -> list[dict]:
    """Files a document schedule has saved, skipping hidden and tool directories."""
    root = Path(folder)
    if not root.is_dir():
        return []
    found: list[dict] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part.startswith(".") or part in _SKIP_OUTPUT for part in relative.parts):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        found.append({"name": relative.as_posix(), "bytes": size})
        if len(found) >= limit:
            break
    return found


def resolve_output_file(schedule: dict, relative: str) -> Path | None:
    """A saved document inside a file schedule's folder, or None."""
    if schedule.get("kind") != "files":
        return None
    root = Path(str(schedule.get("workspace") or "")).resolve()
    if not root.is_dir():
        return None
    raw = (relative or "").strip()
    if not raw or raw.startswith(("/", "\\")):
        return None
    parts = Path(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    target = (root / raw).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    return target


def upsert_schedule(root: Path, fields: dict, schedule_id: str | None = None) -> dict:
    cadence = normalize_cadence(fields.get("cadence"))
    name = _clean_name(fields.get("name"))
    goal = _clean_goal(fields.get("goal"))
    kind = _target_kind(fields.get("kind"))
    enabled = bool(fields.get("enabled", True))
    auto_commit = False if kind == "files" else bool(fields.get("auto_commit", False))
    create_pr = False if kind == "files" else bool(fields.get("create_pr", False))
    now = datetime.now().astimezone()
    with _LOCK:
        payload = _read(root)
        items: list[dict] = payload["schedules"]
        if schedule_id:
            item = next((entry for entry in items if entry.get("id") == schedule_id), None)
            if item is None:
                raise KeyError(schedule_id)
        else:
            item = {"id": uuid4().hex, "created_at": utcnow_iso(), "runs": []}
            items.insert(0, item)
        workspace = _bind_folder(
            root,
            kind,
            str(fields.get("workspace") or ""),
            str(item["id"]),
            previous=item,
        )
        previous = item.get("cadence")
        item["name"] = name
        item["goal"] = goal
        item["kind"] = kind
        item["workspace"] = workspace
        item["enabled"] = enabled
        item["auto_commit"] = auto_commit
        item["create_pr"] = create_pr
        item["cadence"] = cadence
        item["updated_at"] = utcnow_iso()
        nxt = _parse_time(item.get("next_run_at"))
        if previous != cadence or nxt is None or (enabled and nxt <= now):
            item["next_run_at"] = _iso(next_occurrence(cadence, now))
        _write(root, payload)
        return _public(item)


def delete_schedule(root: Path, schedule_id: str) -> None:
    with _LOCK:
        payload = _read(root)
        items = payload["schedules"]
        kept = [item for item in items if item.get("id") != schedule_id]
        if len(kept) == len(items):
            raise KeyError(schedule_id)
        payload["schedules"] = kept
        _write(root, payload)


def claim_due(root: Path, now: datetime) -> list[dict]:
    """Record a queued run for each enabled schedule whose time has arrived."""
    moment = _as_local(now)
    with _LOCK:
        payload = _read(root)
        claimed: list[dict] = []
        changed = False
        for item in payload["schedules"]:
            if not item.get("enabled") or _open_run(item):
                continue
            nxt = _parse_time(item.get("next_run_at"))
            if nxt is None or nxt > moment:
                continue
            _append_run(item, moment, advance=True)
            changed = True
            claimed.append(_public(item))
        if changed:
            _write(root, payload)
        return claimed


def start_run(root: Path, schedule_id: str, now: datetime) -> dict:
    """Queue a run immediately. The regular next time stays where it is."""
    moment = _as_local(now)
    with _LOCK:
        payload = _read(root)
        item = next(
            (entry for entry in payload["schedules"] if entry.get("id") == schedule_id),
            None,
        )
        if item is None:
            raise KeyError(schedule_id)
        if _open_run(item):
            raise ValueError("This schedule is already running.")
        _append_run(item, moment, advance=False)
        _write(root, payload)
        return _public(item)


def attach_task(root: Path, schedule_id: str, run_id: str, task_id: str) -> None:
    _update_run(root, schedule_id, run_id, task_id=task_id)


def fail_run(root: Path, schedule_id: str, run_id: str, error: str) -> None:
    _update_run(
        root,
        schedule_id,
        run_id,
        status="error",
        error=error,
        finished_at=utcnow_iso(),
    )


def sync_runs(
    root: Path, lookup: Callable[[str], dict | None], now: datetime | None = None
) -> None:
    """Copy task status onto open runs. A missing task is a finished error."""
    moment = _as_local(now or datetime.now().astimezone())
    with _LOCK:
        payload = _read(root)
        changed = False
        for item in payload["schedules"]:
            for run in item.get("runs") or []:
                if not isinstance(run, dict) or run.get("status") not in _OPEN:
                    continue
                task_id = str(run.get("task_id") or "")
                if not task_id:
                    started = _parse_time(run.get("started_at"))
                    if started is not None and moment - started > timedelta(minutes=2):
                        run["status"] = "error"
                        run["error"] = "The run did not start."
                        run["finished_at"] = utcnow_iso()
                        changed = True
                    continue
                info = lookup(task_id)
                if info is None:
                    run["status"] = "error"
                    run["error"] = "Run stopped when loco restarted."
                    run["finished_at"] = utcnow_iso()
                    changed = True
                    continue
                status = str(info.get("status") or run.get("status") or "queued")
                summary = str(info.get("summary") or "")
                error = str(info.get("error") or "")
                finished = info.get("finished_at")
                if (
                    run.get("status") == status
                    and run.get("summary") == summary
                    and run.get("error") == error
                ):
                    continue
                run["status"] = status
                run["summary"] = summary
                run["error"] = error
                if status not in _OPEN:
                    run["finished_at"] = str(finished or utcnow_iso())
                changed = True
        if changed:
            _write(root, payload)


class ScheduleClock:
    """Wakes on an interval, claims due schedules, and hands each one to `on_due`."""

    def __init__(
        self,
        root: Path,
        on_due: Callable[[dict], None],
        lookup: Callable[[str], dict | None],
        *,
        interval_seconds: float = 20,
    ) -> None:
        self.root = Path(root)
        self._on_due = on_due
        self._lookup = lookup
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self, now: datetime | None = None) -> int:
        sync_runs(self.root, self._lookup, now)
        due = claim_due(self.root, now or datetime.now().astimezone())
        for schedule in due:
            self._on_due(schedule)
        return len(due)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="loco-schedules", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.tick()
            except Exception:
                log.exception("schedule tick failed")


def _clock_label(hour: int, minute: int) -> str:
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {suffix}"


def _weekdays(raw: object) -> list[int]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("Pick at least one day.")
    days: list[int] = []
    for day in raw:
        try:
            number = int(day)
        except (TypeError, ValueError) as exc:
            raise ValueError("Pick at least one day.") from exc
        if not 0 <= number <= 6:
            raise ValueError("Pick at least one day.")
        if number not in days:
            days.append(number)
    days.sort()
    return days


def _clean_name(value: object) -> str:
    name = str(value or "").strip()
    if not name:
        raise ValueError("Name the schedule.")
    if len(name) > MAX_NAME:
        raise ValueError("Keep the schedule name under 80 characters.")
    return name


def _clean_goal(value: object) -> str:
    goal = str(value or "").strip()
    if not goal:
        raise ValueError("Describe what this schedule should do.")
    if len(goal) > MAX_GOAL:
        raise ValueError("That goal is too long.")
    return goal


def _target_kind(value: object) -> str:
    kind = str(value or "project").strip().lower()
    if kind not in _TARGETS:
        raise ValueError("Choose a project or a file folder.")
    return kind


def _bind_folder(
    root: Path,
    kind: str,
    workspace: str,
    schedule_id: str,
    *,
    previous: dict,
) -> str:
    if kind == "project":
        return str(_workspace_dir(workspace))
    text = workspace.strip()
    if not text and previous.get("kind") == "files" and previous.get("workspace"):
        path = Path(str(previous["workspace"])).expanduser()
    elif not text:
        path = Path(root) / ".loco" / FILES_DIRNAME / schedule_id
    else:
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = Path(root) / path
    path = path.resolve()
    if path.exists() and not path.is_dir():
        raise ValueError("Save files in a folder, not a file.")
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _workspace_dir(value: str) -> Path:
    text = value.strip()
    if not text:
        raise ValueError("Choose a project folder.")
    path = Path(text).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Project folder does not exist: {path}")
    return path


def _as_local(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return moment.astimezone()


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def _open_run(item: dict) -> bool:
    return any(
        isinstance(run, dict) and run.get("status") in _OPEN for run in item.get("runs") or []
    )


def _append_run(item: dict, now: datetime, *, advance: bool) -> dict:
    run = {
        "id": uuid4().hex,
        "task_id": None,
        "started_at": _iso(now),
        "finished_at": None,
        "status": "queued",
        "summary": "",
        "error": "",
    }
    runs = item.setdefault("runs", [])
    runs.insert(0, run)
    del runs[MAX_RUNS:]
    if advance:
        item["next_run_at"] = _iso(next_occurrence(item["cadence"], now))
    item["updated_at"] = utcnow_iso()
    return run


def _update_run(root: Path, schedule_id: str, run_id: str, **changes: object) -> None:
    with _LOCK:
        payload = _read(root)
        for item in payload["schedules"]:
            if item.get("id") != schedule_id:
                continue
            for run in item.get("runs") or []:
                if isinstance(run, dict) and run.get("id") == run_id:
                    run.update(changes)
                    _write(root, payload)
                    return
        return


def _public(item: dict) -> dict:
    cadence = item.get("cadence") if isinstance(item.get("cadence"), dict) else {}
    runs = [dict(run) for run in item.get("runs") or [] if isinstance(run, dict)]
    known = bool(cadence.get("kind"))
    kind = item.get("kind") if item.get("kind") in _TARGETS else "project"
    workspace = item.get("workspace") or ""
    files = output_files(Path(str(workspace))) if kind == "files" and workspace else []
    return {
        "id": item.get("id"),
        "name": item.get("name") or "",
        "goal": item.get("goal") or "",
        "kind": kind,
        "workspace": workspace,
        "files": files,
        "enabled": bool(item.get("enabled")),
        "auto_commit": bool(item.get("auto_commit")),
        "create_pr": bool(item.get("create_pr")),
        "cadence": {
            "kind": cadence.get("kind"),
            "minute": int(cadence.get("minute") or 0),
            "hour": int(cadence.get("hour") or 0),
            "weekdays": list(cadence.get("weekdays") or []),
        },
        "cron": cron_expression(cadence) if known else "",
        "label": human_cadence(cadence) if known else "",
        "next_run_at": item.get("next_run_at"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "runs": runs,
    }


def _read(root: Path) -> dict:
    path = schedules_path(root)
    if not path.exists():
        return {"schedules": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schedules": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("schedules"), list):
        return {"schedules": []}
    return payload


def _write(root: Path, payload: dict) -> None:
    path = schedules_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
