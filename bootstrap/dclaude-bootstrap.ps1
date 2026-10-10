#Requires -Version 5.1
<#
dclaude-bootstrap.ps1 - one-shot installer for the DeepSeek-backed Claude Code setup on Windows.

Installs, in order:
  1. Claude Code CLI        (official installer, npm fallback)
  2. DeepSeek routing       -> $HOME\.config\claude-deepseek\{settings.json,key.env}
  3. the `dclaude` function -> your PowerShell profile
  4. the cost meter         -> git clone + python install.py

Usage:
  powershell -ExecutionPolicy Bypass -File dclaude-bootstrap.ps1 [-Key SK-...] [-KeyFile PATH] [-NoMeter] [-DryRun]

The key comes from -Key, then -KeyFile, then $env:DEEPSEEK_API_KEY, then an
existing key.env (kept as-is), then a secure prompt. Re-running is safe;
nothing is overwritten without a backup. This script never contains or
prints the key; it is stored only in key.env with the ACL locked to you.
#>

param(
    [string]$Key = "",
    [string]$KeyFile = "",
    [switch]$NoMeter,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}

function Ok   ($m) { Write-Host "ok:   $m" }
function Warn ($m) { Write-Host "warn: $m" -ForegroundColor Yellow }
function Err  ($m) { Write-Host "err:  $m" -ForegroundColor Red }
function Say  ($m) { Write-Host "  [dry-run] $m" -ForegroundColor DarkGray }

$RepoUrl  = "https://github.com/hardtomakeanadress/claude-code-deepseek-cost"
$RepoDir  = Join-Path $HOME "claude-code-deepseek-cost"
$ConfDir  = Join-Path $HOME ".config\claude-deepseek"
$KeyEnv   = Join-Path $ConfDir "key.env"
$Settings = Join-Path $ConfDir "settings.json"

# ---------------------------------------------------------------- prerequisites
$git = Get-Command git -ErrorAction SilentlyContinue
$py  = $null
if     (Get-Command python -ErrorAction SilentlyContinue) { $py = "python" }
elseif (Get-Command py     -ErrorAction SilentlyContinue) { $py = "py" }

if (-not $git) { Warn "git not found - install 'Git for Windows' (git-scm.com) and re-run; cost-meter steps will be skipped" }
if (-not $py)  { Warn "python not found - install from python.org (tick 'Add python.exe to PATH') and re-run; the cost meter will be skipped" }

# ---------------------------------------------------------------- 1. the key
if ($Key) {
    # given via -Key
} elseif ($KeyFile) {
    if (-not (Test-Path $KeyFile)) { Err "key file not found: $KeyFile"; exit 1 }
    $line = Get-Content $KeyFile | Where-Object { $_ -match '^\s*DEEPSEEK_API_KEY=' } | Select-Object -First 1
    if ($line) { $Key = ($line -replace '^\s*DEEPSEEK_API_KEY=', '').Trim() }
    else {
        $first = Get-Content $KeyFile | Where-Object { $_.Trim() -ne "" } | Select-Object -First 1
        if ($first) { $Key = $first.Trim() }
    }
    if (-not $Key) { Err "could not read a key from $KeyFile"; exit 1 }
} elseif ($env:DEEPSEEK_API_KEY) {
    $Key = $env:DEEPSEEK_API_KEY
} elseif (Test-Path $KeyEnv) {
    Ok "existing key.env found - kept as-is"
} elseif ($DryRun) {
    Say "would prompt for the DeepSeek API key and write $KeyEnv"
} else {
    $sec = Read-Host -Prompt "DeepSeek API key" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try     { $Key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    if (-not $Key) { Err "no key provided"; exit 1 }
}

if (-not $DryRun -and -not (Test-Path $ConfDir)) { New-Item -ItemType Directory -Force -Path $ConfDir | Out-Null }

if ($Key) {
    if ($DryRun) {
        Say "would write $KeyEnv (ACL locked to $($env:USERNAME))"
    } else {
        Set-Content -Path $KeyEnv -Value ("DEEPSEEK_API_KEY=" + $Key) -Encoding ascii -NoNewline
        & icacls $KeyEnv /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
        if ($LASTEXITCODE -ne 0) { Warn "could not tighten ACL on key.env" }
        Ok "key written to $KeyEnv"
    }
}

# ---------------------------------------------------------------- 2. routing config
$settingsText = @'
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
'@

$writeSettings = $true
if (Test-Path $Settings) {
    $old = Get-Content -Raw $Settings
    if ($old.Trim() -eq $settingsText.Trim()) { Ok "settings.json already up to date"; $writeSettings = $false }
}
if ($writeSettings) {
    if ($DryRun) {
        Say "would write $Settings"
    } else {
        if (Test-Path $Settings) {
            Copy-Item $Settings ($Settings + ".bak-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
            Warn "existing settings.json backed up"
        }
        Set-Content -Path $Settings -Value $settingsText -Encoding ascii
        Ok "wrote $Settings"
    }
}

# ---------------------------------------------------------------- 3. Claude Code
if (Get-Command claude -ErrorAction SilentlyContinue) {
    Ok "Claude Code already installed"
} elseif ($DryRun) {
    Say "would run: irm https://claude.ai/install.ps1 | iex"
} else {
    Ok "installing Claude Code (official installer)"
    $installed = $true
    try { Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression }
    catch { $installed = $false }
    if (-not $installed) {
        Warn "official installer failed - trying npm"
        if (Get-Command npm -ErrorAction SilentlyContinue) {
            npm install -g "@anthropic-ai/claude-code"
            if ($LASTEXITCODE -ne 0) { Err "npm install failed"; exit 1 }
        } else {
            Err "no npm either - install Claude Code manually: https://claude.com/claude-code"
            exit 1
        }
    }
    # pick up the PATH the installer just changed, without needing a new window
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
}

# ---------------------------------------------------------------- 4. dclaude function
$wrapperSnippet = @'
# >>> dclaude wrapper >>>
function dclaude {
    $d = Join-Path $HOME '.config\claude-deepseek'
    $f = Join-Path $d 'key.env'
    $k = $null
    if (Test-Path $f) {
        $line = Select-String -Path $f -Pattern '^DEEPSEEK_API_KEY=' | Select-Object -First 1
        if ($line) { $k = $line.Line.Substring('DEEPSEEK_API_KEY='.Length).Trim() }
    }
    if (-not $k) { Write-Host "dclaude: no key in $f" -ForegroundColor Red; return }
    $env:ANTHROPIC_AUTH_TOKEN = $k
    claude --settings (Join-Path $d 'settings.json') @args
}
# <<< dclaude wrapper <<<
'@

$profileTargets = @($PROFILE, $PROFILE.CurrentUserAllHosts) | Select-Object -Unique
foreach ($p in $profileTargets) {
    $dir = Split-Path -Parent $p
    if (-not $DryRun -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    if ((Test-Path $p) -and (Select-String -Path $p -Pattern '>>> dclaude wrapper >>>' -Quiet)) {
        Ok "dclaude function already present in $p"
    } elseif ($DryRun) {
        Say "would append dclaude function to $p"
    } else {
        Add-Content -Path $p -Value ("`n" + $wrapperSnippet)
        Ok "dclaude function added to $p"
    }
}

if ((Get-ExecutionPolicy) -eq "Restricted") {
    Warn "PowerShell execution policy is Restricted - profiles won't load. Run this once: Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned"
}

# ---------------------------------------------------------------- 5. cost meter
if ($NoMeter) {
    Ok "-NoMeter: skipping the cost meter"
} elseif (-not $git) {
    Warn "git missing - cost meter skipped; install Git for Windows and re-run this script"
} elseif (-not $py) {
    Warn "python missing - cost meter skipped; install Python from python.org and re-run this script"
} else {
    if (Test-Path (Join-Path $RepoDir ".git")) {
        if ($DryRun) { Say "would run: git -C $RepoDir pull --ff-only" } else { git -C $RepoDir pull --ff-only }
    } elseif ($DryRun) {
        Say "would clone $RepoUrl -> $RepoDir"
    } else {
        git clone $RepoUrl $RepoDir
    }
    if ($DryRun) { Say "would run: $py $(Join-Path $RepoDir 'install.py')" }
    else         { & $py (Join-Path $RepoDir "install.py") }
}

# ---------------------------------------------------------------- 6. verify
Write-Host ""
$meter = Join-Path $HOME ".claude\deepseek-cost.py"
if ((Test-Path $meter) -and $py) {
    try {
        Write-Host -NoNewline "version: "; & $py $meter --version
        Write-Host -NoNewline "tier:    "; & $py $meter --tier
    } catch { Warn "could not run the cost meter yet: $_" }
}

Write-Host ""
Ok "bootstrap complete"
Write-Host @'

Next steps:
  1. Open a new PowerShell window (the profile loads on start)
  2. Run:  dclaude
     (first launch may show a one-time theme/onboarding prompt; it will
      NOT ask you to log in to Anthropic - traffic goes to DeepSeek)
'@
