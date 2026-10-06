from __future__ import annotations

import re
from dataclasses import dataclass

_HUNK_START = re.compile(r"\+(\d+)")
_SECRET = re.compile(
    r"""(?ix)
    (?:api[_-]?key|secret|password|passwd|access[_-]?token|private[_-]?key)
    \s*[:=]\s*
    ['\"]([^'\"]{8,})['\"]
    """
)
_PEM = re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")
_LIVE_TOKEN = re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")
_PLACEHOLDER = re.compile(
    r"(?i)(example|placeholder|changeme|change[_-]me|your[-_ ]|xxx+|redacted|"
    r"dummy|fake|not-a-|\$\{|<[^>]+>)"
)
_OS_SHELL = re.compile(r"""os\.(?:system|popen)\(\s*f['\"]""")
_TODO = re.compile(r"(?i)#\s*(?:TODO|FIXME)\b")
_PLUS_PATH = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    location: str
    message: str
    suggestion: str


def review_diff(diff: str, *, goal: str = "") -> list[ReviewFinding]:
    """Review added lines the way an unattended code review would."""
    findings: list[ReviewFinding] = []
    allow_todo = bool(re.search(r"\badd\b.{0,40}\btodo\b", goal or "", re.IGNORECASE))
    for path, line_no, text in _added_lines(diff):
        if _skip_path(path):
            continue
        findings.extend(_line_findings(path, line_no, text, allow_todo=allow_todo))
    findings.extend(_missing_test_findings(diff))
    return findings


def blocking_review_findings(diff: str, *, goal: str = "") -> list[str]:
    """Error findings that should reject a goal the model marked done."""
    return [
        f"{item.location} — {item.message}"
        for item in review_diff(diff, goal=goal)
        if item.severity == "error"
    ]


def format_review(findings: list[ReviewFinding]) -> str:
    if not findings:
        return "No code review findings."
    blocks = []
    for item in findings:
        blocks.append(
            f"**[{item.severity}]** `{item.location}` — {item.message}\n"
            f"Suggestion: {item.suggestion}"
        )
    return "\n\n".join(blocks)


def _line_findings(path: str, line_no: int, text: str, *, allow_todo: bool) -> list[ReviewFinding]:
    location = f"{path}:{line_no}" if path else f"line {line_no}"
    findings: list[ReviewFinding] = []
    if _is_secret(text):
        findings.append(
            ReviewFinding(
                "error",
                location,
                "hard-coded secret in the change",
                "Read the secret from the environment this project already uses.",
            )
        )
    if _is_shell_injection(text):
        findings.append(
            ReviewFinding(
                "error",
                location,
                "shell command is built from an interpolated string",
                "Pass the command as an argument list, without a shell.",
            )
        )
    if not allow_todo and _TODO.search(text):
        findings.append(
            ReviewFinding(
                "error",
                location,
                "unfinished marker left in the change",
                "Finish the work in this run instead of leaving a marker.",
            )
        )
    return findings


def _is_secret(text: str) -> bool:
    if _PEM.search(text) or _LIVE_TOKEN.search(text):
        return not _PLACEHOLDER.search(text)
    match = _SECRET.search(text)
    if match is None:
        return False
    return _PLACEHOLDER.search(match.group(1)) is None and _PLACEHOLDER.search(text) is None


def _is_shell_injection(text: str) -> bool:
    if _OS_SHELL.search(text):
        return True
    if "shell=True" not in text or "subprocess." not in text:
        return False
    return "f'" in text or 'f"' in text or ".format(" in text


def _missing_test_findings(diff: str) -> list[ReviewFinding]:
    modules = _new_src_modules(diff)
    if not modules or _diff_touches_tests(diff):
        return []
    listed = ", ".join(modules[:3])
    return [
        ReviewFinding(
            "warning",
            listed,
            "new module has no test in this change",
            "Add a test that fails without the new behavior and passes with it.",
        )
    ]


def _new_src_modules(diff: str) -> list[str]:
    modules: list[str] = []
    pending_new = False
    saw_hunk = False
    current = ""
    for raw in (diff or "").splitlines():
        if raw.startswith("diff --git "):
            pending_new = False
            saw_hunk = False
            current = ""
            continue
        if raw.startswith("new file mode"):
            pending_new = True
            continue
        if raw.startswith("+++ b/"):
            current = raw[6:].strip()
            saw_hunk = False
            if pending_new and _is_src_module(current):
                modules.append(current)
            pending_new = False
            continue
        if raw.startswith("@@"):
            saw_hunk = True
            continue
        if current and not saw_hunk and _is_src_module(current) and current not in modules:
            modules.append(current)
            saw_hunk = True
    return modules


def _is_src_module(path: str) -> bool:
    posix = path.replace("\\", "/")
    return posix.startswith("src/") and posix.endswith(".py")


def _diff_touches_tests(diff: str) -> bool:
    for path in _PLUS_PATH.findall(diff or ""):
        posix = path.replace("\\", "/")
        name = posix.rsplit("/", 1)[-1]
        if posix.startswith("tests/") or "/tests/" in f"/{posix}" or name.startswith("test_"):
            return True
    return False


def _skip_path(path: str) -> bool:
    posix = path.replace("\\", "/")
    name = posix.rsplit("/", 1)[-1]
    if not posix:
        return False
    if posix.startswith("tests/") or "/tests/" in f"/{posix}":
        return True
    if name.startswith("test_"):
        return True
    if posix.startswith(".loco/") or "/.loco/" in f"/{posix}":
        return True
    return False


def _added_lines(diff: str) -> list[tuple[str, int, str]]:
    path = ""
    line_no = 1
    in_hunk = False
    raw_body = False
    rows: list[tuple[str, int, str]] = []
    for raw in (diff or "").splitlines():
        if raw.startswith("diff --git "):
            path = ""
            in_hunk = False
            raw_body = False
            continue
        if raw.startswith("+++ b/"):
            path = raw[6:].strip()
            line_no = 1
            in_hunk = False
            raw_body = False
            continue
        if raw.startswith("@@"):
            match = _HUNK_START.search(raw)
            line_no = int(match.group(1)) if match else 1
            in_hunk = True
            raw_body = False
            continue
        if raw.startswith(("--- ", "index ", "new file ", "deleted file ", "\\")):
            continue
        if in_hunk:
            if raw.startswith("+") and not raw.startswith("+++"):
                rows.append((path, line_no, raw[1:]))
                line_no += 1
            elif raw.startswith("-"):
                continue
            else:
                line_no += 1
            continue
        if path and raw.strip():
            raw_body = True
        if raw_body and path:
            rows.append((path, line_no, raw))
            line_no += 1
    return rows
