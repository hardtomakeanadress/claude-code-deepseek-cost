# claude-code-deepseek-cost

Real DeepSeek spend for Claude Code. Claude Code prices sessions from an
Anthropic rate card and reads 50–100× too high when you actually run on
DeepSeek; this re-prices your transcripts at DeepSeek's own rates, live in
the statusline.

## Install

**macOS**

```sh
curl -fsSL https://raw.githubusercontent.com/hardtomakeanadress/claude-code-deepseek-cost/main/bootstrap/dclaude-bootstrap.sh | bash
```

**Linux**

```sh
curl -fsSL https://raw.githubusercontent.com/hardtomakeanadress/claude-code-deepseek-cost/main/bootstrap/dclaude-bootstrap.sh | bash
```

**Windows** (PowerShell)

```powershell
irm https://raw.githubusercontent.com/hardtomakeanadress/claude-code-deepseek-cost/main/bootstrap/dclaude-bootstrap.ps1 | iex
```

Each one installs Claude Code, routes it to DeepSeek, adds the `dclaude`
command, and sets up the cost meter; it asks for your DeepSeek API key once.
Then open a new terminal and run `dclaude`. Re-running the same command
updates everything.

The statusline shows this package's version. Account balance refreshes in the
background whenever recorded session spend changes (also once a minute while
idle and redrawing). It uses DeepSeek's separate balance endpoint; a balance
older than two minutes is marked `stale` if lookups fail.

At interactive startup, the launcher checks for a newer package version.
Press **1** to pull and install the update, then continue into chat; press
**Enter** to skip. Offline checks and noninteractive commands continue without
prompting. Updates preserve local checkout changes by refusing to overwrite
them. After upgrading from an older release, open a new terminal once so the
shell loads the updated launcher.

MIT — see [LICENSE](LICENSE).
