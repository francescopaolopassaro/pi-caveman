#!/usr/bin/env bash
# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
#
# Synthelion installer for Claude Code — Linux / macOS
#
# Usage:
#   chmod +x install_claude.sh
#   ./install_claude.sh              # install
#   ./install_claude.sh --upgrade    # update to latest version
#   ./install_claude.sh --no-hook    # install without auto-compression hook
#   ./install_claude.sh --uninstall  # remove everything

set -euo pipefail

# ── colours ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[0;33m'; RED='\033[0;31m'
BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}  ✓ ${RESET}$*"; }
info() { echo -e "${CYAN}  → ${RESET}$*"; }
warn() { echo -e "${YELLOW}  ! ${RESET}$*"; }
err()  { echo -e "${RED}  ✗ ${RESET}$*"; }
h1()   { echo -e "\n${BOLD}${CYAN}$*${RESET}"; }
h2()   { echo -e "\n${BOLD}$*${RESET}"; }

# ── argument parsing ─────────────────────────────────────────────────────────
UPGRADE=false; NO_HOOK=false; UNINSTALL=false; NO_PIP=false; CHROMADB=false
for arg in "$@"; do
  case "$arg" in
    --upgrade)   UPGRADE=true ;;
    --no-hook)   NO_HOOK=true ;;
    --uninstall) UNINSTALL=true ;;
    --no-pip)    NO_PIP=true ;;
    --chromadb)  CHROMADB=true ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

# ── detect python ─────────────────────────────────────────────────────────────
PY=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then PY="$cmd"; break; fi
done
if [ -z "$PY" ]; then
  err "Python not found. Install Python 3.11+ from https://www.python.org/"
  exit 1
fi

# ── settings.json ─────────────────────────────────────────────────────────────
SETTINGS="$HOME/.claude/settings.json"

load_settings() {
  if [ -f "$SETTINGS" ]; then
    cat "$SETTINGS"
  else
    echo '{}'
  fi
}

backup_settings() {
  # Copy an existing settings file to a timestamped .bak-<ts> before we modify
  # it, so a working config always has a restore point. A failed backup must
  # never abort the install.
  if [ -f "$SETTINGS" ]; then
    local bak; bak="${SETTINGS}.bak-$(date +%Y%m%d-%H%M%S)"
    if cp "$SETTINGS" "$bak" 2>/dev/null; then
      ok "Backed up existing settings → $bak"
    fi
  fi
}

save_settings() {
  local data="$1"
  mkdir -p "$(dirname "$SETTINGS")"
  # Write to a temp file then atomically move it into place, so an interrupted
  # or concurrent write can never leave a truncated settings.json.
  local tmp; tmp="${SETTINGS}.tmp-$$"
  printf '%s\n' "$data" > "$tmp" && mv "$tmp" "$SETTINGS"
  rm -f "$tmp" 2>/dev/null || true
  ok "Saved → $SETTINGS"
}

# ── find synthelion binaries ──────────────────────────────────────────────────
find_mcp_binary() {
  # 1. Same bin as the running Python
  local py_bin; py_bin=$(command -v "$PY")
  local py_dir; py_dir=$(dirname "$py_bin")
  if [ -x "$py_dir/synthelion-mcp" ]; then
    echo "$py_dir/synthelion-mcp"; return
  fi
  # 2. PATH
  if command -v synthelion-mcp &>/dev/null; then
    command -v synthelion-mcp; return
  fi
  # 3. ~/.local/bin (pip --user install)
  if [ -x "$HOME/.local/bin/synthelion-mcp" ]; then
    echo "$HOME/.local/bin/synthelion-mcp"; return
  fi
  echo ""
}

find_cli_binary() {
  if command -v synthelion &>/dev/null; then
    command -v synthelion; return
  fi
  if [ -x "$HOME/.local/bin/synthelion" ]; then
    echo "$HOME/.local/bin/synthelion"; return
  fi
  echo "synthelion"
}

# ── build hook command (bash) ─────────────────────────────────────────────────
build_hook_command() {
  local cli="$1"
  # All formatting (savings label, PII/privacy breakdown, AI Act notice)
  # happens inside `synthelion compress --json` itself (see cli.py's
  # `notice` field) — this hook only branches on `blocked`:
  #   - blocked   -> decision:"block" with reason=d['notice'] (full PII
  #                  breakdown + AI Act notice), prompt never reaches Claude.
  #   - otherwise -> systemMessage=d['notice'] (visible in the terminal) +
  #                  additionalContext=d['compressed'] (invisible to the
  #                  terminal, but that's the actual compressed prompt Claude reads).
  cat <<EOF
prompt=\$(cat | $PY -c "import sys,json; print(json.load(sys.stdin).get('prompt',''))"); if [ -n "\$prompt" ]; then r=\$(printf '%s' "\$prompt" | "$cli" compress --json 2>/dev/null); if [ -n "\$r" ]; then out=\$(printf '%s' "\$r" | $PY -c "import sys,json; d=json.load(sys.stdin); eff=int(d.get('efficiency_pct',0)); print(json.dumps({'decision':'block','reason':d.get('notice','')})) if d.get('blocked') else print(json.dumps({'systemMessage':d.get('notice',''),'hookSpecificOutput':{'hookEventName':'UserPromptSubmit','additionalContext':d.get('compressed','')}})) if eff>15 else None"); [ -n "\$out" ] && printf '%s' "\$out"; fi; fi
EOF
}

# ── configure Claude Code ─────────────────────────────────────────────────────
configure_claude() {
  local mcp_bin="$1"
  local add_hook="$2"

  h2 "Configuring Claude Code…"
  info "Settings: $SETTINGS"

  # Use a temp file to pass JSON between shell and Python without quoting issues
  local tmp; tmp=$(mktemp)
  load_settings > "$tmp"

  # Configure MCP server — Python reads/writes the temp file directly
  $PY - "$tmp" "$mcp_bin" "$PY" <<'PYEOF'
import json, sys
path, mcp_bin, py_exe = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as f:
    data = json.load(f)
data.setdefault('mcpServers', {})
if mcp_bin:
    data['mcpServers']['synthelion'] = {'command': mcp_bin}
else:
    data['mcpServers']['synthelion'] = {
        'command': py_exe,
        'args': ['-m', 'synthelion.plugins.mcp_server'],
    }
with open(path, 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write('\n')
PYEOF
  ok "MCP server configured"

  if [ "$add_hook" = "true" ]; then
    local cli; cli=$(find_cli_binary)
    local hook_cmd; hook_cmd=$(build_hook_command "$cli" | tr -d '\n')

    # Write hook command to a second temp file to avoid quoting issues
    local tmp_hook; tmp_hook=$(mktemp)
    printf '%s' "$hook_cmd" > "$tmp_hook"

    $PY - "$tmp" "$tmp_hook" <<'PYEOF'
import json, sys
path, hook_cmd_file = sys.argv[1], sys.argv[2]
with open(hook_cmd_file) as f:
    hook_cmd = f.read()
with open(path) as f:
    data = json.load(f)
hook_entry = {
    'type': 'command',
    'shell': 'bash',
    'command': hook_cmd,
    'statusMessage': 'Compressing prompt...',
    'timeout': 15,
}
hooks = data.setdefault('hooks', {})
existing = [g for g in hooks.get('UserPromptSubmit', [])
            if not any('synthelion' in h.get('command', '') for h in g.get('hooks', []))]
existing.append({'hooks': [hook_entry]})
hooks['UserPromptSubmit'] = existing
with open(path, 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write('\n')
PYEOF
    rm -f "$tmp_hook"
    ok "UserPromptSubmit hook configured"
  fi

  backup_settings
  mkdir -p "$(dirname "$SETTINGS")"
  # Stage the new content as a sibling of $SETTINGS (same filesystem) then
  # atomically rename it into place — never a partial write over the real file.
  local final; final="${SETTINGS}.tmp-$$"
  cp "$tmp" "$final" && mv "$final" "$SETTINGS"
  rm -f "$tmp" "$final" 2>/dev/null || true
  ok "Saved → $SETTINGS"
}

# ── remove configuration ──────────────────────────────────────────────────────
remove_claude_config() {
  h2 "Removing Synthelion from Claude Code…"
  if [ ! -f "$SETTINGS" ]; then
    warn "settings.json not found — nothing to remove."
    return
  fi

  local tmp; tmp=$(mktemp)
  cp "$SETTINGS" "$tmp"

  $PY - "$tmp" <<'PYEOF'
import json, sys
path = sys.argv[1]
with open(path) as f:
    data = json.load(f)
# Remove MCP
mcp = data.get('mcpServers', {})
if 'synthelion' in mcp:
    del mcp['synthelion']
    print('  ✓ MCP server removed', flush=True)
# Remove hook
hooks = data.get('hooks', {})
before = len(hooks.get('UserPromptSubmit', []))
hooks['UserPromptSubmit'] = [
    g for g in hooks.get('UserPromptSubmit', [])
    if not any('synthelion' in h.get('command', '') for h in g.get('hooks', []))
]
if before != len(hooks['UserPromptSubmit']):
    print('  ✓ Hook removed', flush=True)
if not hooks.get('UserPromptSubmit'):
    hooks.pop('UserPromptSubmit', None)
if not hooks:
    data.pop('hooks', None)
with open(path, 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write('\n')
PYEOF

  backup_settings
  local final; final="${SETTINGS}.tmp-$$"
  cp "$tmp" "$final" && mv "$final" "$SETTINGS"
  rm -f "$tmp" "$final" 2>/dev/null || true
  ok "Saved → $SETTINGS"
}

# ── smoke test ────────────────────────────────────────────────────────────────
smoke_test() {
  h2 "Running smoke test…"
  local result
  result=$($PY - <<PYEOF
from synthelion import CompressionService, CompressionLevel
svc = CompressionService()
r = svc.compress(
    "I would like to know if it is possible to receive information.",
    CompressionLevel.SEMANTIC,
)
assert r.compressed_text, "empty output"
print(f"{r.original_tokens}to{r.compressed_tokens} tokens ({r.efficiency_pct:.0f}% saved): {r.compressed_text}")
PYEOF
)
  ok "compress OK — $result"
}

# ── main ─────────────────────────────────────────────────────────────────────
h1 "Synthelion Installer for Claude Code (Linux / macOS)"
echo "  Platform : $(uname -s) $(uname -m)"
echo "  Python   : $($PY --version)"

if [ "$UNINSTALL" = "true" ]; then
  remove_claude_config
  # Remove services before pip drops the package, or the registered unit would
  # be left pointing at an executable that no longer exists.
  h2 "Removing Synthelion services…"
  if $PY -m synthelion.cli service uninstall >/dev/null 2>&1; then
    ok "Services removed"
  else
    warn "No services to remove"
  fi
  if [ "$NO_PIP" = "false" ]; then
    h2 "Uninstalling Synthelion…"
    $PY -m pip uninstall -y synthelion && ok "Synthelion uninstalled" || warn "Not installed via pip"
  fi
  h1 "Uninstall complete."
  exit 0
fi

# Check Python version
h2 "Checking Python version…"
PY_VER=$($PY -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$($PY -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PY -c "import sys; print(sys.version_info.minor)")
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  err "Python $PY_VER found — Synthelion needs 3.11+."
  err "Download from https://www.python.org/downloads/"
  exit 1
fi
ok "Python $PY_VER"

# pip install
if [ "$NO_PIP" = "false" ]; then
  h2 "Installing Synthelion…"
  PKG="synthelion"
  [ "$CHROMADB" = "true" ] && PKG="synthelion[chromadb]"
  if [ "$UPGRADE" = "true" ]; then
    $PY -m pip install --upgrade "$PKG"
  else
    $PY -m pip install "$PKG"
  fi
  ok "pip install $PKG succeeded"
fi

MCP_BIN=$(find_mcp_binary)
if [ -n "$MCP_BIN" ]; then
  info "synthelion-mcp found: $MCP_BIN"
else
  warn "synthelion-mcp not in PATH — will use Python module form"
fi

smoke_test

ADD_HOOK="true"
[ "$NO_HOOK" = "true" ] && ADD_HOOK="false"
configure_claude "$MCP_BIN" "$ADD_HOOK"

h1 "Installation complete!"
echo ""
echo "  Next steps:"
echo "  1. Restart Claude Code (or open /hooks to reload)"
echo "  2. Ask Claude: 'Use Synthelion to compress this text'"
echo "  3. Every prompt is auto-compressed and scanned for PII/prompt-injection"
echo "  4. MCP tools available: compress, route_content, summarize,"
echo "     session_record, session_recall, synthelion_status"
echo ""
echo "  Savings tracker:"
echo "    synthelion status         # show all-time token savings"
echo "    synthelion gain --days 7  # last 7 days"
echo "    synthelion bench          # benchmark on sample corpus"
echo ""
echo "  Optional: enable ChromaDB semantic session memory:"
echo "    ./install_claude.sh --chromadb"
echo ""
echo "  To update:"
echo "    ./install_claude.sh --upgrade"
echo ""
echo "  To uninstall:"
echo "    ./install_claude.sh --uninstall"
echo ""
