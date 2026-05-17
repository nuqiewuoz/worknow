# worknow

[![Ko-fi](https://img.shields.io/badge/Support-Ko--fi-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/nvwalj)

A small local CLI that builds an automatically maintained view of active coding work across your machines.

> **See also:** [AI Memory Reader](https://github.com/nvwalj/ai-memory-reader) — native macOS & iOS app for browsing the `~/.openclaw/workspace/current-work.md` file this tool writes (plus Claude Code, Codex, Cursor, Gemini memory directories).

It scans:

- configured project directories (direct children only — fast & predictable)
- per-repo: git branch, dirty state, last commit, recent commits within a window
- running `claude`, `codex`, `gemini`, `openclaw`, `xcodebuild`, `gradle`, `npm`/`pnpm`/`yarn`, plus anything you add
- optional `openclaw sessions list` / `tasks list` output when the binary is available

Output is two sidecar files at `~/.openclaw/workspace/`:

- `current-work.md` — readable Markdown view, friendly for cross-machine browsing
- `current-work.json` — structured payload consumed by the native macOS menu bar app in `mac/`

Requires Python ≥ 3.11.

## Install

```bash
git clone https://github.com/nuqiewuoz/worknow.git
cd worknow
python3 -m pip install -e .
```

Then run:

```bash
worknow              # one-shot
worknow --watch 300  # refresh every 5 minutes (minimum 10s)
worknow --version
```

If editable install is inconvenient on a machine, the self-contained wrapper works without `pip install`:

```bash
./bin/worknow
ln -sf "$PWD/bin/worknow" ~/.local/bin/worknow
```

On macOS, install automatic refresh every 5 minutes via `launchd`:

```bash
./bin/install-launchd-macos
```

Uninstall it with:

```bash
./bin/uninstall-launchd-macos
```

## Config

Config lives at `~/.config/worknow/config.toml`. Run `worknow --config-init` to seed it with defaults.

Example:

```toml
output = "~/.openclaw/workspace/current-work.md"

project_roots = [
  "~/projects",
  "~/Project",
  "~/.openclaw/workspace",
]

process_keywords = [
  "claude", "codex", "gemini", "openclaw",
  "xcodebuild", "gradle", "npm", "pnpm", "yarn",
]

ignored_process_fragments = [
  "Google Chrome Helper",
  "chrome_crashpad_handler",
]

max_projects = 80
recent_commit_days = 7
```

If no config exists, `worknow` uses the defaults baked into `cli.py`.

## Native macOS menu bar app

A small AppKit menu bar app lives in `mac/`. It reads the JSON sidecar the CLI writes and surfaces:

- a menu bar badge with the **active task count** — a "task" is a tracked git repo that is dirty OR has a coding agent process running inside it
- a draggable floating panel (click the menu bar icon to toggle) listing active repos and agent processes
- auto-refresh every 30 seconds; panel position persists across launches

Build & run:

```bash
cd mac
./build.sh             # compiles a single-file Swift binary (no Xcode project)
./worknow-mac          # run once, look in the menu bar
```

Auto-start at login:

```bash
./install-launchd-macos
./uninstall-launchd-macos   # to remove
```

The app only displays data — it does not run the scanner itself. Schedule the Python CLI (e.g. via `bin/install-launchd-macos`) so the JSON stays fresh.

## Git sync across machines

This repo is self-contained — push your fork wherever, clone on each dev machine, and run `pip install -e .`. Machine-specific paths stay in `~/.config/worknow/config.toml` and never touch git.

## Development

```bash
python3 -m pip install -e '.[dev]'
pytest
```

## License

MIT — see [LICENSE](LICENSE).
