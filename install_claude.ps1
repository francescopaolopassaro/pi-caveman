# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
#
# Synthelion installer for Claude Code — Windows PowerShell
#
# Usage:
#   .\install_claude.ps1              # install
#   .\install_claude.ps1 -Upgrade     # update to latest version
#   .\install_claude.ps1 -NoHook      # install without auto-compression hook
#   .\install_claude.ps1 -Uninstall   # remove everything
#
# Run with: powershell -ExecutionPolicy Bypass -File install_claude.ps1

param(
    [switch]$Upgrade,
    [switch]$NoHook,
    [switch]$Uninstall,
    [switch]$NoPip,
    [switch]$Chromadb
)

$ErrorActionPreference = "Stop"

# ── colours ──────────────────────────────────────────────────────────────────
function Ok($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Info($msg) { Write-Host "   --> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "   !   $msg" -ForegroundColor Yellow }
function Err($msg)  { Write-Host "  [X] $msg" -ForegroundColor Red }
function H1($msg)   { Write-Host "`n$msg" -ForegroundColor Cyan }
function H2($msg)   { Write-Host "`n$msg" -ForegroundColor White }

# ── settings.json path ────────────────────────────────────────────────────────
$SettingsPath = Join-Path $env:USERPROFILE ".claude\settings.json"

function Load-Settings {
    if (Test-Path $SettingsPath) {
        try {
            return Get-Content $SettingsPath -Raw | ConvertFrom-Json
        } catch {
            Warn "settings.json has invalid JSON — backing up and recreating."
            Move-Item $SettingsPath "$SettingsPath.bak" -Force
        }
    }
    return [PSCustomObject]@{}
}

function Backup-Settings {
    # Copy an existing settings file to a timestamped .bak-<ts> before we modify
    # it, so a working config always has a restore point. A failed backup must
    # never abort the install.
    if (Test-Path $SettingsPath) {
        $ts = Get-Date -Format "yyyyMMdd-HHmmss"
        $bak = "$SettingsPath.bak-$ts"
        try {
            Copy-Item -LiteralPath $SettingsPath -Destination $bak -Force
            Ok "Backed up existing settings → $bak"
        } catch { }
    }
}

function Save-Settings($data) {
    $dir = Split-Path $SettingsPath
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    # Write to a temp sibling then atomically replace, so an interrupted or
    # concurrent write can never leave a truncated settings.json.
    $tmp = "$SettingsPath.tmp-$PID"
    $data | ConvertTo-Json -Depth 10 | Set-Content $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $SettingsPath -Force
    Ok "Saved → $SettingsPath"
}

# ── find synthelion-mcp ───────────────────────────────────────────────────────
function Find-McpBinary {
    # 1. Scripts folder next to python.exe
    $pythonDir = Split-Path (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($pythonDir) {
        # Check same dir as python.exe (virtualenv: python.exe lives in Scripts\)
        $candidate = Join-Path $pythonDir "synthelion-mcp.exe"
        if (Test-Path $candidate) { return $candidate }
        # Check Scripts\ subfolder (system install: python.exe lives in root)
        $candidate = Join-Path $pythonDir "Scripts\synthelion-mcp.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    # 2. PATH
    $found = Get-Command synthelion-mcp -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    # 3. Common pip install locations
    $appdata = $env:APPDATA
    foreach ($ver in @("Python313","Python312","Python311")) {
        $candidate = "$appdata\Python\$ver\Scripts\synthelion-mcp.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Find-CliSynthelion {
    $found = Get-Command synthelion -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    $mcp = Find-McpBinary
    if ($mcp) { return $mcp -replace "synthelion-mcp.exe","synthelion.exe" }
    return "synthelion"
}

# ── build hook command ────────────────────────────────────────────────────────
function Build-HookCommand($cliBin) {
    $cli = if ($cliBin) { $cliBin } else { "synthelion" }
    # All formatting (savings label, PII/privacy breakdown, AI Act notice)
    # happens inside `synthelion compress --json` itself (see cli.py's
    # `notice` field) — this hook only branches on `blocked`:
    #   - blocked   -> decision: "block" with reason=$r.notice (full PII
    #                  breakdown + AI Act notice), prompt never reaches Claude.
    #   - otherwise -> systemMessage=$r.notice (visible in the terminal) +
    #                  additionalContext=$r.compressed (invisible to the
    #                  terminal, but that's the actual compressed prompt Claude reads).
    return @"
`$j=[Console]::In.ReadToEnd()|ConvertFrom-Json;`$p=`$j.prompt;if(`$p){`$r=(`$p| & "$cli" compress --json 2>`$null)|ConvertFrom-Json;if(`$r -and `$r.blocked){@{decision='block';reason=`$r.notice}|ConvertTo-Json -Compress}elseif(`$r -and `$r.efficiency_pct -gt 15){@{systemMessage=`$r.notice;hookSpecificOutput=@{hookEventName='UserPromptSubmit';additionalContext=`$r.compressed}}|ConvertTo-Json -Compress}}
"@
}

# ── configure Claude Code ─────────────────────────────────────────────────────
function Configure-Claude($mcpBin, $addHook) {
    H2 "Configuring Claude Code…"
    Info "Settings: $SettingsPath"

    $s = Load-Settings
    Backup-Settings

    # Ensure mcpServers exists
    if (-not (Get-Member -InputObject $s -Name "mcpServers" -MemberType NoteProperty)) {
        $s | Add-Member -MemberType NoteProperty -Name "mcpServers" -Value ([PSCustomObject]@{})
    }

    # Set synthelion MCP config
    if ($mcpBin) {
        $mcpCfg = [PSCustomObject]@{ command = $mcpBin }
        Info "Using binary: $mcpBin"
    } else {
        $py = (Get-Command python -ErrorAction SilentlyContinue).Source
        if (-not $py) { $py = "python" }
        $mcpCfg = [PSCustomObject]@{
            command = $py
            args    = @("-m", "synthelion.plugins.mcp_server")
        }
        Info "Binary not found — using Python module form"
    }
    $s.mcpServers | Add-Member -MemberType NoteProperty -Name "synthelion" -Value $mcpCfg -Force
    Ok "MCP server configured"

    # Hook
    if ($addHook) {
        if (-not (Get-Member -InputObject $s -Name "hooks" -MemberType NoteProperty)) {
            $s | Add-Member -MemberType NoteProperty -Name "hooks" -Value ([PSCustomObject]@{})
        }
        $cli = Find-CliSynthelion
        $hookCmd = Build-HookCommand $cli
        $hookEntry = [PSCustomObject]@{
            type          = "command"
            shell         = "powershell"
            command       = $hookCmd
            statusMessage = "Compressing prompt..."
            timeout       = 15
        }
        $hookGroup = [PSCustomObject]@{ hooks = @($hookEntry) }

        # Remove existing synthelion hook groups
        $existing = @()
        if (Get-Member -InputObject $s.hooks -Name "UserPromptSubmit" -MemberType NoteProperty) {
            $existing = @($s.hooks.UserPromptSubmit | Where-Object {
                -not ($_.hooks | Where-Object { $_.command -match "synthelion" })
            })
        }
        $existing += $hookGroup
        $s.hooks | Add-Member -MemberType NoteProperty -Name "UserPromptSubmit" -Value $existing -Force
        Ok "UserPromptSubmit hook configured"
    }

    Save-Settings $s
}

# ── remove configuration ──────────────────────────────────────────────────────
function Remove-ClaudeConfig {
    H2 "Removing Synthelion from Claude Code…"
    if (-not (Test-Path $SettingsPath)) {
        Warn "settings.json not found — nothing to remove."
        return
    }
    $s = Load-Settings
    Backup-Settings

    # Remove MCP
    if ((Get-Member -InputObject $s -Name "mcpServers" -MemberType NoteProperty) -and
        (Get-Member -InputObject $s.mcpServers -Name "synthelion" -MemberType NoteProperty)) {
        $s.mcpServers.PSObject.Properties.Remove("synthelion")
        Ok "MCP server removed"
    }

    # Remove hook
    if ((Get-Member -InputObject $s -Name "hooks" -MemberType NoteProperty) -and
        (Get-Member -InputObject $s.hooks -Name "UserPromptSubmit" -MemberType NoteProperty)) {
        $filtered = @($s.hooks.UserPromptSubmit | Where-Object {
            -not ($_.hooks | Where-Object { $_.command -match "synthelion" })
        })
        if ($filtered.Count -eq 0) {
            $s.hooks.PSObject.Properties.Remove("UserPromptSubmit")
        } else {
            $s.hooks | Add-Member -MemberType NoteProperty -Name "UserPromptSubmit" -Value $filtered -Force
        }
        Ok "Hook removed"
    }

    Save-Settings $s
}

# ── smoke test ────────────────────────────────────────────────────────────────
function Invoke-SmokeTest {
    H2 "Running smoke test…"
    $tmpPy = [System.IO.Path]::GetTempFileName() -replace '\.tmp$', '.py'
    Set-Content -Path $tmpPy -Encoding UTF8 -Value @'
from synthelion import CompressionService, CompressionLevel
svc = CompressionService()
r = svc.compress("I would like to know if it is possible to receive information.", CompressionLevel.SEMANTIC)
assert r.compressed_text
print(f"{r.original_tokens}to{r.compressed_tokens} tokens ({r.efficiency_pct:.0f}% saved): {r.compressed_text}")
'@
    $result = python $tmpPy 2>&1
    Remove-Item $tmpPy -Force -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) {
        Err "Smoke test failed: $result"
        exit 1
    }
    Ok "compress OK — $result"
}

# ── main ─────────────────────────────────────────────────────────────────────
H1 "Synthelion Installer for Claude Code (Windows)"
Write-Host "  Platform : Windows $([System.Environment]::OSVersion.Version)"
$pyVer = python --version 2>&1
Write-Host "  Python   : $pyVer"

if ($Uninstall) {
    Remove-ClaudeConfig
    if (-not $NoPip) {
        H2 "Uninstalling Synthelion…"
        python -m pip uninstall -y synthelion
        Ok "Synthelion uninstalled"
    }
    H1 "Uninstall complete."
    exit 0
}

# Check Python version
H2 "Checking Python version…"
$pyVerNum = python -c "import sys; v=sys.version_info; print(str(v.major)+'.'+str(v.minor))"
if ([version]$pyVerNum -lt [version]"3.11") {
    Err "Python $pyVerNum found — Synthelion needs 3.11+."
    Err "Download from https://www.python.org/downloads/"
    exit 1
}
Ok "Python $pyVerNum"

# pip install
if (-not $NoPip) {
    H2 "Installing Synthelion…"
    $pkg = if ($Chromadb) { "synthelion[chromadb]" } else { "synthelion" }
    if ($Upgrade) {
        python -m pip install --upgrade $pkg
    } else {
        python -m pip install $pkg
    }
    if ($LASTEXITCODE -ne 0) { Err "pip install failed"; exit 1 }
    Ok "pip install $pkg succeeded"
}

$mcpBin = Find-McpBinary
if ($mcpBin) { Info "synthelion-mcp found: $mcpBin" }
else          { Warn "synthelion-mcp not in PATH — will use Python module form" }

Invoke-SmokeTest
Configure-Claude $mcpBin (-not $NoHook)

H1 "Installation complete!"
Write-Host ""
Write-Host "  Next steps:"
Write-Host "  1. Restart Claude Code (or open /hooks to reload)"
Write-Host "  2. Ask Claude: 'Use Synthelion to compress this text'"
Write-Host "  3. Every prompt is auto-compressed and scanned for PII/prompt-injection"
Write-Host "  4. MCP tools available: compress, route_content, summarize,"
Write-Host "     session_record, session_recall, synthelion_status"
Write-Host ""
Write-Host "  Savings tracker:"
Write-Host "    synthelion status         # show all-time token savings"
Write-Host "    synthelion gain --days 7  # last 7 days"
Write-Host "    synthelion bench          # benchmark on sample corpus"
Write-Host ""
Write-Host "  Optional: enable ChromaDB semantic session memory:"
Write-Host "    powershell -ExecutionPolicy Bypass -File install_claude.ps1 -Chromadb"
Write-Host ""
Write-Host "  To update:"
Write-Host "    powershell -ExecutionPolicy Bypass -File install_claude.ps1 -Upgrade"
Write-Host ""
Write-Host "  To uninstall:"
Write-Host "    powershell -ExecutionPolicy Bypass -File install_claude.ps1 -Uninstall"
Write-Host ""
