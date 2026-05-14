# worknow

`worknow` is a small local CLI that builds an automatically maintained view of active coding work across machines.

It scans:

- configured project directories
- git branch / dirty state / recent commits
- running `claude`, `codex`, `gemini`, `openclaw`, `xcodebuild`, `gradle`, and related processes
- optional OpenClaw session listing when available

Output is a readable Markdown file, defaulting to `~/.openclaw/workspace/current-work.md`.

## Install

```bash
cd worknow
python3 -m pip install -e .
```

Then run:

```bash
worknow
worknow --watch 300
```

If editable install is inconvenient on a machine, use the self-contained wrapper:

```bash
./bin/worknow
ln -sf "$PWD/bin/worknow" ~/.local/bin/worknow
```

On macOS, install automatic refresh every 5 minutes:

```bash
./bin/install-launchd-macos
```

Uninstall it with:

```bash
./bin/uninstall-launchd-macos
```

## Config

Config lives at `~/.config/worknow/config.toml`.

Example:

```toml
output = "~/.openclaw/workspace/current-work.md"

project_roots = [
  "~/Project",
  "/Volumes/MOVESPEED/Data/Project"
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
```

If no config exists, `worknow` uses sensible defaults for Qun's Mac mini setup.

## Git sync across machines

This repo is intentionally self-contained. Push it to a private GitHub repo, then clone it on each dev machine and run `pip install -e .`.

Machine-specific paths stay in `~/.config/worknow/config.toml`, not in git.
