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
foreach ($candidate in @("python", "python3", "py")) {
    $c = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $c) { continue }
    # A WindowsApps alias is a real Python only when a Store-installed one
    # answers; the bare stub would open the Microsoft Store instead.
    if ("$($c.Source)" -like "*\WindowsApps\*") {
        $probe = & $candidate -c "print(1)" 2>$null
        if ("$probe".Trim() -ne "1") { continue }
    }
    $py = $candidate
    break
}

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
}

# The native installer drops claude.exe in %USERPROFILE%\.local\bin and does
# NOT add that folder to PATH itself (it only prints a note about it), so
# `claude` - and therefore `dclaude` - would not be found in new windows.
# The raw registry value is edited in place, keeping its type, so %VAR%
# entries elsewhere in the PATH stay expandable instead of being frozen.
$localBin = Join-Path $HOME ".local\bin"
$alreadyOnPath = @([Environment]::ExpandEnvironmentVariables(
        [string][Environment]::GetEnvironmentVariable("Path", "User")) -split ";" |
    Where-Object { $_ -ne "" }) -contains $localBin
if (-not $alreadyOnPath) {
    if ($DryRun) {
        Say "would add $localBin to the user PATH"
    } else {
        $written = $false
        try {
            $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $true)
            $raw = $key.GetValue("Path", $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            $kind = [Microsoft.Win32.RegistryValueKind]::ExpandString
            if ($null -ne $raw) { $kind = $key.GetValueKind("Path") }
            if ($null -eq $raw -or "$raw".Trim() -eq "") { $raw = $localBin }
            else { $raw = "$raw".TrimEnd(";") + ";" + $localBin }
            $key.SetValue("Path", $raw, $kind)
            $key.Close()
            $written = $true
        } catch {
            Warn "could not edit the user PATH in the registry ($_); using the standard method"
        }
        if (-not $written) {
            $fallbackPath = [string][Environment]::GetEnvironmentVariable("Path", "User")
            [Environment]::SetEnvironmentVariable("Path", ($fallbackPath.TrimEnd(";") + ";" + $localBin), "User")
        } else {
            # tell Explorer a variable changed, so new windows pick it up now
            try {
                if (-not ("Native.EnvRefresh" -as [type])) {
                    Add-Type -Namespace Native -Name EnvRefresh -MemberDefinition @'
[DllImport("user32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, IntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out IntPtr lpdwResult);
'@
                }
                $result = [IntPtr]::Zero
                [Native.EnvRefresh]::SendMessageTimeout([IntPtr]0xFFFF, 0x1A, [IntPtr]::Zero, "Environment", 0x0002, 5000, [ref]$result) | Out-Null
            } catch { Warn "PATH updated, but new windows may need a sign-out to see it" }
        }
        Ok "added $localBin to the user PATH (new windows)"
    }
}
if (-not $DryRun -and ($env:Path -split ";") -notcontains $localBin) {
    $env:Path = "$env:Path;$localBin"   # reachable in this window too
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
    if (-not $k) { $k = $env:DEEPSEEK_API_KEY }
    if (-not $k) { Write-Host "dclaude: no key in $f and no DEEPSEEK_API_KEY set" -ForegroundColor Red; return }
    $env:ANTHROPIC_AUTH_TOKEN = $k
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        claude --settings (Join-Path $d 'settings.json') @args
    } else {
        $fallback = Join-Path $HOME '.local\bin\claude.exe'
        if (Test-Path $fallback) {
            & $fallback --settings (Join-Path $d 'settings.json') @args
        } else {
            Write-Host "dclaude: claude not found - install it (irm https://claude.ai/install.ps1 | iex), then open a new window" -ForegroundColor Red
        }
    }
}
# <<< dclaude wrapper <<<
'@

# current host + all-hosts, and the sibling host's all-hosts file when it
# exists (Windows PowerShell 5.1 and pwsh 7 keep separate profile folders)
$docs = [Environment]::GetFolderPath("MyDocuments")
$profileTargets = @($PROFILE, $PROFILE.CurrentUserAllHosts)
foreach ($hostDir in @((Join-Path $docs "WindowsPowerShell"), (Join-Path $docs "PowerShell"))) {
    if (Test-Path $hostDir) { $profileTargets += (Join-Path $hostDir "profile.ps1") }
}
$profileTargets = $profileTargets | Select-Object -Unique
$markerOpen  = '# >>> dclaude wrapper >>>'
$markerClose = '# <<< dclaude wrapper <<<'
foreach ($p in $profileTargets) {
    $dir = Split-Path -Parent $p
    if (-not $DryRun -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $current = ""
    if (Test-Path $p) {
        $current = Get-Content -Raw -Path $p
        if ($null -eq $current) { $current = "" }   # empty file
    }
    if ($current.Contains($wrapperSnippet)) {
        Ok "dclaude function already present in $p"
    } elseif ($DryRun) {
        if ($current -match [regex]::Escape($markerOpen)) { Say "would refresh the dclaude function in $p" }
        else { Say "would append dclaude function to $p" }
    } elseif ($current -match [regex]::Escape($markerOpen)) {
        # an older wrapper is there - replace that marked block in place,
        # keeping the file's own line endings outside it and its encoding
        $pattern = "(?s)\r?\n?" + [regex]::Escape($markerOpen) + ".*?" + [regex]::Escape($markerClose)
        $refreshed = [regex]::Replace($current, $pattern, { param($m) "`n" + $wrapperSnippet })
        $bytes = [System.IO.File]::ReadAllBytes($p)
        $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
        $enc = [System.Text.Encoding]::Default
        if ($hasBom) { $enc = New-Object System.Text.UTF8Encoding($true) }
        [System.IO.File]::WriteAllText($p, $refreshed, $enc)
        Ok "dclaude function updated in $p"
    } else {
        Add-Content -Path $p -Value ("`n" + $wrapperSnippet)
        Ok "dclaude function added to $p"
    }
}

# Windows PowerShell's default policy (Restricted) silently skips profile
# files - the dclaude function would never load. Judge the policy a NORMAL
# window would resolve (a -ExecutionPolicy Bypass run does not tell us), and
# set RemoteSigned for the current user when that resolution blocks profiles.
$machinePolicy = Get-ExecutionPolicy -Scope MachinePolicy
$userPolicy    = Get-ExecutionPolicy -Scope UserPolicy
$currentUser   = Get-ExecutionPolicy -Scope CurrentUser
$localMachine  = Get-ExecutionPolicy -Scope LocalMachine
$wouldBe = "Restricted"      # the client default when nothing is set
foreach ($v in @($machinePolicy, $userPolicy, $currentUser, $localMachine)) {
    if ($v -and $v -ne "Undefined") { $wouldBe = $v; break }
}
if ($wouldBe -eq "Restricted" -or $wouldBe -eq "AllSigned") {
    if ($DryRun) {
        Say "execution policy is $wouldBe - would set it to RemoteSigned for the current user"
    } else {
        try {
            Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
            Ok "execution policy was $wouldBe - set to RemoteSigned so profiles can load"
        } catch {
            Warn "execution policy is $wouldBe and could not be changed automatically: $_"
            Warn "run once, then open a new window: Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned"
        }
    }
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
$cmd = Get-Command claude -ErrorAction SilentlyContinue
$exe = $null
if ($cmd) { $exe = $cmd.Source }
if (-not $exe) {
    $fallback = Join-Path $HOME ".local\bin\claude.exe"
    if (Test-Path $fallback) { $exe = $fallback }
}
if ($exe) { Ok "claude: $exe" } else { Warn "claude not found on PATH - dclaude will not run until it is" }

if (Test-Path $KeyEnv) { Ok "key:    $KeyEnv" } else { Warn "no key.env - dclaude will not find a key" }

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

If 'dclaude' is not recognized in the new window, the profile did not
load - run: Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
and open another window.
'@
