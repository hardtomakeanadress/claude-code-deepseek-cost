#!/usr/bin/env python3
"""Install the DeepSeek cost statusline and session-start check for Claude Code.

Run from the repo checkout:

    python3 install.py            # install (or update) into ~/.claude
    python3 install.py --dry-run  # show what would change, touch nothing

It copies deepseek-cost.py and statusline.py into ~/.claude/ and merges two
entries into ~/.claude/settings.json:

  - statusLine           the command Claude Code runs to draw the status line
  - hooks.SessionStart   the one-line cost check at the start of a chat

When the machine is routed to a 1M DeepSeek model (deepseek-flash,
deepseek-v4-flash, deepseek-v4-pro), it also ensures the context-window keys
Claude Code needs - CLAUDE_CODE_MAX_CONTEXT_TOKENS and
CLAUDE_CODE_AUTO_COMPACT_WINDOW - in whichever settings file carries the
routing (the claude-deepseek router config, or ~/.claude/settings.json).
See "Context window" in the README.

Safety rules: settings.json is backed up before any change, a statusLine
that points at some other tool is never overwritten (one that already
references this tool's script is refreshed in place), an already-set context
key is never overridden, the hook is added at most once, and nothing else in
the file is touched. Safe to re-run - that is also how you update the
scripts after a `git pull`.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# An already-installed command, in either spelling this tool writes: the long
# name, or the 8.3 form (DEEPSE~1.PY) Windows falls back to when the home path
# does not survive quoting. The short form is why a run must not trust a plain
# "deepseek-cost.py" substring check - it would not recognize its own hook and
# would append a duplicate on every install.
_OURS = re.compile(r"deepseek-cost\.py|deepse~1\.py", re.IGNORECASE)

SCRIPTS = ("deepseek-cost.py", "statusline.py")

# The 1M-window DeepSeek IDs; Claude Code assumes 200K for IDs it does not
# recognize and auto-compacts early. A name already ending in [1m] declares
# its own window, so it only needs the auto-compact key.
CONTEXT_1M_MODELS = ("deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro")
ROUTER_SETTINGS = "~/.config/claude-deepseek/settings.json"


def _runs(name):
    """True when `name -c 'print(1)'` really executes."""
    try:
        done = subprocess.run([name, "-c", "print(1)"],
                              capture_output=True, text=True, timeout=15)
    except OSError:
        return False
    return done.returncode == 0 and done.stdout.strip() == "1"


def interpreter():
    """The name to write into settings.json: python3 wherever it exists.

    On Windows: python (python.org installs), then python3 (Microsoft Store),
    then the py launcher - in that order. A WindowsApps alias counts only
    when a Store-installed Python actually answers; the bare stub would open
    the Microsoft Store instead.
    """
    names = ("python", "python3", "py") if os.name == "nt" else ("python3", "python")
    for name in names:
        found = shutil.which(name)
        if not found:
            continue
        if "windowsapps" in found.lower() and not _runs(name):
            continue
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


def _windows_short_path(path):
    """The 8.3 form of path, or None - it has no spaces and no $ or quotes."""
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(1024)
        size = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
        if 0 < size < len(buffer):
            short = buffer.value
            if short and short.lower() != str(path).lower():
                return short
    except Exception:
        pass
    return None


def command_for(py, script, extra=()):
    """The command string Claude Code runs for one of our scripts.

    Unix keeps the $HOME form. On Windows the command goes through Git Bash
    (which eats unquoted backslashes) or PowerShell depending on what is
    installed, so the path is written as a concrete forward-slash absolute
    path. Plain paths go in bare; a path with spaces goes in double quotes;
    a path with $ or quotes (which the two shells expand differently and no
    single quoting survives) prefers the 8.3 short name when the filesystem
    offers one.
    """
    if os.name == "nt":
        # bare interpreter name (a quoted one parses in bash but not in
        # PowerShell)
        target = Path.home() / ".claude" / script
        shown = (_windows_short_path(target) or str(target)).replace("\\", "/")
        if any(ch in shown for ch in " '\"$`"):
            shown = f'"{shown}"'
        parts = [py, shown]
    else:
        parts = [py, f'"$HOME/.claude/{script}"']
    return " ".join(parts + list(extra))


def status_block(py):
    return {"type": "command",
            "command": command_for(py, "statusline.py"),
            "padding": 0}


def hook_entry(py):
    return {"matcher": "startup|resume|clear",
            "hooks": [{"type": "command",
                       "command": command_for(py, "deepseek-cost.py", ("--welcome",)),
                       "timeout": 30}]}


def save_settings(path, settings):
    """Write settings, keeping a timestamped backup of the old file."""
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        shutil.copy2(path, backup)
        print(f"ok: backup written: {backup}")
    with open(path, "w") as handle:
        json.dump(settings, handle, indent=2)
        handle.write("\n")


def _quiet_json(path):
    """path's JSON object, or None when missing, unreadable, or not a dict."""
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _env(settings):
    env = settings.get("env")
    return env if isinstance(env, dict) else {}


def context_plan(settings, settings_path):
    """Ensure the DeepSeek context-window keys where the routing lives.

    Claude Code assumes a 200K window for model IDs it does not recognize and
    auto-compacts inside that guess; the 1M DeepSeek models hit it. The fix -
    DeepSeek's own documented config - is CLAUDE_CODE_MAX_CONTEXT_TOKENS (a
    [1m] model name declares the same thing, so it only needs the second key)
    plus CLAUDE_CODE_AUTO_COMPACT_WINDOW=786432. Added only for a recognized
    1M DeepSeek ID and never over an already-set key, to the router config
    when it carries the routing, else ~/.claude/settings.json.

    Returns (target_path, target_data, added) - added maps each key actually
    added to its value ({} when nothing was). Nothing is written here.
    """
    router = Path(os.path.expanduser(ROUTER_SETTINGS))
    router_data = _quiet_json(router)
    model = os.environ.get("ANTHROPIC_MODEL", "").strip()
    if not model:
        model = _env(router_data or {}).get("ANTHROPIC_MODEL", "")
    if not model:
        model = _env(settings).get("ANTHROPIC_MODEL", "")
    base = model.lower().split("[1m]", 1)[0].strip()
    if base not in CONTEXT_1M_MODELS:
        if not model:
            print("note: no DeepSeek model detected - if Claude Code compacts early,")
            print("      add the context keys by hand (README 'Context window')")
        elif base.startswith("deepseek"):
            print(f"note: {model!r} is not a known 1M DeepSeek ID - "
                  "context keys not added")
        return settings_path, settings, {}
    router_env = _env(router_data or {})
    if router_env.get("ANTHROPIC_MODEL") or router_env.get("ANTHROPIC_BASE_URL"):
        target, data = router, router_data
    else:
        target, data = settings_path, settings
    env = data.get("env")
    if env is None:
        env = data["env"] = {}
    elif not isinstance(env, dict):
        print(f"warn: {target} has an unexpected 'env' shape - context keys not added")
        return settings_path, settings, {}
    wanted = {}
    if not model.lower().endswith("[1m]"):
        wanted["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = "1000000"
    wanted["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "786432"
    added = {}
    for key, value in wanted.items():
        if key in env or key in os.environ:
            print(f"ok: {key} already set")
        else:
            env[key] = value
            added[key] = value
    return target, data, added


def plan(settings, py):
    """Merge our blocks into settings; return the list of changes made."""
    changes = []
    ours = status_block(py)
    existing = settings.get("statusLine")
    ours_ref = ".claude/statusline.py"
    existing_ref = (str(existing.get("command", "")).lower().replace("\\", "/")
                    if isinstance(existing, dict) else "")
    if existing is None:
        settings["statusLine"] = ours
        changes.append("statusLine added")
    elif existing == ours:
        print("ok: statusLine already configured")
    elif ours_ref in existing_ref:       # ours from an older install - update
        merged = dict(existing)
        merged.update(ours)
        if merged == existing:
            print("ok: statusLine already configured")
        else:
            settings["statusLine"] = merged
            changes.append("statusLine updated to the current command")
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
    else:
        ours_hook = hook_entry(py)
        wanted = ours_hook["hooks"][0]["command"]

        def is_ours(value):
            return bool(_OURS.search(json.dumps(value)))

        matches = [entry for entry in sessions
                   if isinstance(entry, dict) and is_ours(entry)]
        if not matches:
            sessions.append(ours_hook)
            changes.append("hooks.SessionStart cost check added")
        else:
            # update just the matching handler, keeping any siblings and the
            # entry's own matcher untouched
            changed = False
            handlers = matches[0].get("hooks")
            if isinstance(handlers, list):
                for handler in handlers:
                    if (isinstance(handler, dict) and is_ours(handler)
                            and handler.get("command") != wanted):
                        handler["command"] = wanted
                        changed = True
            # an entry per install run stacked up when an older version could
            # not recognize its own short-path commands - keep the first, drop
            # the rest (they are byte-identical duplicates)
            if len(matches) > 1:
                for entry in matches[1:]:
                    sessions.remove(entry)
                changes.append(f"hooks.SessionStart duplicate cost checks removed "
                               f"({len(matches) - 1})")
            if changed:
                changes.append("hooks.SessionStart cost check updated to the current command")
            elif len(matches) == 1:
                print("ok: SessionStart hook already present")
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
        ctx_target, _, ctx_added = context_plan(settings, settings_path)
        print(f"dry-run: would install into {claude}:")
        for name in SCRIPTS:
            print(f"  {here / name} -> {claude / name}")
        print("  settings.json: "
              + (", ".join(changes) if changes else "no changes needed"))
        if ctx_added:
            shown = ", ".join(f"{key}={value}" for key, value in ctx_added.items())
            print(f"  {ctx_target}: would add {shown}")
        return 0

    claude.mkdir(parents=True, exist_ok=True)
    for name in SCRIPTS:
        shutil.copy2(here / name, claude / name)
        os.chmod(claude / name, 0o755)
        print(f"ok: installed {claude / name}")

    changes = plan(settings, py)
    ctx_target, ctx_data, ctx_added = context_plan(settings, settings_path)
    if ctx_target == settings_path:
        changes.extend(f"context: {key}={value}" for key, value in ctx_added.items())

    if changes:
        save_settings(settings_path, settings)
        for change in changes:
            print(f"ok: {change}")
    else:
        print("ok: settings.json unchanged")
    if ctx_added and ctx_target != settings_path:
        save_settings(ctx_target, ctx_data)
        shown = ", ".join(f"{key}={value}" for key, value in ctx_added.items())
        print(f"ok: context window: added {shown} to {ctx_target}")

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
