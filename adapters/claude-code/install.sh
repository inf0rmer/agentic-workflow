#!/usr/bin/env bash
# Install the Claude Code build into ~/.claude, backing up what it replaces.
# Settings are not touched: merge settings.hooks.json into ~/.claude/settings.json yourself.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
DIST=$ROOT/dist/claude-code
DEST=${CLAUDE_HOME:-$HOME/.claude}
BACKUP=$DEST/backups/agentic-workflow-$(date +%Y%m%d-%H%M%S)

"$ROOT/tests/run.sh"

mkdir -p "$BACKUP/agents" "$DEST/agents" "$DEST/team/bin"
cp "$DEST"/agents/*.md "$BACKUP/agents/" 2>/dev/null || true
cp "$DEST/CLAUDE.md" "$BACKUP/CLAUDE.md" 2>/dev/null || true

cp "$DIST"/agents/*.md "$DEST/agents/"
cp "$DIST/CLAUDE.md" "$DEST/CLAUDE.md"
install -m 0755 "$DIST"/bin/* "$DEST/team/bin/"

echo
echo "Installed agents, CLAUDE.md, and $DEST/team/bin. Backup: $BACKUP"
echo "Next: merge $HERE/settings.hooks.json into $DEST/settings.json (hooks + allow rules)."
