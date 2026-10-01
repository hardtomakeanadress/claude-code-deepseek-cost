#!/usr/bin/env python3
"""What a Claude Code session costs at DeepSeek's API prices.

Claude Code's own dollar figure comes from an Anthropic rate card, which is
wildly too high for a session that actually runs on DeepSeek.  This script
re-prices a session from the token counts its transcript already records.

Peak hours are 01:00-04:00 and 06:00-10:00 UTC, Monday through Friday,
excluding Chinese public holidays; everything else - weekends and holidays
included - is off-peak at exactly half price (api-docs.deepseek.com).

    USD per 1M tokens, peak rates:
        deepseek-flash     cache hit 0.006   cache miss 0.30   output 1.20
        deepseek-v4-pro    cache hit 0.044   cache miss 1.32   output 3.96

Notes on the accounting:
  - cache_creation tokens are billed as misses; DeepSeek has no separate
    cache-write line item
  - thinking tokens are part of output_tokens and billed at the output price
  - a message id appears several times in a transcript while its reply is
    streamed; the largest counts seen for an id are used, so every API
    response is priced exactly once

The balance lookup reads $DEEPSEEK_API_KEY when set, otherwise the first
line of ~/.config/claude-deepseek/key.env (everything after the first =).

    deepseek-cost.py --transcript t.jsonl [--transcript ...]   # USD on stdout
    deepseek-cost.py --transcript t.jsonl --report             # human table
    deepseek-cost.py --transcript t.jsonl --report --json      # machine table
    deepseek-cost.py --transcript t.jsonl --cache c.json       # incremental
    deepseek-cost.py --tier    # billing tier right now, for the statusline
    deepseek-cost.py --welcome # SessionStart hook payload: balance/spend/tier
"""
import argparse
import glob
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

PRICES_PEAK = {
    "deepseek-flash": (0.006 / 1e6, 0.30 / 1e6, 1.20 / 1e6),   # hit, miss, out
    "deepseek-v4-pro": (0.044 / 1e6, 1.32 / 1e6, 3.96 / 1e6),
}
ALIASES = {  # retired names; traffic is served and billed as Flash
    "deepseek-chat": "deepseek-flash",
    "deepseek-reasoner": "deepseek-flash",
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-v4-flash-vision-exp": "deepseek-flash",
}

# Chinese public holidays 2026, State Council notice 国办发明电〔2025〕7号.
# Only 2026 is listed; refresh the list when the next notice is published.
HOLIDAYS = {
    "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
    "2026-04-04", "2026-04-05", "2026-04-06",
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    "2026-06-19", "2026-06-20", "2026-06-21",
    "2026-09-25", "2026-09-26", "2026-09-27",
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
    "2026-10-06", "2026-10-07",
}


def is_peak_at(dt):
    """True when this UTC datetime falls in a DeepSeek peak window."""
    if dt.weekday() >= 5:
        return False
    if dt.date().isoformat() in HOLIDAYS:
        return False
    hour = dt.hour + dt.minute / 60.0
    return 1.0 <= hour < 4.0 or 6.0 <= hour < 10.0


def is_peak(ts):
    """True when an ISO-8601 UTC timestamp falls in a DeepSeek peak window."""
    return is_peak_at(datetime.fromisoformat(ts.replace("Z", "+00:00")))


def tier_line(now=None):
    """One statusline-sized phrase: the billing tier now and when it flips."""
    now = now or datetime.now(timezone.utc)
    current = is_peak_at(now)
    probe = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    limit = now + timedelta(days=14)
    while probe < limit and is_peak_at(probe) == current:
        probe += timedelta(hours=1)
    mark, label = ("▲", "peak") if current else ("▽", "cheap")
    if probe >= limit:
        return f"{mark} {label}"
    minutes = int(((probe - now).total_seconds() + 59) // 60)
    hours, minutes = divmod(minutes, 60)
    left = f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"
    return f"{mark} {label} · {left} left"


def holiday_note(short=False, now=None):
    """A warning when the holiday list no longer covers the current year.

    The list is a snapshot. Once the calendar year passes its last entry, a
    holiday it doesn't know would be priced - and marked - as peak on a day
    DeepSeek bills off-peak, so every surface says so rather than being
    quietly wrong. Empty while the list is current.
    """
    now = now or datetime.now(timezone.utc)
    years = sorted({day[:4] for day in HOLIDAYS})
    if not years or now.year <= int(years[-1]):
        return ""
    if short:
        return f"⚠ holiday list ends {years[-1]}"
    return (f"holiday list ends in {years[-1]} - Chinese holidays after that "
            "get counted as peak; refresh HOLIDAYS in deepseek-cost.py")


def response_cost(model, ts, miss, hit, out):
    """USD for one API response, or None when the model has no price card."""
    card = PRICES_PEAK.get(ALIASES.get(model, model))
    if card is None:
        return None
    factor = 1.0 if is_peak(ts) else 0.5
    return factor * (card[0] * hit + card[1] * miss + card[2] * out)


def absorb(store, mid, model, ts, miss, hit, out):
    """Record one sighting of a message id, keeping the largest counts."""
    prev = store.get(mid)
    if prev is None:
        store[mid] = [model, ts, miss, hit, out]
        return
    for index, value in ((2, miss), (3, hit), (4, out)):
        if value > prev[index]:
            prev[index] = value


def scan(path, store, start=0):
    """Read assistant rows in path from byte offset start; return new offset."""
    end = start
    with open(path, "rb") as handle:
        if start:
            handle.seek(start)
        for raw in handle:
            if not raw.endswith(b"\n"):
                break                 # partial tail; next call re-reads it
            end += len(raw)
            if b'"type":"assistant"' not in raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage") or {}
            mid = msg.get("id")
            model = msg.get("model") or ""
            ts = rec.get("timestamp") or ""
            if not mid or not ts or not usage or model == "<synthetic>":
                continue
            miss = int(usage.get("input_tokens") or 0) \
                + int(usage.get("cache_creation_input_tokens") or 0)
            absorb(store, mid, model, ts, miss,
                   int(usage.get("cache_read_input_tokens") or 0),
                   int(usage.get("output_tokens") or 0))
    return end


def summarize(store):
    total = {
        "usd": 0.0, "requests": 0, "unknown_models": 0,
        "miss_tokens": 0, "hit_tokens": 0, "out_tokens": 0,
        "usd_miss": 0.0, "usd_hit": 0.0, "usd_out": 0.0,
        "peak_usd": 0.0, "peak_requests": 0,
        "off_peak_usd": 0.0, "off_peak_requests": 0,
    }
    for model, ts, miss, hit, out in store.values():
        cost = response_cost(model, ts, miss, hit, out)
        if cost is None:
            total["unknown_models"] += 1
            continue
        hit_price, miss_price, out_price = PRICES_PEAK[ALIASES.get(model, model)]
        factor = 1.0 if is_peak(ts) else 0.5
        total["usd"] += cost
        total["requests"] += 1
        total["miss_tokens"] += miss
        total["hit_tokens"] += hit
        total["out_tokens"] += out
        total["usd_miss"] += miss_price * factor * miss
        total["usd_hit"] += hit_price * factor * hit
        total["usd_out"] += out_price * factor * out
        if factor == 1.0:
            total["peak_usd"] += cost
            total["peak_requests"] += 1
        else:
            total["off_peak_usd"] += cost
            total["off_peak_requests"] += 1
    return total


def report(paths):
    global_store = {}
    per_file = []
    for path in paths:
        store = {}
        scan(path, store, 0)
        for mid, values in store.items():
            keep = global_store.get(mid)
            if keep is None:
                global_store[mid] = list(values)
            else:
                for index in (2, 3, 4):
                    if values[index] > keep[index]:
                        keep[index] = values[index]
        hourly = {}
        for model, ts, miss, hit, out in store.values():
            cost = response_cost(model, ts, miss, hit, out)
            if cost is None:
                continue
            local = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
            bucket = local.strftime("%Y-%m-%dT%H")
            hourly[bucket] = round(hourly.get(bucket, 0.0) + cost, 6)
        summary = summarize(store)
        stamps = sorted(ts for _, ts, _, _, _ in store.values())
        per_file.append({
            "path": path,
            "requests": summary["requests"],
            "usd": round(summary["usd"], 6),
            "first_ts": stamps[0] if stamps else "",
            "last_ts": stamps[-1] if stamps else "",
            "models": sorted({v[0] for v in store.values()}),
            "hourly_local": dict(sorted(hourly.items())),
        })
    total = summarize(global_store)
    return {
        "total": {k: (round(v, 6) if isinstance(v, float) else v)
                  for k, v in total.items()},
        "duplicate_responses_merged": sum(f["requests"] for f in per_file)
        - total["requests"],
        "files": per_file,
    }


def fetch_balance():
    """USD left on the DeepSeek account, or None when it can't be read.

    The key comes from $DEEPSEEK_API_KEY when set, otherwise from the first
    line of ~/.config/claude-deepseek/key.env (everything after the first =).
    """
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        try:
            with open(os.path.expanduser("~/.config/claude-deepseek/key.env")) as handle:
                key = handle.readline().split("=", 1)[1].strip()
        except (OSError, IndexError):
            return None
    if not key:
        return None
    request = urllib.request.Request(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return float(json.load(response)["balance_infos"][0]["total_balance"])
    except (OSError, ValueError, KeyError, IndexError):
        return None


def billing_model():
    """The model Claude Code is pointed at.

    $ANTHROPIC_MODEL wins when set - that is what a running session actually
    uses - then the claude-deepseek router config, then a plain guess.
    """
    model = os.environ.get("ANTHROPIC_MODEL", "").strip()
    if model:
        return model
    try:
        with open(os.path.expanduser("~/.config/claude-deepseek/settings.json")) as handle:
            return json.load(handle)["env"]["ANTHROPIC_MODEL"]
    except (OSError, ValueError, KeyError):
        return "deepseek-flash"


def welcome():
    """SessionStart hook payload: model, balance, all-time spend, tier."""
    base = os.path.expanduser("~/.claude/projects")
    try:
        spent = report(sorted(glob.glob(os.path.join(base, "**", "*.jsonl"),
                                        recursive=True)))["total"]["usd"]
        spent_text = f"${spent:.2f} spent so far"
    except (OSError, ValueError):
        spent_text = "spend unavailable"
    balance = fetch_balance()
    left = f"${balance:.2f} left" if balance is not None else "balance unavailable"
    line = f"{billing_model()} — {left} · {spent_text} · {tier_line()}"
    note = holiday_note(short=True)
    if note:
        line += f" · {note}"
    print(json.dumps({
        "systemMessage": line,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": line + " (details: ~/.claude/deepseek-cost.py)",
        },
    }))


def session_cost(path, cache):
    """USD for one transcript; keeps the incremental cache up to date.

    Shared by the CLI (--cache) and by statusline.py, which imports this file.
    """
    store, offset = {}, 0
    if cache and os.path.exists(cache):
        try:
            with open(cache) as handle:
                cached = json.load(handle)
            store = {k: list(v) for k, v in cached.get("ids", {}).items()}
            offset = int(cached.get("offset", 0))
        except (ValueError, OSError, TypeError):
            store, offset = {}, 0
    if os.path.getsize(path) < offset:         # transcript was replaced
        store, offset = {}, 0
    new_offset = scan(path, store, offset)
    if cache and new_offset != offset:
        with open(cache + ".tmp", "w") as handle:
            json.dump({"offset": new_offset, "ids": store}, handle)
        os.replace(cache + ".tmp", cache)
    return summarize(store)["usd"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", action="append", default=[])
    parser.add_argument("--cache", help="state file for incremental runs")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--all-projects", action="store_true",
                        help="include every transcript under ~/.claude/projects")
    parser.add_argument("--tier", action="store_true",
                        help="only print the current billing tier, e.g. '▽ cheap · 162h42m left'")
    parser.add_argument("--welcome", action="store_true",
                        help="print the SessionStart hook payload: balance, spend, tier")
    args = parser.parse_args()
    if args.tier:
        print(tier_line())
        return
    if args.welcome:
        welcome()
        return
    if args.all_projects:
        base = os.path.expanduser("~/.claude/projects")
        args.transcript += sorted(
            glob.glob(os.path.join(base, "**", "*.jsonl"), recursive=True))
    if not args.transcript:
        parser.error("at least one --transcript is required")

    if args.cache and len(args.transcript) == 1 and not args.report:
        print(f"{session_cost(args.transcript[0], args.cache):.4f}")
        return

    out = report(args.transcript)
    if args.json:
        print(json.dumps(out, indent=1))
        return
    total = out["total"]
    plural = "" if total["requests"] == 1 else "s"
    print(f"total ${total['usd']:.4f} over {total['requests']} response{plural} "
          f"({total['unknown_models']} rows skipped: model not on the card)")
    print(f"  peak ${total['peak_usd']:.4f} ({total['peak_requests']} req)   "
          f"off-peak ${total['off_peak_usd']:.4f} ({total['off_peak_requests']} req)")
    note = holiday_note()
    if note:
        print(f"  note: {note}")
    print(f"  tokens: miss {total['miss_tokens']:,}  hit {total['hit_tokens']:,}"
          f"  out {total['out_tokens']:,}")
    merged = out["duplicate_responses_merged"]
    if merged:
        print(f"  merged {merged} duplicate responses (same id in several transcripts)")
    for entry in out["files"]:
        name = entry["path"].split("/projects/")[-1]
        models = ",".join(entry["models"]) or "?"
        print(f"  ${entry['usd']:>8.4f}  {entry['requests']:>5} req  "
              f"{entry['first_ts'][:16]} -> {entry['last_ts'][:16]}  "
              f"{models:>19}  {name}")


if __name__ == "__main__":
    main()
