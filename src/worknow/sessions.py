"""Discover active coding-agent sessions (Claude + Codex) on the local machine.

Both Claude and Codex persist per-session JSONL transcripts on disk:

- Claude: ``~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl``
- Codex:  ``~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl``

We use those files (not just running processes) as the authoritative
"what is this agent doing" signal — a session may already have finished
its turn but the user might still care about the last 5 minutes of work.

Classification (per `RECENT_SESSION_WINDOW`):

- ``active`` — file mtime < window AND a matching CLI process is running
- ``done``   — file mtime < window AND no matching process
- ``idle``   — file mtime older than the window (omitted from output)

The "active task count" surfaced to the UI is `active + done` — both are
things the user might still want a glance at.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import pathlib
import platform
import re
import subprocess
import time
from typing import Iterable


# How recent the session log must be to be considered active. Anything
# older falls into "idle" and is suppressed.
RECENT_SESSION_WINDOW = 5 * 60  # seconds

# How many trailing lines of a jsonl to inspect when extracting the
# "last user message" / "last assistant summary". Beyond ~40 events the
# context-window cost of reading the whole file outweighs the freshness
# benefit, and the trailing slice typically captures the relevant turn.
TAIL_LINES = 60

# Truncation lengths for messages shown in the UI.
USER_MSG_MAX_CHARS = 160
ASSISTANT_MSG_MAX_CHARS = 160


@dataclasses.dataclass
class Session:
    agent: str  # "claude" | "codex"
    session_id: str
    cwd: str | None
    last_activity_iso: str
    last_user_message: str | None
    last_assistant_summary: str | None
    host: str  # "iTerm" | "Terminal" | "VSCode" | "Claude Desktop" | "Background" | "Unknown"
    status: str  # "active" | "done"
    pid: str | None


# --------------------------------------------------------------------------- #
# Claude discovery
# --------------------------------------------------------------------------- #

def _claude_root() -> pathlib.Path:
    return pathlib.Path(os.path.expanduser("~/.claude/projects"))


def _decode_claude_cwd(dir_name: str) -> str:
    """Reverse Claude's `cwd → dir name` encoding.

    Claude replaces ``/`` with ``-`` in the cwd path to derive the project
    directory name. This is lossy if the original path has dashes, so this
    function is best-effort — we return whatever the encoding suggests and
    let downstream code cope.
    """
    if not dir_name.startswith("-"):
        return dir_name
    return "/" + dir_name[1:].replace("-", "/")


def _scan_claude_sessions(now: float) -> list[Session]:
    root = _claude_root()
    if not root.exists():
        return []
    out: list[Session] = []
    try:
        project_dirs = list(root.iterdir())
    except OSError:
        return []
    for project_dir in project_dirs:
        if not project_dir.is_dir():
            continue
        try:
            jsonl_files = [p for p in project_dir.iterdir() if p.suffix == ".jsonl"]
        except OSError:
            continue
        for jsonl_path in jsonl_files:
            try:
                mtime = jsonl_path.stat().st_mtime
            except OSError:
                continue
            if now - mtime > RECENT_SESSION_WINDOW:
                continue
            session = _parse_jsonl_session(jsonl_path, agent="claude", fallback_cwd=_decode_claude_cwd(project_dir.name))
            if session:
                out.append(session)
    return out


# --------------------------------------------------------------------------- #
# Codex discovery
# --------------------------------------------------------------------------- #

def _codex_root() -> pathlib.Path:
    return pathlib.Path(os.path.expanduser("~/.codex/sessions"))


def _scan_codex_sessions(now: float) -> list[Session]:
    """Codex partitions sessions into ``YYYY/MM/DD/`` subfolders. We only
    walk today and yesterday — anything older can't be within the recent
    window anyway.
    """
    root = _codex_root()
    if not root.exists():
        return []
    today = dt.date.today()
    yesterday = today - dt.timedelta(days=1)
    candidates: list[pathlib.Path] = []
    for date in (today, yesterday):
        day_dir = root / f"{date.year:04d}" / f"{date.month:02d}" / f"{date.day:02d}"
        if day_dir.exists():
            try:
                candidates.extend(p for p in day_dir.iterdir() if p.suffix == ".jsonl")
            except OSError:
                continue
    out: list[Session] = []
    for jsonl_path in candidates:
        try:
            mtime = jsonl_path.stat().st_mtime
        except OSError:
            continue
        if now - mtime > RECENT_SESSION_WINDOW:
            continue
        session = _parse_jsonl_session(jsonl_path, agent="codex", fallback_cwd=None)
        if session:
            out.append(session)
    return out


# --------------------------------------------------------------------------- #
# JSONL parsing
# --------------------------------------------------------------------------- #

def _read_tail(path: pathlib.Path, max_lines: int = TAIL_LINES) -> list[str]:
    """Read up to the last ``max_lines`` lines without slurping the whole file
    when it's large. We seek to the end and walk back, since JSONL transcripts
    can grow into the tens of MB.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size == 0:
        return []
    chunk_size = 32 * 1024
    data = b""
    with path.open("rb") as f:
        pos = size
        while pos > 0 and data.count(b"\n") <= max_lines:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            data = f.read(read_size) + data
    return data.decode("utf-8", errors="replace").splitlines()[-max_lines:]


def _parse_jsonl_session(path: pathlib.Path, agent: str, fallback_cwd: str | None) -> Session | None:
    """Pull out the bits we care about from a JSONL transcript.

    Both agents emit one JSON object per line; field naming differs
    between Claude and Codex but the relevant pieces (cwd, user text,
    assistant text, timestamp) overlap enough that one passthrough works.
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    lines = _read_tail(path)
    if not lines:
        return None

    cwd: str | None = None
    last_user: str | None = None
    last_assistant: str | None = None
    session_id = path.stem
    if agent == "codex" and session_id.startswith("rollout-"):
        # rollout-2026-03-21T01-13-03-019d0df3-eeb8-... → keep the UUID tail.
        parts = session_id.split("-")
        if len(parts) >= 6:
            session_id = "-".join(parts[-5:])

    for raw in lines:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        cwd = cwd or _extract_cwd(event)
        user_text = _extract_user_text(event, agent)
        if user_text:
            last_user = user_text
        assistant_text = _extract_assistant_text(event, agent)
        if assistant_text:
            last_assistant = assistant_text

    cwd = cwd or fallback_cwd

    last_activity_iso = dt.datetime.fromtimestamp(mtime).astimezone().isoformat()
    return Session(
        agent=agent,
        session_id=session_id,
        cwd=cwd,
        last_activity_iso=last_activity_iso,
        last_user_message=_truncate(last_user, USER_MSG_MAX_CHARS),
        last_assistant_summary=_truncate(last_assistant, ASSISTANT_MSG_MAX_CHARS),
        host="Unknown",  # filled in later by process correlation
        status="done",   # adjusted later if a matching process is found
        pid=None,
    )


def _extract_cwd(event: dict) -> str | None:
    if "cwd" in event and isinstance(event["cwd"], str):
        return event["cwd"]
    payload = event.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("cwd"), str):
        return payload["cwd"]
    return None


def _extract_user_text(event: dict, agent: str) -> str | None:
    if agent == "claude":
        if event.get("type") == "user":
            msg = event.get("message")
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str):
                    return _clean_text(content)
                if isinstance(content, list):
                    parts = [p.get("text") for p in content if isinstance(p, dict) and isinstance(p.get("text"), str)]
                    if parts:
                        return _clean_text(" ".join(parts))
    # Codex layout
    if agent == "codex":
        if event.get("type") == "response_item":
            payload = event.get("payload", {})
            if payload.get("type") == "message" and payload.get("role") == "user":
                content = payload.get("content", [])
                if isinstance(content, list):
                    parts = [c.get("text") for c in content if isinstance(c, dict) and isinstance(c.get("text"), str)]
                    if parts:
                        return _clean_text(" ".join(parts))
    return None


def _extract_assistant_text(event: dict, agent: str) -> str | None:
    if agent == "claude":
        if event.get("type") == "assistant":
            msg = event.get("message", {})
            content = msg.get("content")
            if isinstance(content, list):
                parts = [p.get("text") for p in content if isinstance(p, dict) and isinstance(p.get("text"), str)]
                if parts:
                    return _clean_text(" ".join(parts))
    if agent == "codex":
        if event.get("type") == "response_item":
            payload = event.get("payload", {})
            if payload.get("type") == "message" and payload.get("role") == "assistant":
                content = payload.get("content", [])
                if isinstance(content, list):
                    parts = [c.get("text") for c in content if isinstance(c, dict) and isinstance(c.get("text"), str)]
                    if parts:
                        return _clean_text(" ".join(parts))
    return None


_WHITESPACE_RE = re.compile(r"\s+")
_CHANNEL_TAG_RE = re.compile(r"<channel[^>]*>(.*?)</channel>", re.DOTALL)


def _clean_text(text: str) -> str:
    """Strip the most common noise: Discord <channel> wrappers and excess
    whitespace. Leaves the body that a human would recognize as the message.
    """
    match = _CHANNEL_TAG_RE.search(text)
    if match:
        text = match.group(1)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _truncate(text: str | None, max_chars: int) -> str | None:
    if text is None:
        return None
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


# --------------------------------------------------------------------------- #
# Process / host correlation
# --------------------------------------------------------------------------- #

_HOST_PATTERNS = [
    ("iTerm", ["iTerm.app", "iTerm2.app", "/iTerm"]),
    ("Terminal", ["Terminal.app", "/Terminal"]),
    ("VSCode", ["Code Helper", "Visual Studio Code.app"]),
    ("Claude Desktop", ["Claude.app/Contents/MacOS/Claude"]),
    ("OpenClaw", ["openclaw"]),
]


def _ps_lines() -> list[tuple[str, str, str]]:
    """Return ``(pid, ppid, command)`` rows. macOS-only currently."""
    if platform.system() != "Darwin":
        return []
    try:
        out = subprocess.check_output(["ps", "axo", "pid=,ppid=,command="], text=True, timeout=8)
    except Exception:
        return []
    rows: list[tuple[str, str, str]] = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        rows.append((parts[0], parts[1], parts[2]))
    return rows


def _walk_host(pid: str, ps_index: dict[str, tuple[str, str]]) -> str:
    """Walk parent process chain looking for a recognizable host app."""
    current = pid
    for _ in range(15):
        node = ps_index.get(current)
        if not node:
            return "Background"
        ppid, command = node
        for label, patterns in _HOST_PATTERNS:
            if any(p in command for p in patterns):
                return label
        if ppid in ("1", "0") or ppid == current:
            return "Background"
        current = ppid
    return "Unknown"


def _correlate_processes(sessions: list[Session]) -> None:
    """For each session, find the running CLI process (if any) and set
    ``status``, ``pid``, and ``host`` accordingly. Matching is heuristic:
    a claude/codex process whose cwd equals the session's cwd is the strong
    signal. We fall back to "process exists but unmatched" only when there's
    a single ambiguous candidate.
    """
    rows = _ps_lines()
    if not rows:
        return
    ps_index: dict[str, tuple[str, str]] = {pid: (ppid, command) for pid, ppid, command in rows}

    # Group running claude/codex processes by their cwd. We get cwd via
    # `lsof` in a single batched call rather than per-pid.
    candidates: dict[str, list[str]] = {"claude": [], "codex": []}
    for pid, _ppid, command in rows:
        lc = command.lower()
        # Match the bare CLI; deliberately skip plugin daemons and helpers
        # whose command lines contain `claude` only as a path fragment.
        if "/.claude/plugins/" in command or "Claude.app/" in command or "ShipIt" in command:
            continue
        if "chrome-devtools-mcp" in command or "chrome-native-host" in command:
            continue
        if re.search(r"(^|/)claude(\s|$)", command) or re.search(r"(^|/)claude(\s+--|\s+\w)", command):
            candidates["claude"].append(pid)
        elif "/codex " in command or command.startswith("codex ") or " codex " in command:
            candidates["codex"].append(pid)

    cwd_by_pid = _batch_pid_cwd(candidates["claude"] + candidates["codex"])

    for session in sessions:
        target_pids = candidates.get(session.agent, [])
        matched_pid: str | None = None
        for pid in target_pids:
            if cwd_by_pid.get(pid) and session.cwd and cwd_by_pid[pid] == session.cwd:
                matched_pid = pid
                break
        if matched_pid is None and len(target_pids) == 1 and session.cwd is None:
            matched_pid = target_pids[0]
        if matched_pid:
            session.pid = matched_pid
            session.status = "active"
            session.host = _walk_host(matched_pid, ps_index)
        else:
            session.status = "done"


def _batch_pid_cwd(pids: Iterable[str]) -> dict[str, str]:
    pid_list = [p for p in pids]
    if not pid_list:
        return {}
    try:
        out = subprocess.check_output(
            ["lsof", "-a", "-p", ",".join(pid_list), "-d", "cwd", "-Fpn"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=6,
        )
    except Exception:
        return {}
    result: dict[str, str] = {}
    current_pid: str | None = None
    for line in out.splitlines():
        if not line:
            continue
        if line.startswith("p"):
            current_pid = line[1:]
        elif line.startswith("n") and current_pid:
            result[current_pid] = line[1:]
    return result


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def discover() -> list[Session]:
    """Return all currently-relevant (``active`` or ``done``) agent sessions.

    Sessions are sorted with active first, then by recency.
    """
    now = time.time()
    sessions = _scan_claude_sessions(now) + _scan_codex_sessions(now)
    _correlate_processes(sessions)
    sessions.sort(key=lambda s: (s.status != "active", s.last_activity_iso), reverse=False)
    sessions.sort(key=lambda s: s.last_activity_iso, reverse=True)
    sessions.sort(key=lambda s: s.status != "active")
    return sessions
