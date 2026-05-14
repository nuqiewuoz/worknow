from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import os
import pathlib
import platform
import shlex
import subprocess
import sys
import time
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.9/3.10 fallback
    tomllib = None
from typing import Iterable


DEFAULT_CONFIG = {
    "output": "~/.openclaw/workspace/current-work.md",
    "project_roots": [
        "~/Project",
        "/Volumes/MOVESPEED/Data/Project",
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


@dataclasses.dataclass
class GitProject:
    path: pathlib.Path
    branch: str
    dirty: bool
    changes: str
    last_commit: str
    recent_commits: list[str]


@dataclasses.dataclass
class ProcessInfo:
    pid: str
    command: str
    cwd: str | None = None


def expand(path: str) -> pathlib.Path:
    return pathlib.Path(os.path.expandvars(os.path.expanduser(path))).resolve()


def run(cmd: list[str], cwd: pathlib.Path | None = None, timeout: int = 5) -> str:
    try:
        return subprocess.check_output(cmd, cwd=str(cwd) if cwd else None, text=True, stderr=subprocess.DEVNULL, timeout=timeout).strip()
    except Exception:
        return ""


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    cfg_path = expand("~/.config/worknow/config.toml")
    if cfg_path.exists():
        if tomllib:
            with cfg_path.open("rb") as f:
                user_cfg = tomllib.load(f)
        else:
            user_cfg = parse_simple_toml(cfg_path.read_text())
        config.update(user_cfg)
    return config


def parse_simple_toml(text: str) -> dict:
    """Tiny TOML subset parser for Python 3.9's system interpreter.

    Supports the config shape this project writes: string, int, and multi-line
    arrays of quoted strings. It is intentionally conservative.
    """
    result: dict = {}
    lines = iter(text.splitlines())
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        if value == "[":
            items: list[str] = []
            for arr_raw in lines:
                arr_line = arr_raw.strip()
                if arr_line.startswith("#") or not arr_line:
                    continue
                if arr_line == "]":
                    break
                items.extend(parse_array_items(arr_line))
            result[key] = items
        elif value.startswith("[") and value.endswith("]"):
            result[key] = parse_array_items(value[1:-1])
        elif value.startswith('"') and value.endswith('"'):
            result[key] = value[1:-1]
        else:
            try:
                result[key] = int(value)
            except ValueError:
                result[key] = value
    return result


def parse_array_items(text: str) -> list[str]:
    items: list[str] = []
    for part in text.rstrip(",").split(","):
        part = part.strip().rstrip(",")
        if part.startswith('"') and part.endswith('"'):
            items.append(part[1:-1])
    return items


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
            git_dir = candidate / ".git"
            if git_dir.exists():
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
    since = f"{recent_days}.days"
    recent = run(["git", "log", f"--since={since}", "--pretty=%cr · %s", "-5"], cwd=path)
    recent_commits = [line for line in recent.splitlines() if line]
    # Show active repos first: dirty, non-main branch, or recent commit.
    if not status and branch in {"main", "master", "develop"} and not recent_commits:
        return None
    return GitProject(path=path, branch=branch, dirty=bool(status), changes=changes, last_commit=last_commit, recent_commits=recent_commits)


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
    rows: list[ProcessInfo] = []
    lowered_keywords = [k.lower() for k in keywords]
    ignored_fragments = ignored_fragments or []
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
        if any(k in lc for k in lowered_keywords):
            if "worknow" in lc and "python" in lc:
                continue
            rows.append(ProcessInfo(pid=pid, command=command[:220], cwd=process_cwd(pid)))
    return rows


def process_cwd(pid: str) -> str | None:
    if platform.system() == "Darwin":
        # lsof is best-effort and may be slow/permission-limited.
        out = run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], timeout=2)
        for line in out.splitlines():
            if line.startswith("n"):
                return line[1:]
    elif pathlib.Path(f"/proc/{pid}/cwd").exists():
        try:
            return str(pathlib.Path(f"/proc/{pid}/cwd").resolve())
        except Exception:
            return None
    return None


def openclaw_sessions() -> str:
    # Optional integration. If CLI output changes or command is unavailable, fail quiet.
    for cmd in (["openclaw", "sessions", "list"], ["openclaw", "tasks", "list"]):
        out = run(list(cmd), timeout=5)
        if out:
            return out
    return ""


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
        rel = str(p.path)
        lines.append(f"### {p.path.name}")
        lines.append(f"- Path: `{rel}`")
        lines.append(f"- Branch: `{p.branch}`")
        lines.append(f"- State: `{p.changes}`")
        lines.append(f"- Last commit: {p.last_commit}")
        if p.recent_commits:
            lines.append("- Recent:")
            for c in p.recent_commits[:3]:
                lines.append(f"  - {c}")
        lines.append("")
    lines.append("## Agent / Build Processes")
    lines.append("")
    if not processes:
        lines.append("No matching agent/build processes found.")
    for proc in processes[:40]:
        cwd = f" · cwd: `{proc.cwd}`" if proc.cwd else ""
        lines.append(f"- `{proc.pid}`{cwd} — `{proc.command}`")
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
    lines.append("This file is generated by `worknow`; do not hand-maintain it. Tune machine-specific roots in `~/.config/worknow/config.toml`.")
    lines.append("")
    return "\n".join(lines)


def write_default_config_if_missing() -> pathlib.Path:
    cfg_path = expand("~/.config/worknow/config.toml")
    if cfg_path.exists():
        return cfg_path
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    content = """# Machine-local worknow config. Safe to edit; not stored in the repo.
output = "~/.openclaw/workspace/current-work.md"

project_roots = [
  "~/Project",
  "/Volumes/MOVESPEED/Data/Project",
  "~/.openclaw/workspace",
]

process_keywords = [
  "claude", "codex", "gemini", "openclaw", "xcodebuild", "gradle", "npm", "pnpm", "yarn"
]

ignored_process_fragments = [
  "Google Chrome Helper",
  "chrome_crashpad_handler",
  "/Applications/Claude.app/Contents/Frameworks/Claude Helper",
  "/Applications/Claude.app/Contents/MacOS/Claude",
  "/Applications/Claude.app/Contents/Frameworks/Squirrel.framework",
]

max_projects = 80
recent_commit_days = 7
"""
    cfg_path.write_text(content)
    return cfg_path


def generate(config: dict) -> pathlib.Path:
    repos = find_git_repos(config["project_roots"], int(config.get("max_projects", 80)))
    projects = [p for repo in repos if (p := inspect_git_repo(repo, int(config.get("recent_commit_days", 7))))]
    processes = list_processes(
        list(config.get("process_keywords", [])),
        list(config.get("ignored_process_fragments", [])),
    )
    sessions_text = openclaw_sessions()
    output = expand(str(config["output"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(projects, processes, sessions_text, config))
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate an automatic current-work tracker.")
    parser.add_argument("--config-init", action="store_true", help="write default config if missing")
    parser.add_argument("--output", help="override output markdown path")
    parser.add_argument("--watch", type=int, metavar="SECONDS", help="refresh forever every N seconds")
    args = parser.parse_args(argv)

    cfg_path = write_default_config_if_missing() if args.config_init else expand("~/.config/worknow/config.toml")
    config = load_config()
    if args.output:
        config["output"] = args.output

    while True:
        output = generate(config)
        print(f"Updated {output}")
        if not args.watch:
            break
        time.sleep(max(args.watch, 10))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
