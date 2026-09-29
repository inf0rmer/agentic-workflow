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

for skill in "$DIST"/skills/*/; do
  name=$(basename "$skill")
  mkdir -p "$DEST/skills/$name"
  cp -R "$skill". "$DEST/skills/$name/"
  # The denylist is yours, not the repo's: create it once, never overwrite it.
  [[ "$name" != jev-decide || -f "$DEST/skills/$name/denylist.txt" ]] || printf '%s\n' \
    "# Names that must never be sent to Jev: one per line, matched case-insensitively as whole words." \
    > "$DEST/skills/$name/denylist.txt"
done

echo
echo "Installed agents, CLAUDE.md, skills, and $DEST/team/bin. Backup: $BACKUP"
echo "Next: merge $HERE/settings.hooks.json into $DEST/settings.json (hooks + allow rules)."
