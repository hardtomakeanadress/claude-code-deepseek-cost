#!/usr/bin/env bash
# dclaude-bootstrap.sh — one-shot installer for the DeepSeek-backed Claude Code setup.
#
# Installs, in order:
#   1. Claude Code CLI (official installer, npm fallback)
#   2. DeepSeek routing  -> ~/.config/claude-deepseek/{settings.json,key.env}
#   3. the `dclaude` shell wrapper (zsh or bash rc file)
#   4. the cost meter     -> git clone + python3 install.py
#
# Usage:
#   bash dclaude-bootstrap.sh [--key SK-...] [--key-file PATH] [--no-meter] [--dry-run]
#
# The key comes from --key, then --key-file, then $DEEPSEEK_API_KEY, then an
# existing ~/.config/claude-deepseek/key.env (kept as-is), then an interactive
# prompt. Re-running is safe; nothing is overwritten without a backup.

set -euo pipefail

REPO_URL="https://github.com/hardtomakeanadress/claude-code-deepseek-cost"
REPO_DIR="$HOME/claude-code-deepseek-cost"
CONF_DIR="$HOME/.config/claude-deepseek"
KEY_ENV="$CONF_DIR/key.env"
SETTINGS="$CONF_DIR/settings.json"

KEY_ARG=""; KEY_FILE=""; NO_METER=false; DRY=false

ok()   { printf 'ok:   %s\n' "$*"; }
warn() { printf 'warn: %s\n' "$*" >&2; }
err()  { printf 'err:  %s\n' "$*" >&2; }
run()  { if $DRY; then printf '  [dry-run] %s\n' "$*"; else "$@"; fi; }

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --key)       KEY_ARG="${2:?--key needs a value}"; shift 2 ;;
    --key=*)     KEY_ARG="${1#*=}"; shift ;;
    --key-file)  KEY_FILE="${2:?--key-file needs a path}"; shift 2 ;;
    --key-file=*) KEY_FILE="${1#*=}"; shift ;;
    --no-meter)  NO_METER=true; shift ;;
    --dry-run)   DRY=true; shift ;;
    -h|--help)   usage; exit 0 ;;
    *)           err "unknown argument: $1"; usage; exit 2 ;;
  esac
done

case "$(uname -s)" in
  Darwin) OS=Darwin ;;
  Linux)  OS=Linux ;;
  *) err "unsupported OS '$(uname -s)' - on Windows, run this inside WSL."; exit 1 ;;
esac

# ---------------------------------------------------------------- prerequisites
# Best-effort: try to install missing git/python3 with the platform's tooling.
ensure_tool() {
  command -v "$1" >/dev/null 2>&1 && return 0
  warn "$1 not found - attempting install"
  if $DRY; then printf '  [dry-run] would install %s\n' "$1"; return 0; fi
  if [ "$OS" = Darwin ]; then
    if command -v brew >/dev/null 2>&1; then brew install "$1" || true
    elif [ "$1" = python3 ]; then xcode-select --install || true
    fi
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq && sudo apt-get install -y "$1" || true
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y "$1" || true
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm "$1" || true
  fi
  command -v "$1" >/dev/null 2>&1
}

ensure_tool git || warn "git unavailable - cost-meter steps will be skipped"
if command -v python3 >/dev/null 2>&1; then
  :
elif [ "$OS" = Linux ]; then
  ensure_tool python3 || warn "python3 unavailable - cost meter will be skipped"
else
  warn "python3 unavailable (macOS: 'xcode-select --install' provides it) - cost meter will be skipped"
fi

# ---------------------------------------------------------------- 1. Claude Code
if command -v claude >/dev/null 2>&1; then
  ok "Claude Code already installed"
elif $DRY; then
  printf '  [dry-run] would run: curl -fsSL https://claude.ai/install.sh | bash\n'
else
  ok "installing Claude Code (official installer)"
  if ! curl -fsSL https://claude.ai/install.sh | bash; then
    warn "curl installer failed - trying npm"
    if command -v npm >/dev/null 2>&1; then
      npm install -g @anthropic-ai/claude-code || { err "npm install failed"; exit 1; }
    else
      err "no npm either - install Claude Code manually: https://claude.com/claude-code"
      exit 1
    fi
  fi
  export PATH="$HOME/.local/bin:$PATH"
fi

# ---------------------------------------------------------------- 2. routing config
mkdir_p() { if $DRY; then printf '  [dry-run] mkdir -p %s\n' "$1"; else mkdir -p "$1"; fi; }
mkdir_p "$CONF_DIR"

# key resolution: --key > --key-file > $DEEPSEEK_API_KEY > existing key.env > prompt
KEY=""; EXISTING_KEY=false
if [ -n "$KEY_ARG" ]; then
  KEY="$KEY_ARG"
elif [ -n "$KEY_FILE" ]; then
  if [ -f "$KEY_FILE" ]; then
    KEY="$(sed -n 's/^DEEPSEEK_API_KEY=//p' "$KEY_FILE" | head -1)"
    [ -n "$KEY" ] || KEY="$(grep -m1 -v '^[[:space:]]*$' "$KEY_FILE" | tr -d '[:space:]')"
  else
    err "key file not found: $KEY_FILE"; exit 1
  fi
  [ -n "$KEY" ] || { err "could not read a key from $KEY_FILE"; exit 1; }
elif [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  KEY="$DEEPSEEK_API_KEY"
elif [ -f "$KEY_ENV" ]; then
  ok "existing key.env found - kept as-is"
  EXISTING_KEY=true
elif $DRY; then
  printf '  [dry-run] would prompt for the DeepSeek API key and write %s\n' "$KEY_ENV"
else
  if [ -r /dev/tty ]; then
    read -rsp "DeepSeek API key: " KEY < /dev/tty || true
    echo
  fi
  [ -n "$KEY" ] || { err "no key provided and no TTY to prompt - pass --key or --key-file"; exit 1; }
fi

if [ -n "$KEY" ]; then
  if $DRY; then
    printf '  [dry-run] would write %s (chmod 600)\n' "$KEY_ENV"
  else
    printf 'DEEPSEEK_API_KEY=%s\n' "$KEY" > "$KEY_ENV"
    chmod 600 "$KEY_ENV"
    ok "key written to $KEY_ENV"
  fi
fi

# settings.json: write when missing or different; back up anything replaced
TMP="$(mktemp)"; trap 'rm -f "$TMP"' EXIT
cat > "$TMP" <<'EOF'
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_MODEL": "deepseek-flash",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-flash",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-flash",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-flash",
    "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-flash",
    "CLAUDE_CODE_EFFORT_LEVEL": "max",
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "786432",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "1000000"
  }
}
EOF

if [ -f "$SETTINGS" ] && cmp -s "$TMP" "$SETTINGS"; then
  ok "settings.json already up to date"
elif $DRY; then
  printf '  [dry-run] would write %s%s\n' "$SETTINGS" \
    "$([ -f "$SETTINGS" ] && printf ' (backing up the existing one first)' || printf '')"
else
  if [ -f "$SETTINGS" ]; then
    cp -p "$SETTINGS" "$SETTINGS.bak-$(date +%Y%m%d-%H%M%S)"
    warn "existing settings.json backed up"
  fi
  cp "$TMP" "$SETTINGS"
  chmod 600 "$SETTINGS"
  ok "wrote $SETTINGS"
fi

# ---------------------------------------------------------------- 3. dclaude wrapper
case "$(basename "${SHELL:-sh}")" in
  zsh)  RC="$HOME/.zshrc" ;;
  bash) if [ "$OS" = Darwin ] && [ -f "$HOME/.bash_profile" ]; then RC="$HOME/.bash_profile"
        else RC="$HOME/.bashrc"; fi ;;
  *)    RC="" ;;
esac

WRAPPER_SNIPPET='# >>> dclaude wrapper >>>
dclaude() {
    local d="$HOME/.config/claude-deepseek"
    local k
    k=$(sed -n '"'"'s/^DEEPSEEK_API_KEY=//p'"'"' "$d/key.env" 2>/dev/null)
    if [ -z "$k" ]
    then
        echo "dclaude: no key in $d/key.env" >&2
        return 1
    fi
    ANTHROPIC_AUTH_TOKEN="$k" claude --settings "$d/settings.json" "$@"
}
# <<< dclaude wrapper <<<'

if [ -z "$RC" ]; then
  warn "unrecognized login shell '${SHELL:-?}' - add this block to your rc file by hand:"
  printf '%s\n' "$WRAPPER_SNIPPET"
elif grep -q '>>> dclaude wrapper >>>' "$RC" 2>/dev/null; then
  ok "dclaude wrapper already present in $RC"
elif $DRY; then
  printf '  [dry-run] would append dclaude wrapper to %s\n' "$RC"
else
  printf '\n%s\n' "$WRAPPER_SNIPPET" >> "$RC"
  ok "dclaude wrapper added to $RC"
fi

# the wrapper calls plain `claude`; make sure it will be on PATH in new shells
if ! command -v claude >/dev/null 2>&1 && [ -x "$HOME/.local/bin/claude" ]; then
  export PATH="$HOME/.local/bin:$PATH"
  if [ -n "$RC" ] && ! grep -q '>>> claude local bin >>>' "$RC" 2>/dev/null && ! $DRY; then
    printf '\n# >>> claude local bin >>>\nexport PATH="$HOME/.local/bin:$PATH"\n# <<< claude local bin <<<\n' >> "$RC"
    ok "added ~/.local/bin to PATH in $RC"
  fi
fi

# ---------------------------------------------------------------- 4. cost meter
if $NO_METER; then
  ok "--no-meter: skipping the cost meter"
elif ! command -v python3 >/dev/null 2>&1; then
  warn "python3 missing - cost meter skipped; install it and re-run this script"
elif ! command -v git >/dev/null 2>&1; then
  warn "git missing - cost meter skipped; install it and re-run this script"
else
  if [ -d "$REPO_DIR/.git" ]; then
    run git -C "$REPO_DIR" pull --ff-only
  elif $DRY; then
    printf '  [dry-run] would clone %s -> %s\n' "$REPO_URL" "$REPO_DIR"
  else
    git clone "$REPO_URL" "$REPO_DIR"
  fi
  run python3 "$REPO_DIR/install.py"
fi

# ---------------------------------------------------------------- 5. verify
echo
if [ -f "$HOME/.claude/deepseek-cost.py" ]; then
  printf 'version: '; python3 "$HOME/.claude/deepseek-cost.py" --version 2>/dev/null || echo '(unavailable)'
  printf 'tier:    '; python3 "$HOME/.claude/deepseek-cost.py" --tier 2>/dev/null || echo '(unavailable)'
fi

echo
ok "bootstrap complete"
cat <<'EOF'

Next steps:
  1. Open a new terminal (or run: exec "$SHELL")
  2. Run:  dclaude
     (the first launch may show a one-time theme/onboarding prompt;
      it will NOT ask you to log in to Anthropic - traffic goes to DeepSeek)
EOF
