#!/usr/bin/env python3
"""Install the DeepSeek cost statusline and session-start check for Claude Code.

Run from the repo checkout:

    python3 install.py            # install (or update) into ~/.claude
    python3 install.py --dry-run  # show what would change, touch nothing

It copies deepseek-cost.py and statusline.py into ~/.claude/ and merges two
entries into ~/.claude/settings.json:

  - statusLine           the command Claude Code runs to draw the status line
  - hooks.SessionStart   the one-line cost check at the start of a chat

Safety rules: settings.json is backed up before any change, an existing
different statusLine is never overwritten, the hook is added at most once,
and nothing else in the file is touched. Safe to re-run - that is also how
you update the scripts after a `git pull`.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS = ("deepseek-cost.py", "statusline.py")


def interpreter():
    """The name to write into settings.json: python3 wherever it exists."""
    names = ("python", "python3") if os.name == "nt" else ("python3", "python")
    for name in names:
        if shutil.which(name):
            return name
    return names[0]


def load_settings(path):
    """settings.json as a dict; aborts when it exists but is unreadable."""
    if not path.exists():
        return {}
    try:
        with open(path) as handle:
            settings = json.load(handle)
    except ValueError as exc:
        print(f"err: {path} is not valid JSON ({exc}); fix or move it, then re-run")
        raise SystemExit(1)
    if not isinstance(settings, dict):
        print(f"err: {path} does not hold a JSON object; fix or move it, then re-run")
        raise SystemExit(1)
    return settings


def status_block(py):
    return {"type": "command",
            "command": f'{py} "$HOME/.claude/statusline.py"',
            "padding": 0}


def hook_entry(py):
    return {"matcher": "startup|resume|clear",
            "hooks": [{"type": "command",
                       "command": f'{py} "$HOME/.claude/deepseek-cost.py" --welcome',
                       "timeout": 30}]}


def plan(settings, py):
    """Merge our blocks into settings; return the list of changes made."""
    changes = []
    ours = status_block(py)
    existing = settings.get("statusLine")
    if existing is None:
        settings["statusLine"] = ours
        changes.append("statusLine added")
    elif existing == ours:
        print("ok: statusLine already configured")
    else:
        print("warn: settings.json already has a different statusLine - left untouched;")
        print(f"      to switch, replace its command with: {ours['command']}")

    hooks = settings.get("hooks")
    if hooks is None:
        hooks = settings["hooks"] = {}
    if not isinstance(hooks, dict):
        print("warn: settings.json 'hooks' has an unexpected shape - hook not added")
        return changes
    sessions = hooks.get("SessionStart")
    if sessions is None:
        sessions = hooks["SessionStart"] = []
    if not isinstance(sessions, list):
        print("warn: SessionStart has an unexpected shape - hook not added")
    elif any("deepseek-cost.py" in json.dumps(entry) for entry in sessions):
        print("ok: SessionStart hook already present")
    else:
        sessions.append(hook_entry(py))
        changes.append("hooks.SessionStart cost check added")
    return changes


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be done without changing anything")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    missing = [name for name in SCRIPTS if not (here / name).is_file()]
    if missing:
        print(f"err: {', '.join(missing)} not found next to install.py - "
              "run from the repo checkout")
        return 1
    if sys.version_info < (3, 8):
        print(f"warn: Python {sys.version.split()[0]} - 3.8 or newer is recommended")

    claude = Path.home() / ".claude"
    settings_path = claude / "settings.json"
    py = interpreter()
    settings = load_settings(settings_path)

    if args.dry_run:
        changes = plan(settings, py)
        print(f"dry-run: would install into {claude}:")
        for name in SCRIPTS:
            print(f"  {here / name} -> {claude / name}")
        print("  settings.json: "
              + (", ".join(changes) if changes else "no changes needed"))
        return 0

    claude.mkdir(parents=True, exist_ok=True)
    for name in SCRIPTS:
        shutil.copy2(here / name, claude / name)
        os.chmod(claude / name, 0o755)
        print(f"ok: installed {claude / name}")

    changes = plan(settings, py)

    if changes:
        if settings_path.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = settings_path.with_name(f"settings.json.bak-{stamp}")
            shutil.copy2(settings_path, backup)
            print(f"ok: backup written: {backup}")
        with open(settings_path, "w") as handle:
            json.dump(settings, handle, indent=2)
            handle.write("\n")
        for change in changes:
            print(f"ok: {change}")
    else:
        print("ok: settings.json unchanged")

    sample = json.dumps({"model": {"display_name": "deepseek-flash"},
                         "workspace": {"current_dir": "/tmp"}})
    probe = subprocess.run([sys.executable, str(claude / "statusline.py")],
                           input=sample, capture_output=True, text=True, timeout=30)
    if probe.returncode == 0 and probe.stdout.strip():
        print("ok: statusline self-check passed")
    else:
        print(f"warn: statusline self-check failed (rc={probe.returncode}) "
              f"{probe.stderr.strip()[:160]}")

    print()
    print("Next steps:")
    print("  - start a NEW Claude Code session; the line appears above the prompt")
    print("  - optional balance figure: export DEEPSEEK_API_KEY=sk-... or put it in")
    print("    ~/.config/claude-deepseek/key.env (first line, after the first =)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
