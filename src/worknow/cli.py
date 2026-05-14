from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
import json
import os
import pathlib
import platform
import subprocess
import sys
import time
import tomllib
from typing import Iterable

from worknow import __version__
from worknow import sessions as sessions_mod


DEFAULT_CONFIG: dict = {
    "output": "~/.openclaw/workspace/current-work.md",
    "project_roots": [
        "~/Project",
        "~/projects",
        "~/.openclaw/workspace",
    ],
    "process_keywords": [
        "claude",
        "codex",
        "gemini",
        "openclaw",
        "xcodebuild",
        "gradle",
        "npm",
        "pnpm",
        "yarn",
    ],
    "max_projects": 80,
    "recent_commit_days": 7,
    "ignored_process_fragments": [
        "Google Chrome Helper",
        "chrome_crashpad_handler",
        "/Applications/Claude.app/Contents/Frameworks/Claude Helper",
        "/Applications/Claude.app/Contents/MacOS/Claude",
        "/Applications/Claude.app/Contents/Frameworks/Squirrel.framework",
    ],
}

RECENT_COMMITS_LIMIT = 5
GIT_WORKERS = 8


@dataclasses.dataclass
class GitProject:
    path: pathlib.Path
    branch: str
    dirty: bool
    changes: str
    last_commit: str
    recent_commits: list[str]
    has_active_agent: bool = False

    @property
    def is_active(self) -> bool:
        return self.dirty or self.has_active_agent


@dataclasses.dataclass
class ProcessInfo:
    pid: str
    command: str
    cwd: str | None = None


def expand(path: str) -> pathlib.Path:
    return pathlib.Path(os.path.expandvars(os.path.expanduser(path))).resolve()


def run(cmd: list[str], cwd: pathlib.Path | None = None, timeout: int = 5) -> str:
    try:
        return subprocess.check_output(
            cmd,
            cwd=str(cwd) if cwd else None,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        ).strip()
    except Exception:
        return ""


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    cfg_path = expand("~/.config/worknow/config.toml")
    if cfg_path.exists():
        with cfg_path.open("rb") as f:
            config.update(tomllib.load(f))
    return config


def dict_to_toml(data: dict) -> str:
    """Render the small subset of types used by DEFAULT_CONFIG to TOML.

    Supports str, int, and list[str]. Anything else would surface a TypeError —
    intentional, since this is only meant to seed the user's config file.
    """
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, str):
            lines.append(f'{key} = "{value}"')
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")
        elif isinstance(value, list):
            items = ",\n  ".join(f'"{item}"' for item in value)
            lines.append(f"{key} = [\n  {items},\n]")
        else:
            raise TypeError(f"Unsupported config value type: {type(value).__name__}")
    return "\n".join(lines) + "\n"


def find_git_repos(roots: Iterable[str], max_projects: int) -> list[pathlib.Path]:
    repos: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()
    for root_s in roots:
        root = expand(root_s)
        if not root.exists():
            continue
        # Prefer direct children to keep this fast and predictable.
        candidates = [root]
        try:
            candidates.extend(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            pass
        for candidate in candidates:
            if (candidate / ".git").exists():
                resolved = candidate.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    repos.append(resolved)
            if len(repos) >= max_projects:
                return repos
    return repos


def inspect_git_repo(path: pathlib.Path, recent_days: int) -> GitProject | None:
    branch = run(["git", "branch", "--show-current"], cwd=path)
    if not branch:
        branch = run(["git", "rev-parse", "--short", "HEAD"], cwd=path) or "unknown"
    status = run(["git", "status", "--porcelain"], cwd=path)
    changes = summarize_status(status)
    last_commit = run(["git", "log", "-1", "--pretty=%cr · %s"], cwd=path) or "no commits"
    recent = run(
        ["git", "log", f"--since={recent_days}.days", "--pretty=%cr · %s", f"-{RECENT_COMMITS_LIMIT}"],
        cwd=path,
    )
    recent_commits = [line for line in recent.splitlines() if line]
    # Show active repos first: dirty, non-main branch, or recent commit.
    if not status and branch in {"main", "master", "develop"} and not recent_commits:
        return None
    return GitProject(
        path=path,
        branch=branch,
        dirty=bool(status),
        changes=changes,
        last_commit=last_commit,
        recent_commits=recent_commits,
    )


def inspect_git_repos(paths: list[pathlib.Path], recent_days: int) -> list[GitProject]:
    if not paths:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=GIT_WORKERS) as pool:
        results = list(pool.map(lambda p: inspect_git_repo(p, recent_days), paths))
    return [r for r in results if r is not None]


def summarize_status(status: str) -> str:
    if not status:
        return "clean"
    counts: dict[str, int] = {}
    for line in status.splitlines():
        key = line[:2].strip() or "??"
        counts[key] = counts.get(key, 0) + 1
    return ", ".join(f"{k}:{v}" for k, v in sorted(counts.items()))


def list_processes(keywords: list[str], ignored_fragments: list[str] | None = None) -> list[ProcessInfo]:
    if platform.system() == "Darwin":
        raw = run(["ps", "axo", "pid=,command="], timeout=10)
    else:
        raw = run(["ps", "-eo", "pid=,command="], timeout=10)
    own_pid = str(os.getpid())
    ignored_fragments = ignored_fragments or []
    lowered_keywords = [k.lower() for k in keywords]
    matched: list[ProcessInfo] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, command = line.partition(" ")
        if pid == own_pid:
            continue
        lc = command.lower()
        if any(fragment in command for fragment in ignored_fragments):
            continue
        if not any(k in lc for k in lowered_keywords):
            continue
        if "worknow" in lc and "python" in lc:
            continue
        matched.append(ProcessInfo(pid=pid, command=command[:220]))
    if matched:
        cwds = batch_process_cwd([p.pid for p in matched])
        for proc in matched:
            proc.cwd = cwds.get(proc.pid)
    return matched


def batch_process_cwd(pids: list[str]) -> dict[str, str]:
    """Resolve cwd for many pids in one syscall round.

    On macOS uses a single `lsof -a -p pid1,pid2,... -d cwd -Fpn`. The `-F`
    output is a stream of `p<pid>` / `n<path>` markers — we walk it
    statefully, mapping the latest pid we saw to the next n-line.
    On Linux reads `/proc/<pid>/cwd` symlinks (cheap, no subprocess).
    """
    if not pids:
        return {}
    if platform.system() == "Darwin":
        # lsof's pid list is comma-separated; `-a` ANDs the pid filter with
        # `-d cwd` so we only get the working-directory descriptor.
        out = run(["lsof", "-a", "-p", ",".join(pids), "-d", "cwd", "-Fpn"], timeout=6)
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
    # Linux
    result = {}
    for pid in pids:
        cwd_link = pathlib.Path(f"/proc/{pid}/cwd")
        try:
            if cwd_link.exists():
                result[pid] = str(cwd_link.resolve())
        except Exception:
            continue
    return result


def openclaw_sessions() -> str:
    # Optional integration. If CLI output changes or command is unavailable, fail quiet.
    for cmd in (["openclaw", "sessions", "list"], ["openclaw", "tasks", "list"]):
        out = run(list(cmd), timeout=5)
        if out:
            return out
    return ""


def escape_md_inline(text: str) -> str:
    """Escape backticks so commit messages don't break inline code spans."""
    return text.replace("`", "\\`")


def attach_agent_presence(projects: list[GitProject], processes: list[ProcessInfo]) -> None:
    """Mark a project as having an active agent if any process's cwd is inside
    the project path. Mutates projects in place.
    """
    agent_cwds = [p.cwd for p in processes if p.cwd]
    for project in projects:
        project_str = str(project.path)
        project.has_active_agent = any(
            cwd == project_str or cwd.startswith(project_str + os.sep)
            for cwd in agent_cwds
        )


def render_json(
    projects: list[GitProject],
    processes: list[ProcessInfo],
    sessions_text: str,
    sessions: list[sessions_mod.Session] | None = None,
) -> str:
    now = dt.datetime.now().astimezone().isoformat()
    sessions = sessions or []
    active_sessions_count = len(sessions)
    payload = {
        # Bumped to 2 with the sessions[] addition; UI clients should
        # tolerate either by feature-detecting the `sessions` key.
        "schema_version": 2,
        "generated_at": now,
        "host": platform.node(),
        # `active_tasks_count` now reflects agent sessions, not dirty repos.
        # The old repo-only count remains available as `dirty_repo_count`.
        "active_tasks_count": active_sessions_count,
        "active_sessions_count": active_sessions_count,
        "dirty_repo_count": sum(1 for p in projects if p.is_active),
        "sessions": [
            {
                "agent": s.agent,
                "session_id": s.session_id,
                "cwd": s.cwd,
                "last_activity": s.last_activity_iso,
                "last_user_message": s.last_user_message,
                "last_assistant_summary": s.last_assistant_summary,
                "host": s.host,
                "status": s.status,
                "pid": s.pid,
            }
            for s in sessions
        ],
        "repos": [
            {
                "name": p.path.name,
                "path": str(p.path),
                "branch": p.branch,
                "dirty": p.dirty,
                "changes": p.changes,
                "last_commit": p.last_commit,
                "recent_commits": p.recent_commits,
                "has_active_agent": p.has_active_agent,
                "is_active": p.is_active,
            }
            for p in sorted(projects, key=lambda x: (not x.is_active, not x.dirty, x.path.name.lower()))
        ],
        "processes": [
            {"pid": proc.pid, "command": proc.command, "cwd": proc.cwd}
            for proc in processes[:40]
        ],
        "sessions_text": sessions_text[:6000] if sessions_text else "",
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def render(projects: list[GitProject], processes: list[ProcessInfo], sessions_text: str, config: dict) -> str:
    now = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    host = platform.node()
    lines: list[str] = []
    lines.append("# Current Work")
    lines.append("")
    lines.append(f"Generated: `{now}` on `{host}`")
    lines.append("")
    lines.append("## Active Git Projects")
    lines.append("")
    if not projects:
        lines.append("No active git projects found from configured roots.")
    for p in sorted(projects, key=lambda x: (not x.dirty, x.path.name.lower())):
        lines.append(f"### {p.path.name}")
        lines.append(f"- Path: `{p.path}`")
        lines.append(f"- Branch: `{p.branch}`")
        lines.append(f"- State: `{p.changes}`")
        lines.append(f"- Last commit: {escape_md_inline(p.last_commit)}")
        if p.recent_commits:
            lines.append("- Recent:")
            for c in p.recent_commits:
                lines.append(f"  - {escape_md_inline(c)}")
        lines.append("")
    lines.append("## Agent / Build Processes")
    lines.append("")
    if not processes:
        lines.append("No matching agent/build processes found.")
    for proc in processes[:40]:
        cwd = f" · cwd: `{proc.cwd}`" if proc.cwd else ""
        lines.append(f"- `{proc.pid}`{cwd} — `{escape_md_inline(proc.command)}`")
    lines.append("")
    if sessions_text:
        lines.append("## OpenClaw Sessions / Tasks")
        lines.append("")
        lines.append("```text")
        lines.append(sessions_text[:6000])
        lines.append("```")
        lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(
        "This file is generated by `worknow`; do not hand-maintain it. "
        "Tune machine-specific roots in `~/.config/worknow/config.toml`."
    )
    lines.append("")
    return "\n".join(lines)


def write_default_config_if_missing() -> pathlib.Path:
    cfg_path = expand("~/.config/worknow/config.toml")
    if cfg_path.exists():
        return cfg_path
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    header = "# Machine-local worknow config. Safe to edit; not stored in the repo.\n"
    cfg_path.write_text(header + dict_to_toml(DEFAULT_CONFIG))
    return cfg_path


def generate(config: dict) -> pathlib.Path:
    repos = find_git_repos(config["project_roots"], int(config.get("max_projects", 80)))
    projects = inspect_git_repos(repos, int(config.get("recent_commit_days", 7)))
    processes = list_processes(
        list(config.get("process_keywords", [])),
        list(config.get("ignored_process_fragments", [])),
    )
    attach_agent_presence(projects, processes)
    sessions_text = openclaw_sessions()
    discovered_sessions = sessions_mod.discover()
    output = expand(str(config["output"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(projects, processes, sessions_text, config))
    # Sidecar JSON for native UIs (e.g. mac/ menu bar app).
    json_output = output.with_suffix(".json")
    json_output.write_text(render_json(projects, processes, sessions_text, discovered_sessions))
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate an automatic current-work tracker.")
    parser.add_argument("--config-init", action="store_true", help="write default config if missing")
    parser.add_argument("--output", help="override output markdown path")
    parser.add_argument("--watch", type=int, metavar="SECONDS", help="refresh forever every N seconds")
    parser.add_argument("--version", action="version", version=f"worknow {__version__}")
    args = parser.parse_args(argv)

    if args.config_init:
        write_default_config_if_missing()
    config = load_config()
    if args.output:
        config["output"] = args.output

    try:
        while True:
            output = generate(config)
            print(f"Updated {output}")
            if not args.watch:
                break
            time.sleep(max(args.watch, 10))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
