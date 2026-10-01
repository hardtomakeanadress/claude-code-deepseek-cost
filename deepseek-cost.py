#!/usr/bin/env python3
"""What a Claude Code session costs at DeepSeek's API prices.

Claude Code's own dollar figure comes from an Anthropic rate card, which is
wildly too high for a session that actually runs on DeepSeek.  This script
re-prices a session from the token counts its transcript already records.

Peak hours are 01:00-04:00 and 06:00-10:00 UTC, Monday through Friday,
excluding Chinese public holidays; everything else - weekends and holidays
included - is off-peak at exactly half price (api-docs.deepseek.com).

The holiday days behind that rule are the State Council's official days off,
fetched from the internet (two machine-readable mirrors of the yearly notice,
cross-checked), never typed in by hand; --refresh-holidays re-fetches them
and rewrites the list in this file.  A session start also visits the
internet once every two weeks (--check-online): DeepSeek's own pricing page,
to see whether the rates or the peak-hours wording changed, and the holiday
mirrors; rates that changed are applied from the page automatically.

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
    deepseek-cost.py --refresh-holidays [--dry-run]  # re-fetch the holiday days
    deepseek-cost.py --check-online    # re-check rates + holidays right now
"""
import argparse
import glob
import hashlib
import json
import os
import re
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

# --- Chinese public holidays -------------------------------------------------
# The days off DeepSeek bills off-peak: the State Council's yearly holiday
# notice, fetched from the internet and cross-checked, never hand-guessed;
# --refresh-holidays re-fetches and rewrites the block below.
#
#   2026 notice (国办发明电〔2025〕7号):
#   https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm
#
# Mirrors: github.com/NateScarlet/holiday-cn (per-year JSON citing the notice)
# and timor.tech/api/holiday (per-year JSON, reachable from China).  When both
# answer they must agree exactly, or nothing gets written anywhere.
HOLIDAYS = {  # days off; refreshed 2026-10-01 via --refresh-holidays
    # sources checked: holiday-cn + timor.tech
    "2025-01-01",
    "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31", "2025-02-01",
    "2025-02-02", "2025-02-03", "2025-02-04",
    "2025-04-04", "2025-04-05", "2025-04-06",
    "2025-05-01", "2025-05-02", "2025-05-03", "2025-05-04", "2025-05-05",
    "2025-05-31", "2025-06-01", "2025-06-02",
    "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-04", "2025-10-05",
    "2025-10-06", "2025-10-07", "2025-10-08",
    "2026-01-01", "2026-01-02", "2026-01-03",
    "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
    "2026-04-04", "2026-04-05", "2026-04-06",
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    "2026-06-19", "2026-06-20", "2026-06-21",
    "2026-09-25", "2026-09-26", "2026-09-27",
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
    "2026-10-06", "2026-10-07",
}

STATE_FILE = os.path.expanduser("~/.claude/deepseek-online.json")
PRICING_URL = "https://api-docs.deepseek.com/quick_start/pricing"
CHECK_EVERY = timedelta(days=14)     # how often "once in a while" is

# The peak-hours sentence this file bills by (read off DeepSeek's page
# 2026-10-01).  check_online warns when the page stops saying exactly this;
# a person then decides whether the windows in is_peak_at move with it.
PEAK_RULE = ("Peak hours are 01:00 - 04:00 and 06:00 - 10:00 UTC, Monday "
             "through Friday, excluding Chinese public holidays. All other "
             "hours are off-peak, including weekends and Chinese public "
             "holidays in full.")


def _read_state():
    """What the last online check found: the holiday days it fetched, the
    rates it read off DeepSeek's pricing page, the rule wording it saw,
    and when to look again.  {} until the first check."""
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _state_days(state):
    """Holiday days from the check; they merge into HOLIDAYS on import, so
    the meter and the reports see what the check saw."""
    days = state.get("days")
    if not isinstance(days, list):
        return set()
    return {day for day in days
            if isinstance(day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)}


def _state_rates(state):
    """Rates from the check, merged over PRICES_PEAK on import.

    Only entries shaped like [hit, miss, out] of plausible per-token
    prices pass: above zero, and below 1e-3 (the page's per-1M numbers all
    start above 1e-3, so a file written in the wrong unit gets ignored
    rather than inflating every figure by a million).
    """
    rates = state.get("rates")
    if not isinstance(rates, dict):
        return {}
    good = {}
    for model, triplet in rates.items():
        if (isinstance(model, str) and isinstance(triplet, list)
                and len(triplet) == 3
                and all(isinstance(value, (int, float))
                        and 0 < value < 1e-3 for value in triplet)):
            good[model] = tuple(float(value) for value in triplet)
    return good


_STATE = _read_state()
HOLIDAYS |= _state_days(_STATE)
PRICES_PEAK.update(_state_rates(_STATE))


def is_peak_at(dt):
    """True when this UTC datetime falls in a DeepSeek peak window."""
    if dt.weekday() >= 5:
        return False
    if dt.date().isoformat() in HOLIDAYS:
        return False
    hour = dt.hour + dt.minute / 60.0
    return 1.0 <= hour < 4.0 or 6.0 <= hour < 10.0


def is_peak(ts):
    """True when an ISO-8601 timestamp falls in a DeepSeek peak window.

    Timestamps are UTC - a trailing Z or an offset; an offset is converted
    first, so one instant cannot land in two different windows.
    """
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return is_peak_at(dt)


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

    The list is fetched, but only as far as the State Council has published;
    a year past its end would be priced - and marked - as peak on days
    DeepSeek bills off-peak, so every surface says so rather than being
    quietly wrong. Empty while the list is current.
    """
    now = now or datetime.now(timezone.utc)
    years = sorted({day[:4] for day in HOLIDAYS})
    if not years or now.year <= int(years[-1]):
        return ""
    if short:
        return f"⚠ holidays end {years[-1]} - run --refresh-holidays"
    return (f"holiday list ends in {years[-1]} - holidays after that price as "
            "peak; run: python3 ~/.claude/deepseek-cost.py --refresh-holidays")


def years_note(years):
    """Reports the response years the holiday list has no days for.

    Nothing fetches years before the tool's sources reach, so days off
    there price as peak; a report says so instead of being quietly wrong.
    """
    known = {day[:4] for day in HOLIDAYS}
    return [f"no holiday list for {year} - days off there price as peak"
            for year in years if year not in known]


class HolidaySourceError(Exception):
    """No usable, agreeing answer from the holiday sources."""


def _get_text(url):
    """Fetch one URL and return the body as text."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "claude-code-deepseek-cost"})
    with urllib.request.urlopen(request, timeout=4) as response:
        return response.read().decode("utf-8", "replace")


def _get_json(url):
    """Fetch one URL and parse the body as JSON."""
    return json.loads(_get_text(url))


def _holidays_holiday_cn(year):
    """The year's days off from the holiday-cn dataset (two mirrors)."""
    error = None
    for url in ("https://cdn.jsdelivr.net/gh/NateScarlet/holiday-cn@master/%d.json" % year,
                "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/%d.json" % year):
        try:
            days = {day["date"] for day in _get_json(url)["days"] if day.get("isOffDay")}
            if len(days) >= 5:
                return days
            error = f"{url}: nothing for {year} yet"
        except (OSError, ValueError, KeyError, TypeError) as problem:
            error = f"{url}: {problem}"
    raise HolidaySourceError(error or f"holiday-cn unreachable for {year}")


def _holidays_timor(year):
    """The year's days off from timor.tech (an API that works from China)."""
    url = "https://timor.tech/api/holiday/year/%d" % year
    try:
        data = _get_json(url)
        if data.get("code") != 0:
            raise ValueError("code %r" % data.get("code"))
        holiday = data.get("holiday")
        if not isinstance(holiday, dict):
            raise ValueError("no holiday map")
        days = {value.get("date") or "%d-%s" % (year, key)
                for key, value in holiday.items()
                if isinstance(value, dict) and value.get("holiday")}
    except (OSError, ValueError, KeyError, TypeError) as problem:
        raise HolidaySourceError(f"{url}: {problem}") from problem
    if len(days) < 5:
        raise HolidaySourceError(f"{url}: nothing for {year} yet")
    return days


def fetch_holidays(year):
    """The year's days off, cross-checked; returns (days, source names).

    Both sources answer -> they must match exactly (independent
    transcriptions of the same notice) -> use the set.  One answers -> use
    it, saying which.  None -> HolidaySourceError.
    """
    answers, problems = {}, []
    for name, fetch in (("holiday-cn", _holidays_holiday_cn),
                        ("timor.tech", _holidays_timor)):
        try:
            answers[name] = fetch(year)
        except HolidaySourceError as problem:
            problems.append(str(problem))
    if not answers:
        raise HolidaySourceError("; ".join(problems) or f"no source for {year}")
    first = next(iter(answers.values()))
    for days in answers.values():
        if days != first:
            differ = " ".join(sorted(first ^ days))
            raise HolidaySourceError(f"sources disagree on {differ}")
    return first, " + ".join(answers)


def _write_state(state):
    """Persist the check state; best effort, never fatal."""
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=1, sort_keys=True)
        os.replace(STATE_FILE + ".tmp", STATE_FILE)
    except OSError:
        pass


def _strip_html(text):
    """Tags out, whitespace squeezed: for matching and comparing prose."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


_PRICE = re.compile(r"\$\s*(\d+(?:\.\d+)?)")


def _parse_pricing(html):
    """Read DeepSeek's pricing page: (rates, off_peak, rule).

    rates and off_peak: {model: (hit, miss, out)} USD per token - the page
    quotes per 1M, the conversion happens here so everything downstream
    matches PRICES_PEAK; rule: the peak-hours sentences verbatim.  Raises
    ValueError when the page doesn't look like the one this parser knows -
    the caller reports that instead of guessing.
    """
    marker = html.upper().find("CACHE HIT")
    if marker < 0:
        raise ValueError("no pricing table on the page")
    table = html[html.rfind("<table", 0, marker):
                 html.find("</table>", marker)]
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)
    if not rows:
        raise ValueError("no table rows")
    header = [_strip_html(cell) for cell in
              re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", rows[0], re.S)]
    models = [re.sub(r"\(\d+\)", "", name).strip()
              for name in header[1:] if name.strip()]
    if not models:
        raise ValueError("no model columns")
    cells = [_strip_html(cell) for cell in
             re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", table, re.S)]

    def find(label):
        for index, text in enumerate(cells):
            if text.upper().startswith(label):
                return index
        raise ValueError(f"no {label} row")

    def pair_after(index):
        """OFF-PEAK prices then PEAK prices following a label cell."""
        if (index + 3 + 2 * len(models) > len(cells)
                or cells[index + 1].upper() != "OFF-PEAK"
                or cells[index + 2 + len(models)].upper() != "PEAK"):
            raise ValueError("unexpected pricing row layout")
        values = []
        for cell in (cells[index + 2:index + 2 + len(models)]
                     + cells[index + 3 + len(models):index + 3 + 2 * len(models)]):
            match = _PRICE.fullmatch(cell)
            if not match:
                raise ValueError(f"not a price: {cell!r}")
            values.append(float(match.group(1)))
        split = len(models)
        return values[:split], values[split:]

    hit_off, hit_peak = pair_after(find("1M INPUT TOKENS (CACHE HIT)"))
    miss_off, miss_peak = pair_after(find("1M INPUT TOKENS (CACHE MISS)"))
    out_off, out_peak = pair_after(find("1M OUTPUT TOKENS"))
    rates = {model: (hit_peak[index] / 1e6, miss_peak[index] / 1e6,
                     out_peak[index] / 1e6)
             for index, model in enumerate(models)}
    off_peak = {model: (hit_off[index] / 1e6, miss_off[index] / 1e6,
                        out_off[index] / 1e6)
                for index, model in enumerate(models)}

    text = _strip_html(html)
    match = re.search(r"Peak hours are .{0,400}?holidays in full\.", text)
    if not match:
        raise ValueError("no peak-hours rule on the page")
    return rates, off_peak, match.group(0)


def check_online(now=None, force=False):
    """The "once in a while" visit to the internet, run from a session start.

    Due every CHECK_EVERY days, and at once on a fresh install; a visit
    that couldn't finish (network down, a source silent) retries the next
    day rather than on every session start; --check-online forces one.  It
    reads DeepSeek's own pricing page - changed rates are applied from
    there, since they are the authority on their prices; a peak-hours rule
    that no longer says what this file was built on is only reported, since
    moving the windows is a person's call.  Then it re-fetches the holiday
    mirrors for this year and next.  Returns short notes for the welcome
    line; [] in the normal case.
    """
    now = now or datetime.now(timezone.utc)
    state = _read_state()
    try:                             # no stamp readable: look right away
        due = force or now >= datetime.fromisoformat(state["next_check"])
    except (KeyError, TypeError, ValueError):
        due = True
    if not due:
        return []
    notes = []
    completed = True

    # --- DeepSeek's own pricing page ---------------------------------------
    try:
        page = _get_text(PRICING_URL)
    except OSError:
        completed = False                # transient; retried tomorrow
    else:
        try:
            rates, off_peak, rule = _parse_pricing(page)
        except ValueError:
            notes.append("DeepSeek's pricing page changed and the rates "
                         "couldn't be read - check PRICES_PEAK")
        else:
            unreadable = ("deepseek-flash" not in rates
                          or any(not 0 < hit <= miss <= out
                                 for hit, miss, out in rates.values()))
            ratio = [model for model, triplet in rates.items()
                     if any(abs(off_peak[model][index] * 2 - triplet[index])
                            > triplet[index] * 1e-9 for index in (0, 1, 2))]
            if unreadable:
                notes.append("DeepSeek's pricing page changed and the rates "
                             "couldn't be read - check PRICES_PEAK")
            elif ratio:
                notes.append("DeepSeek's off-peak prices no longer look like "
                             "exactly half - check the factor in response_cost")
            else:
                before = {model: PRICES_PEAK.get(model) for model in rates}
                PRICES_PEAK.update(rates)
                applied = [model for model in rates
                           if before[model] != rates[model]]
                was_rule, was_rates = state.get("rule"), state.get("rates")
                if applied and (was_rates is None or rates != was_rates):
                    model = ("deepseek-flash" if "deepseek-flash" in applied
                             else sorted(applied)[0])
                    change = (f" miss ${before[model][1] * 1e6:g} -> "
                              f"${rates[model][1] * 1e6:g}"
                              if before[model] else "")
                    more = (f" (+{len(applied) - 1} more)"
                            if len(applied) > 1 else "")
                    notes.append(f"rates updated from DeepSeek's page: "
                                 f"{model}{change}{more}")
                if rule != (was_rule or PEAK_RULE):
                    notes.append("DeepSeek rewrote the peak-hours rule - "
                                 "check the windows in deepseek-cost.py")
                state["rates"] = rates
                state["rule"] = rule

    # --- holiday mirrors ---------------------------------------------------
    fresh = set()
    for year in (now.year, now.year + 1):
        try:
            days, _ = fetch_holidays(year)
        except HolidaySourceError:
            if year == now.year:
                completed = False
            continue                   # next year: just not published yet
        known = {day for day in HOLIDAYS if day.startswith(str(year))}
        if known and known != days:
            notes.append(f"holiday days for {year} changed at the sources - "
                         "run --refresh-holidays")
        fresh |= days
    if fresh:
        HOLIDAYS.update(fresh)
        kept = state.get("days")
        kept = kept if isinstance(kept, list) else []
        state["days"] = sorted({day for day in kept if isinstance(day, str)}
                               | fresh)
    state["next_check"] = (
        now + (CHECK_EVERY if completed else timedelta(days=1))).isoformat()
    _write_state(state)
    return notes


def check_online_command():
    """--check-online: run the visit now and print what it saw."""
    notes = check_online(force=True)
    state = _read_state()
    rates = _state_rates(state)
    for model in sorted(rates):
        hit, miss, out = rates[model]
        print(f"  {model}: peak hit ${hit * 1e6:g} miss ${miss * 1e6:g} "
              f"out ${out * 1e6:g} per 1M")
    print(f"  rule seen: {state.get('rule') or '(not read)'}")
    days = _state_days(state)
    print(f"  holiday days kept: {len(days)} beyond the built-in list")
    print(f"  next check: {state.get('next_check', '(unknown)')}")
    for note in notes:
        print(f"  note: {note}")
    return 0


def _render_holidays(days, sources, now):
    """The HOLIDAYS = {...} block, laid out like the rest of the file."""
    runs, run = [], []
    for day in sorted(days):
        if run and (datetime.fromisoformat(day)
                    - datetime.fromisoformat(run[-1])).days > 1:
            runs.append(run)
            run = []
        run.append(day)
    if run:
        runs.append(run)
    lines = ["HOLIDAYS = {  # days off; refreshed %s via --refresh-holidays"
             % now.date().isoformat(),
             f"    # sources checked: {sources}"]
    for run in runs:
        for start in range(0, len(run), 5):
            lines.append("    "
                         + ", ".join(f'"{day}"' for day in run[start:start + 5])
                         + ",")
    lines.append("}")
    return "\n".join(lines)


def refresh_holidays_command(dry_run=False):
    """Re-fetch holiday days and rewrite the HOLIDAYS block in this file.

    Fetches the previous, current, and next year.  A year both sources
    agree on replaces whatever the file carried for it (their set is the
    least-wrong answer there is, additions and removals alike); a year only
    one source answered for - or the next year, before its notice is out -
    is merged day by day, keeping days already carried.  Refuses to write
    anything unless the result still compiles.
    """
    now = datetime.now(timezone.utc)
    fetched, agreeing, sources = {}, set(), set()
    for year in (now.year - 1, now.year, now.year + 1):
        try:
            days, used = fetch_holidays(year)
        except HolidaySourceError as problem:
            if year == now.year:
                print(f"err: {year}: {problem}")
                return 1
            guess = ("next year's notice not out yet?" if year > now.year
                     else "sources may not carry that year?")
            print(f"warn: {year}: {problem} ({guess})")
            continue
        fetched[year] = days
        names = used.split(" + ")
        sources.update(names)
        if len(names) > 1:               # both answered, and agreed
            agreeing.add(year)
    all_days = set(HOLIDAYS)
    for year, days in sorted(fetched.items()):
        carried = {day for day in all_days if day[:4] == str(year)}
        if year in agreeing:
            if carried - days:
                print(f"warn: {year}: dropped, both sources say these are "
                      "working days: " + " ".join(sorted(carried - days)))
            all_days -= carried
        elif carried - days:
            print(f"warn: {year}: only one source answered, kept: "
                  + " ".join(sorted(carried - days)))
        all_days |= days
    added = sorted(all_days - HOLIDAYS)
    block = _render_holidays(all_days, " + ".join(sorted(sources)), now)
    path = os.path.abspath(__file__)
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    updated, count = re.subn(r"HOLIDAYS = \{.*?^\}", lambda _: block, source,
                             count=1, flags=re.DOTALL | re.MULTILINE)
    if count != 1:
        print(f"err: no HOLIDAYS block found in {path}")
        return 1
    compile(updated, path, "exec")   # never write something that won't run
    if dry_run:
        print(block)
        print(f"dry-run: {path} not touched")
        return 0
    with open(path + ".tmp", "w", encoding="utf-8") as handle:
        handle.write(updated)
    os.replace(path + ".tmp", path)
    state = _read_state()            # keep the online-check days in step
    kept = _state_days(state)
    for year, days in fetched.items():
        if year in agreeing:
            kept = {day for day in kept if day[:4] != str(year)}
        kept |= days
    state["days"] = sorted(kept)
    _write_state(state)
    mark = f" (+{len(added)} new)" if added else " (unchanged)"
    print(f"ok: {path} now carries {len(all_days)} holiday days{mark}")
    if added:
        print("     new: " + " ".join(added))
    return 0


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
            if b'"assistant"' not in raw:   # cheap prefilter; spacing can't
                continue                    # fool it, the parse below decides
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
            hourly[bucket] = hourly.get(bucket, 0.0) + cost   # round at output
        summary = summarize(store)
        stamps = sorted(ts for _, ts, _, _, _ in store.values())
        per_file.append({
            "path": path,
            "requests": summary["requests"],
            "usd": round(summary["usd"], 6),
            "first_ts": stamps[0] if stamps else "",
            "last_ts": stamps[-1] if stamps else "",
            "models": sorted({v[0] for v in store.values()}),
            "hourly_local": {bucket: round(value, 6)
                             for bucket, value in sorted(hourly.items())},
        })
    total = summarize(global_store)
    years = sorted({values[1][:4] for values in global_store.values()
                    if isinstance(values[1], str) and values[1][:4].isdigit()})
    return {
        "years": years,
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
    try:
        notes = check_online()        # the "once in a while" internet visit
    except Exception:                 # a hook must never break session start
        notes = []
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
    for note in notes:
        line += f" · {note}"
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


def _file_head(path):
    """A cheap fingerprint of a transcript's first bytes, so a cache can
    notice that the file it was built from was replaced, not appended to."""
    with open(path, "rb") as handle:
        return hashlib.sha1(handle.read(512)).hexdigest()


def session_cost(path, cache):
    """USD for one transcript; keeps the incremental cache up to date.

    Shared by the CLI (--cache) and by statusline.py, which imports this file.
    """
    store, offset = {}, 0
    if cache and os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as handle:
                cached = json.load(handle)
            if cached.get("head") != _file_head(path):
                raise ValueError("cache is for a different file")
            store = {mid: list(counts) for mid, counts in cached["ids"].items()}
            if any(not (isinstance(counts, list) and len(counts) == 5
                        and isinstance(counts[0], str)
                        and isinstance(counts[1], str)
                        and all(isinstance(value, (int, float))
                                for value in counts[2:]))
                   for counts in store.values()):
                raise ValueError("malformed cache entries")
            offset = int(cached["offset"])
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            store, offset = {}, 0
    if os.path.getsize(path) < offset:         # transcript was replaced
        store, offset = {}, 0
    new_offset = scan(path, store, offset)
    if cache and new_offset != offset:
        with open(cache + ".tmp", "w", encoding="utf-8") as handle:
            json.dump({"offset": new_offset, "head": _file_head(path),
                       "ids": store}, handle)
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
    parser.add_argument("--refresh-holidays", action="store_true",
                        help="re-fetch the holiday days (previous, current, "
                             "and next year) and rewrite the HOLIDAYS list "
                             "in this file")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --refresh-holidays: print the block, write nothing")
    parser.add_argument("--check-online", action="store_true",
                        help="re-check DeepSeek's pricing page and the holiday "
                             "sources right now (normally every %d days from "
                             "--welcome)" % CHECK_EVERY.days)
    args = parser.parse_args()
    if args.refresh_holidays:
        raise SystemExit(refresh_holidays_command(dry_run=args.dry_run))
    if args.check_online:
        raise SystemExit(check_online_command())
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
    for note in years_note(out["years"]):
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
