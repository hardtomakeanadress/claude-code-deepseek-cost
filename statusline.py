#!/usr/bin/env python3
"""Status line for Claude Code: model, folder, context, DeepSeek cost, tier.

Claude Code pipes a JSON blob to this script on stdin and draws whatever it
prints on a line of its own. Pure Python 3, standard library only, so the
same file runs on Linux, macOS and Windows - no bash and no jq. It prints
byte-for-byte what statusline.sh prints; that one is kept as a fallback.

The dollar figure is this session re-priced at DeepSeek's own rates by
deepseek-cost.py - Claude Code's built-in number comes from an Anthropic
rate card and reads far too high. Peak/off-peak is decided per request from
its timestamp. It is an estimate, not a bill.

The trailing marker is DeepSeek's billing tier right now: green ▽ cheap
(off-peak, half price) or red ▲ peak, with the time left in that window.
"""
import importlib.util
import json
import os
import sys
import tempfile

DIM = "\033[2m"
OFF = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"


def load_costs():
    """Import deepseek-cost.py next to this file (the hyphen blocks import)."""
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "deepseek_cost", os.path.join(here, "deepseek-cost.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def meter_part(data):
    pct = (data.get("context_window") or {}).get("used_percentage")
    if pct is None:
        return ""
    try:
        used = min(int(float(pct)), 100)
    except (TypeError, ValueError):
        return ""
    if used >= 85:
        colour = RED
    elif used >= 60:
        colour = YELLOW
    else:
        colour = GREEN
    bar = "█" * (used // 10) + "░" * (10 - used // 10)
    return f" {colour}{bar}{OFF} {used}%"


def money_part(data, costs):
    tpath = data.get("transcript_path")
    if not tpath or not os.path.isfile(tpath):
        return ""
    sid = data.get("session_id") or ""
    cache = os.path.join(
        tempfile.gettempdir(),
        "claude-deepseek-cost" + (f"-{sid}" if sid else "") + ".json")
    try:
        return f" ${costs.session_cost(tpath, cache):.2f}"
    except Exception:                  # a statusline must never crash
        return ""


def elapsed_part(data):
    dur = (data.get("cost") or {}).get("total_duration_ms")
    if dur is None:
        return ""
    try:
        secs = int(float(dur)) // 1000
    except (TypeError, ValueError):
        return ""
    if secs >= 3600:
        return f" {secs // 3600}h{secs % 3600 // 60:02d}m"
    return f" {secs // 60}m"


def tier_part(costs):
    try:
        tier = costs.tier_line()
    except Exception:
        return ""
    if not tier:
        return ""
    colour = RED if tier.startswith("▲") else GREEN
    return f" {colour}{tier}{OFF}"


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    model = (data.get("model") or {}).get("display_name") or ""
    if not model:                      # first moment of a session: stay quiet
        return
    folder = ((data.get("workspace") or {}).get("current_dir")
              or data.get("cwd") or "/")
    costs = load_costs()
    print(f"{CYAN}{model}{OFF} {DIM}·{OFF} "
          f"{DIM}{folder}{OFF}"
          f"{meter_part(data)}"
          f"{DIM}{money_part(data, costs)}{elapsed_part(data)}{OFF}"
          f"{tier_part(costs)}")


if __name__ == "__main__":
    main()
