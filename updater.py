#!/usr/bin/env python3
"""Offer a single-key package update before an interactive chat starts."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO_URL = "https://github.com/hardtomakeanadress/claude-code-deepseek-cost"


def load_costs():
    spec = importlib.util.spec_from_file_location(
        "deepseek_cost", Path(__file__).with_name("deepseek-cost.py"))
    costs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(costs)
    return costs


def latest_version(costs):
    previous = costs._DEADLINE
    costs._DEADLINE = time.monotonic() + 3
    try:
        latest = costs.fetch_latest_version()
    finally:
        costs._DEADLINE = previous
    if latest:
        def remember(state):
            state["latest"] = latest
            return state
        costs._write_state_merged(remember)
    return latest or costs._valid_version(costs._read_state().get("latest"))


def read_choice():
    """Read one key, restoring the terminal before launching Claude Code."""
    if os.name == "nt":
        import msvcrt
        return msvcrt.getwch()
    import termios
    import tty
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd, termios.TCSANOW)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)


def update_package():
    record = Path.home() / ".claude" / "deepseek-install.json"
    repo = Path(json.loads(record.read_text(encoding="utf-8"))["repo"])

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], text=True, timeout=15).strip()

    if git("remote", "get-url", "origin") not in (REPO_URL, REPO_URL + ".git"):
        raise ValueError("checkout origin does not match this package")
    if git("branch", "--show-current") != "main":
        raise ValueError("checkout must be on main to update")
    if git("status", "--porcelain"):
        raise ValueError("checkout has local changes; save them before updating")
    subprocess.run(["git", "-C", str(repo), "pull", "--ff-only", "origin", "main"],
                   check=True, timeout=60)
    subprocess.run([sys.executable, str(repo / "install.py")], check=True, timeout=60)
    return (repo / "version.txt").read_text(encoding="utf-8").strip()


def main():
    if (not sys.stdin.isatty() or not sys.stdout.isatty()
            or any(arg in ("-p", "--print", "--version", "-v", "--help", "-h")
                   for arg in sys.argv[1:])):
        return
    costs = load_costs()
    latest = latest_version(costs)
    if not latest or not costs._newer(latest, costs.VERSION):
        return
    print(f"Update available: v{costs.VERSION} → v{latest}")
    print("[1] Update now   [Enter] Continue without updating", flush=True)
    try:
        choice = read_choice()
    except KeyboardInterrupt:
        print()
        return
    print()
    if choice != "1":
        return
    try:
        version = update_package()
        print(f"Updated to v{version}. Starting chat…")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as problem:
        print(f"Update could not complete: {problem}. Continuing with the installed version.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError):
        pass                            # offline checks must not prevent chat
