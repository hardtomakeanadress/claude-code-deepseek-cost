# claude-code-deepseek-cost

Real DeepSeek API spend for [Claude Code](https://claude.com/claude-code).

Claude Code's built-in dollar figure is priced from an Anthropic rate card. When
your sessions actually run on DeepSeek, that number reads wildly too high
(50–100× in observed cases — a session Claude Code priced at $214.54 really cost
$2.22). This tool ignores it and re-prices your session transcripts at DeepSeek's
own rates, including its peak/off-peak schedule.

Two single-file Python 3 scripts, **standard library only** — no pip installs, no
bash, no jq. Runs on Linux, macOS, and Windows (WSL, Git Bash, or PowerShell).

## What you get

**Live statusline**, redrawn continuously while you work:

```
deepseek-flash · /home/you/project ███████░░░ 71% $0.17 24m ▽ cheap · 162h16m left
```

model · working folder · context-window meter · this session's DeepSeek cost ·
session time so far · billing tier — green `▽ cheap` or red `▲ peak`, with the
time left in the current window.

**Session-start cost check** — one line every time a chat starts, resumes, or clears:

```
deepseek-flash — $4.00 left · $3.70 spent so far · ▽ cheap · 162h42m left
```

**Report CLI** — re-price any transcript, one project, or everything:

```
$ python3 deepseek-cost.py --all-projects --report
total $3.7026 over 2965 responses (137 rows skipped: model not on the card)
  peak $0.0420 (14 req)   off-peak $3.6606 (2951 req)
  tokens: miss 3,077,690  hit 377,122,711  out 12,345,678
  merged 8123 streamed duplicates
```

## How the costing works

- Claude Code transcripts (`~/.claude/projects/<project>/<session>.jsonl`) record
  exact token counts for every API response: cache-hit tokens, cache-miss tokens,
  output tokens. These scripts replay those counts against DeepSeek's price card.
- A streamed response appears in the transcript several times with growing token
  counts; the largest count seen per message id is used, so every response is
  priced exactly once.
- `cache_creation` tokens are billed as misses (DeepSeek has no separate
  cache-write line item), and thinking tokens are included in output tokens.
- Peak hours are **01:00–04:00 and 06:00–10:00 UTC, Monday–Friday, excluding
  Chinese public holidays**. Everything else — nights, weekends, holidays — is
  off-peak at exactly half price. Peak rates per 1M tokens:

  | model           | cache hit | cache miss | output |
  |-----------------|-----------|------------|--------|
  | deepseek-flash  | $0.006    | $0.30      | $1.20  |
  | deepseek-v4-pro | $0.044    | $1.32      | $3.96  |

- It is an **estimate, not a bill**: it re-prices the token counts the transcripts
  recorded; it does not read DeepSeek's billing system.
- A resumed session keeps the same session id and the same transcript file, so
  the statusline cost simply continues from the previous number.

## Install

1. Copy `deepseek-cost.py` and `statusline.py` into `~/.claude/`.
2. Merge the blocks from `settings.example.json` into `~/.claude/settings.json`.
   That file sets the statusline and installs the session-start hook for
   `startup|resume|clear`.
3. Optional — the account balance: put your DeepSeek API key in
   `~/.config/claude-deepseek/key.env` (first line, `SOMENAME=sk-...`; the part
   after the first `=` is used). Without it everything works except the
   "$X left" figure, which reads "balance unavailable".
   The model name comes from `env.ANTHROPIC_MODEL` in
   `~/.config/claude-deepseek/settings.json` (the router config), falling back
   to `deepseek-flash`.

### Windows

Claude Code runs command strings through Git Bash when it is installed, and
through PowerShell when it is not. Two adjustments:

- Use `python` instead of `python3` — python.org installs provide `python`, and
  `python3` usually doesn't exist on Windows.
- Keep paths forward-slashed, or reference `$HOME` (Git Bash) /
  `$env:USERPROFILE` (PowerShell) instead of writing them out — Git Bash treats
  backslashes in command paths as escape characters and the command fails
  silently.

`statusline.sh` is the original Bash implementation, kept as a fallback with
identical output for systems that have bash and jq.

## CLI reference

```
deepseek-cost.py --transcript session.jsonl            # USD on stdout
deepseek-cost.py --transcript session.jsonl --report   # human-readable table
deepseek-cost.py --transcript s.jsonl --cache c.json   # incremental, for fast re-runs
deepseek-cost.py --all-projects --report               # everything under ~/.claude/projects
deepseek-cost.py --tier                                # e.g. "▽ cheap · 162h42m left"
deepseek-cost.py --welcome                             # SessionStart hook JSON payload
```

## Notes

- The Chinese-public-holiday list currently covers 2026 (State Council notice);
  refresh `HOLIDAYS` in `deepseek-cost.py` when the next notice is published.
- Clock times in report output are local, in the system timezone.
- Rows from models that aren't on the DeepSeek card (e.g. Anthropic models from
  before the router was set up) are skipped and counted in the summary.
