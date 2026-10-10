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
deepseek-flash · /home/you/project ███████░░░ 71% $0.17 24m ▽ cheap · 162h16m left · v1.2
```

model · working folder · context-window meter · this session's DeepSeek cost ·
time in the session · billing tier — green `▽ cheap` or red `▲ peak`, with the
time left in the current window — and the version you run, so a screenshot or
bug report says which copy it was.

**Session-start cost check** — one line when a chat starts, resumes, or clears:

```
deepseek-flash — $18.42 left · $6.58 spent so far · ▽ cheap · 162h42m left
```

…and when the two-weekly check has seen a newer release in the repo, it
carries `· ⬆ v1.2 on GitHub - git pull && python3 install.py` until you update.

**Report CLI** — re-price any transcript, one project, or everything
(example output):

```
$ python3 deepseek-cost.py --all-projects --report
total $8.2871 over 5119 responses (137 rows skipped: model not on the card)
  peak $0.0930 (31 req)   off-peak $8.1941 (5088 req)
  tokens: miss 5,431,220  hit 402,918,744  out 4,210,553
  …then one row per transcript
```

## Requirements

- [Claude Code](https://claude.com/claude-code), already running on DeepSeek —
  by any means (a router config such as `~/.config/claude-deepseek/settings.json`,
  or `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic` plus `ANTHROPIC_MODEL`
  exported before launching). Nothing here sets up the routing; it only measures it.
- Python 3.8 or newer on PATH (`python3 --version`; on Windows, `python --version`).
- Optional, for the account-balance figure: a DeepSeek API key (see Configuration).

If your sessions run on Anthropic models, everything still installs and works;
those responses are simply skipped in reports ("rows skipped: model not on the card").

## Context window

Claude Code assumes a **200K** context window for model IDs it does not
recognize, and auto-compacts inside that guess — including `deepseek-flash`
and `deepseek-v4-pro`, which take 1M (long chats then compact early).
DeepSeek's documented fix, and what this installer applies when it recognizes
a 1M DeepSeek model:

| key | value | note |
|-----|-------|------|
| `CLAUDE_CODE_MAX_CONTEXT_TOKENS` | `1000000` | unneeded when the model name already ends in `[1m]`, which declares the same window |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | `786432` | DeepSeek's own recommended compact point; the effective window is the smaller of the two |

The keys go into the settings file that carries the routing: the
`~/.config/claude-deepseek/settings.json` router config when there is one,
otherwise `~/.claude/settings.json` (export-style setups). An already-set key
is never overridden, and a Claude Code restart is required to pick them up.
If detection misses your setup, add the same keys by hand, or append `[1m]`
to the model name (DeepSeek's official form). Don't declare a window larger
than the model really has: requests error out mid-chat instead of compacting.

## Install

```sh
git clone https://github.com/hardtomakeanadress/claude-code-deepseek-cost
cd claude-code-deepseek-cost
python3 install.py          # on Windows: python install.py
```

Start a new Claude Code session and the line appears above the prompt. The
installer copies the two scripts into `~/.claude/` and merges the statusline and
the `SessionStart` hook into `~/.claude/settings.json`. An existing
`settings.json` is backed up first; a statusLine that points at some other tool
is never overwritten (one that already references this tool's script is
refreshed in place), the hook is added at most once, and `--dry-run` shows the
plan before anything is touched. When it recognizes a 1M DeepSeek model it also
ensures the two [context-window](#context-window) keys in whichever settings
file carries the routing. To update later: `git pull && python3 install.py`.

### One-command bootstrap (fresh machines)

To set up a brand-new machine end to end — Claude Code, the DeepSeek routing
config, a `dclaude` launcher, and this meter — run the bootstrap script for
your platform. It is key-free: the DeepSeek API key is read from the
environment or a hidden interactive prompt and stored only in
`~/.config/claude-deepseek/key.env`.

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/hardtomakeanadress/claude-code-deepseek-cost/main/bootstrap/dclaude-bootstrap.ps1 | iex
```

macOS / Linux / WSL:

```sh
curl -fsSL https://raw.githubusercontent.com/hardtomakeanadress/claude-code-deepseek-cost/main/bootstrap/dclaude-bootstrap.sh | bash
```

Re-running is safe; nothing is overwritten without a backup. `-DryRun` /
`--dry-run` prints the plan without touching anything.

On Windows the bootstrap also does the two things newer machines keep failing
on: it puts `%USERPROFILE%\.local\bin` on your user PATH (the native installer
does not, so `claude` is otherwise not found), and it lifts a `Restricted`
execution policy to `RemoteSigned` for the current user so the `dclaude`
function can load from your PowerShell profile.

### For automated installs

`install.py` is non-interactive, prints `ok:` / `warn:` / `err:` lines, exits
non-zero on failure, and is safe to re-run — a second run reports everything as
already present. `--dry-run` prints the planned file copies and settings
changes and touches nothing.

Manual install: copy `deepseek-cost.py` and `statusline.py` into `~/.claude/`
and merge the blocks from `settings.example.json` into
`~/.claude/settings.json`. (`statusline.sh` is the original Bash
implementation, kept as an identical-output fallback for systems with bash + jq.)

## Verify the install

No API calls needed:

```sh
python3 ~/.claude/deepseek-cost.py --tier
# ▽ cheap · 162h42m left      (or: ▲ peak · 2h10m left)

python3 ~/.claude/deepseek-cost.py --version
# v1.2

python3 ~/.claude/deepseek-cost.py --transcript examples/sample.jsonl --report
# total $0.0005 over 1 response (0 rows skipped: model not on the card)
#   peak $0.0000 (0 req)   off-peak $0.0005 (1 req)
#   tokens: miss 1,000  hit 2,000  out 500

echo '{"model":{"display_name":"deepseek-flash"},"workspace":{"current_dir":"/tmp"}}' \
  | python3 ~/.claude/statusline.py
# renders the line for a pretend session
```

`examples/sample.jsonl` is a two-row transcript of one off-peak DeepSeek-flash
response ($0.000456, shown as $0.0005). Its two rows carry the same message id
with growing token counts — exactly how Claude Code records a streamed reply —
and the report collapses them into a single, larger response: a broken dedupe
would print 2 responses. (On a year the holiday list doesn't cover yet, the
report also prints a `note:` line until the list is refreshed — expected.)

## Windows

Native Windows (PowerShell) and WSL both work. Claude Code runs command
strings through Git Bash when it is installed, and through PowerShell when it
is not; the installer writes commands that work under either:

- On Windows the statusline and hook commands use the interpreter actually on
  the machine (`python`, `python3`, or `py`; the Microsoft Store stub counts
  as missing) and a concrete forward-slash path
  (`C:/Users/you/.claude/statusline.py`). If you hand-edit these commands,
  never use `C:\...` backslash paths — Git Bash eats them and the command
  fails without a visible error.
- `claude.exe` lands in `%USERPROFILE%\.local\bin`, and the native installer
  does not add that folder to PATH (it only prints a note about it). The
  bootstrap adds it for you; by hand, append it to the user PATH and open a
  new window.
- PowerShell profiles — and with them the `dclaude` function — do not load
  under the default `Restricted` execution policy. The bootstrap sets
  `RemoteSigned` for the current user when needed; by hand:
  `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`,
  then open a new window.

## Configuration

- **Balance figure** — read from `$DEEPSEEK_API_KEY` when set, otherwise from
  the first line of `~/.config/claude-deepseek/key.env` (everything after the
  first `=`). Without it everything else works; the line reads "balance unavailable".
- **Model label** — `$ANTHROPIC_MODEL` when set, otherwise `env.ANTHROPIC_MODEL`
  in `~/.config/claude-deepseek/settings.json`, otherwise `deepseek-flash`.
- **Prices and holidays** — checked against the sources, not trusted to stay
  hardcoded. Once every two weeks, a session start re-reads
  [DeepSeek's own pricing page](https://api-docs.deepseek.com/quick_start/pricing):
  changed rates are applied from there automatically (they are the authority
  on their prices), a changed peak-hours wording is reported but never
  applied (moving the windows is a person's call), and the holiday mirrors
  are re-fetched — [holiday-cn](https://github.com/NateScarlet/holiday-cn)
  and [timor.tech](https://timor.tech/api/holiday), two machine-readable
  renditions of the State Council's yearly notice, cross-checked against
  each other. `deepseek-cost.py --check-online` runs the visit by hand and
  prints what it saw; findings live in `~/.claude/deepseek-online.json`.
  The built-in `PRICES_PEAK` card and `HOLIDAYS` list stay as the offline
  fallback; `--refresh-holidays` re-fetches the holiday days — the previous,
  current, and next year — and rewrites the list in the file it is run from
  (`--dry-run`: print, don't write). A year both mirrors agree on replaces
  whatever was carried for it (additions and removals alike); a year only
  one answered for is merged day by day. If the holiday list ever falls
  behind the calendar, the session-start line and reports say so
  (`⚠ holidays end 2026 – run --refresh-holidays`) — the meter itself only
  ever shows the tier and the time left.
- **Updates** — the statusline ends with the version you run (`· v1.2`). The
  same two-weekly visit that checks prices reads `version.txt` from this
  repo — the jsDelivr CDN first (reachable from China), GitHub raw second —
  and once it sees a newer version, every session-start line carries
  `⬆ v1.2 on GitHub - git pull && python3 install.py` until you update. The
  check is quiet when offline and never touches the meter. Releasing a
  version: bump `VERSION` in `deepseek-cost.py` and `version.txt` together.

## How the costing works

- Claude Code transcripts (`~/.claude/projects/<project>/<session>.jsonl`)
  record exact token counts for every API response: cache-hit tokens,
  cache-miss tokens, output tokens. These scripts replay those counts against
  DeepSeek's price card.
- A streamed response appears in the transcript several times with growing
  token counts; the largest count seen per message id is used, so every
  response is priced exactly once (see the fixture above).
- `cache_creation` tokens are billed as misses (DeepSeek has no separate
  cache-write line item), and thinking tokens are included in output tokens.
- Peak hours are **01:00–04:00 and 06:00–10:00 UTC, Monday–Friday, excluding
  Chinese public holidays** — the State Council's official days off
  ([2026 notice](https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm)),
  fetched from the cross-checked sources above, never typed in by hand.
  Everything else — nights, weekends, holidays — is off-peak at exactly half
  price. (A report flags any response dated to a year the list has no days
  for, since those days off would price as peak.) Peak rates per 1M tokens,
  re-read from DeepSeek's page and updated if they ever change:

  | model           | cache hit | cache miss | output |
  |-----------------|-----------|------------|--------|
  | deepseek-flash  | $0.006    | $0.30      | $1.20  |
  | deepseek-v4-pro | $0.044    | $1.32      | $3.96  |

- It is an **estimate, not a bill**: it re-prices the token counts the
  transcripts recorded; it does not read DeepSeek's billing system.
- A resumed session keeps the same session id and transcript file (verified),
  so the statusline cost and the context meter simply continue from where they
  were.

## CLI reference

```
deepseek-cost.py --transcript session.jsonl            # USD on stdout
deepseek-cost.py --transcript session.jsonl --report   # human-readable table
deepseek-cost.py --transcript s.jsonl --cache c.json   # incremental, for fast re-runs
deepseek-cost.py --all-projects --report               # everything under ~/.claude/projects
deepseek-cost.py --tier                                # e.g. "▽ cheap · 162h42m left"
deepseek-cost.py --welcome                             # SessionStart hook JSON payload
deepseek-cost.py --refresh-holidays [--dry-run]        # update the holiday list
deepseek-cost.py --check-online                        # re-check rates and holidays now
deepseek-cost.py --version                             # e.g. v1.0
```

## Troubleshooting

- **Statusline blank** — run the two check commands above. If the echo one
  renders a line, the script is fine and the problem is session-side: workspace
  trust must be accepted, and statuslines are suppressed by `disableAllHooks`.
  On Windows, see the Windows section.
- **"balance unavailable"** — no key found, or the balance endpoint was
  unreachable. Check `$DEEPSEEK_API_KEY` or `key.env` (the line needs an `=`).
- **No dollar figure next to the %** — the cost appears once the session has
  its first recorded response.
- **Compaction fires early, around ~200K** — Claude Code assumed a 200K window
  for `deepseek-flash`; see [Context window](#context-window).
- **Garbled colours** — some terminals render ANSI badly when the statusline
  overlaps other UI updates; the line is deliberately single-line.

## Uninstall

Delete `~/.claude/statusline.py`, `~/.claude/deepseek-cost.py` and
`~/.claude/deepseek-online.json`, and remove the `statusLine` and
`hooks.SessionStart` entries plus any `CLAUDE_CODE_MAX_CONTEXT_TOKENS` /
`CLAUDE_CODE_AUTO_COMPACT_WINDOW` keys the installer added (see
[Context window](#context-window)) — or restore the `settings.json.bak-<timestamp>`
the installer wrote for each file it touched.

## License

MIT — see [LICENSE](LICENSE).
