# worknow

A small local CLI that builds an automatically maintained view of active coding work across your machines.

It scans:

- configured project directories (direct children only — fast & predictable)
- per-repo: git branch, dirty state, last commit, recent commits within a window
- running `claude`, `codex`, `gemini`, `openclaw`, `xcodebuild`, `gradle`, `npm`/`pnpm`/`yarn`, plus anything you add
- optional `openclaw sessions list` / `tasks list` output when the binary is available

Output is a single readable Markdown file, defaulting to `~/.openclaw/workspace/current-work.md`.

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

## Git sync across machines

This repo is self-contained — push your fork wherever, clone on each dev machine, and run `pip install -e .`. Machine-specific paths stay in `~/.config/worknow/config.toml` and never touch git.

## Development

```bash
python3 -m pip install -e '.[dev]'
pytest
```

## License

MIT — see [LICENSE](LICENSE).
