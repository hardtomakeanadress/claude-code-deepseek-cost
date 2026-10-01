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
time in the session · billing tier — green `▽ cheap` or red `▲ peak`, with the
time left in the current window.

**Session-start cost check** — one line when a chat starts, resumes, or clears:

```
deepseek-flash — $18.42 left · $6.58 spent so far · ▽ cheap · 162h42m left
```

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

## Install

```sh
git clone https://github.com/hardtomakeanadress/claude-code-deepseek-cost
cd claude-code-deepseek-cost
python3 install.py          # on Windows: python install.py
```

Start a new Claude Code session and the line appears above the prompt. The
installer copies the two scripts into `~/.claude/` and merges the statusline and
the `SessionStart` hook into `~/.claude/settings.json`. An existing
`settings.json` is backed up first, an existing *different* statusLine is never
overwritten, the hook is added at most once, and `--dry-run` shows the plan
before anything is touched. To update later: `git pull && python3 install.py`.

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
would print 2 responses.

## Windows

Claude Code runs command strings through Git Bash when it is installed, and
through PowerShell when it is not — this works under either, but note:

- Use `python` instead of `python3`; python.org installs provide `python`.
  (The installer writes whichever interpreter it finds.)
- The commands use `$HOME`, which both Git Bash and PowerShell expand. If you
  hand-edit them, don't write literal `C:\...` paths: Git Bash treats
  backslashes as escape characters and the command fails without a visible
  error. Forward slashes work everywhere.

## Configuration

- **Balance figure** — read from `$DEEPSEEK_API_KEY` when set, otherwise from
  the first line of `~/.config/claude-deepseek/key.env` (everything after the
  first `=`). Without it everything else works; the line reads "balance unavailable".
- **Model label** — `$ANTHROPIC_MODEL` when set, otherwise `env.ANTHROPIC_MODEL`
  in `~/.config/claude-deepseek/settings.json`, otherwise `deepseek-flash`.
- **Prices and holidays** — when DeepSeek changes rates, edit `PRICES_PEAK` at
  the top of `deepseek-cost.py` (retired model names map through `ALIASES`).
  The `HOLIDAYS` set holds the 2026 Chinese public holidays (State Council
  notice); refresh it when the next notice is published.

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
  Chinese public holidays**. Everything else — nights, weekends, holidays — is
  off-peak at exactly half price. Peak rates per 1M tokens:

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
- **Garbled colours** — some terminals render ANSI badly when the statusline
  overlaps other UI updates; the line is deliberately single-line.

## Uninstall

Delete `~/.claude/statusline.py` and `~/.claude/deepseek-cost.py`, and remove
the `statusLine` and `hooks.SessionStart` entries from `~/.claude/settings.json`
— or restore the `settings.json.bak-<timestamp>` the installer wrote.

## License

MIT — see [LICENSE](LICENSE).
