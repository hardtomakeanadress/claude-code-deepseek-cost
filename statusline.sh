#!/bin/bash
# Status line for Claude Code: model, folder, context usage, session cost.
#
# Claude Code pipes a JSON blob to this script on stdin every few hundred
# milliseconds and draws whatever it prints on a line of its own, just above
# the built-in badges. Nothing here is sent anywhere.
#
# The dollar figure is this session re-priced at DeepSeek's own rates by
# ~/.claude/deepseek-cost.py - Claude Code's built-in number comes from an
# Anthropic rate card and reads far too high. Peak/off-peak is decided per
# request from its timestamp. It is an estimate, not a bill.
#
# The ▽/▲ marker at the end says whether DeepSeek is billing cheap
# (off-peak, half price - green) or peak (red) right now, and when
# that changes.

# The decimal point has to be a point. On a machine whose locale writes
# decimals with a comma, printf "%.2f" refuses the number outright and the
# cost comes out as garbage, so the locale is pinned before anything is
# formatted.
export LC_ALL=C

input=$(cat)

get() { printf '%s' "$input" | jq -r "$1 // empty" 2>/dev/null; }

model=$(get '.model.display_name')
dir=$(get '.workspace.current_dir')
[ -z "$dir" ] && dir=$(get '.cwd')
pct=$(get '.context_window.used_percentage')
dur=$(get '.cost.total_duration_ms')

# Nothing to say yet (the very first moment of a session, before the model is
# known). Printing an empty line is better than printing a line of punctuation.
[ -z "$model" ] && exit 0

# --- colours, dimmed so the line stays out of the way -----------------------
dim=$'\033[2m'; off=$'\033[0m'
cyan=$'\033[36m'; green=$'\033[32m'; yellow=$'\033[33m'; red=$'\033[31m'

# --- folder -----------------------------------------------------------------
# The working directory, spelled out in full. A bare ~ for home read as
# noise, so no shorthand: this is the actual path the session lives in.
folder="$dir"
[ -z "$folder" ] && folder="/"

# --- context meter ----------------------------------------------------------
meter=""
if [ -n "$pct" ]; then
    used=${pct%.*}                      # 42.7 -> 42
    [ -z "$used" ] && used=0
    [ "$used" -gt 100 ] && used=100

    filled=$(( used / 10 ))
    if   [ "$used" -ge 85 ]; then bar_col=$red
    elif [ "$used" -ge 60 ]; then bar_col=$yellow
    else                          bar_col=$green
    fi

    bar=""
    for ((i = 0; i < 10; i++)); do
        if [ "$i" -lt "$filled" ]; then bar="${bar}█"; else bar="${bar}░"; fi
    done
    meter=" ${bar_col}${bar}${off} ${used}%"
fi

# --- cost -------------------------------------------------------------------
# Re-price the session from its transcript at DeepSeek peak/off-peak rates.
# The cache file keeps this cheap to re-run on long transcripts.
tpath=$(get '.transcript_path')
sid=$(get '.session_id')
money=""
if [ -n "$tpath" ] && [ -f "$tpath" ]; then
    cache="/tmp/claude-deepseek-cost${sid:+-$sid}.json"
    cost=$(python3 "$HOME/.claude/deepseek-cost.py" --transcript "$tpath" --cache "$cache" 2>/dev/null)
    if [ -n "$cost" ]; then
        money=$(printf ' $%.2f' "$cost")
    fi
fi

# --- billing tier -----------------------------------------------------------
tier=$(python3 "$HOME/.claude/deepseek-cost.py" --tier 2>/dev/null)
tier_part=""
if [ -n "$tier" ]; then
    case "$tier" in
        "▲"*) tier_col=$red ;;
        *)    tier_col=$green ;;
    esac
    tier_part=" ${tier_col}${tier}${off}"
fi

# --- how long ---------------------------------------------------------------
elapsed=""
if [ -n "$dur" ]; then
    secs=$(( dur / 1000 ))
    if [ "$secs" -ge 3600 ]; then
        elapsed=$(printf ' %dh%02dm' $((secs / 3600)) $(((secs % 3600) / 60)))
    else
        elapsed=$(printf ' %dm' $((secs / 60)))
    fi
fi

printf '%s%s%s %s·%s %s%s%s%s%s%s%s%s\n' \
    "$cyan" "$model" "$off" \
    "$dim" "$off" \
    "$dim" "$folder" "$off" \
    "$meter" "$dim" "$money$elapsed" "$off" \
    "$tier_part"
