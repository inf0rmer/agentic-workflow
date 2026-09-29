#!/usr/bin/env bash
# Install this workflow into ~/.claude, backing up what it replaces.
# Settings are not touched: merge config/settings.hooks.json into ~/.claude/settings.json yourself.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")" && pwd)
DEST=${CLAUDE_HOME:-$HOME/.claude}
BACKUP=$DEST/backups/agentic-workflow-$(date +%Y%m%d-%H%M%S)

"$ROOT/tests/run.sh"

mkdir -p "$BACKUP/agents" "$DEST/agents" "$DEST/team/bin"
cp "$DEST"/agents/*.md "$BACKUP/agents/" 2>/dev/null || true
cp "$DEST/CLAUDE.md" "$BACKUP/CLAUDE.md" 2>/dev/null || true

cp "$ROOT"/agents/*.md "$DEST/agents/"
cp "$ROOT/CLAUDE.md" "$DEST/CLAUDE.md"
install -m 0755 "$ROOT/bin/team-guard" "$ROOT/bin/mutation-check" "$DEST/team/bin/"

echo
echo "Installed agents, CLAUDE.md, and $DEST/team/bin. Backup: $BACKUP"
echo "Next: merge $ROOT/config/settings.hooks.json into $DEST/settings.json (hooks + allow rules)."
