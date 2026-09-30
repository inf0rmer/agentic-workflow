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
  target="$DEST/skills/$name"
  mkdir -p "$BACKUP/skills"
  if [[ -L "$target" ]]; then
    # A symlink (for example into a skills.sh store): keep the link itself, and never write through it.
    cp -P "$target" "$BACKUP/skills/$name"
    rm "$target"
    echo "note: $target was a symlink; backed up the link and replaced it with a real directory."
  elif [[ -e "$target" ]]; then
    cp -R "$target" "$BACKUP/skills/$name"
  fi
  mkdir -p "$target"
  cp -R "$skill". "$DEST/skills/$name/"
  # The denylist is yours, not the repo's: create it once, never overwrite it.
  [[ "$name" != jev-decide || -f "$DEST/skills/$name/denylist.txt" ]] || printf '%s\n' \
    "# Names that must never be sent to Jev: one per line, matched case-insensitively as whole words." \
    > "$DEST/skills/$name/denylist.txt"
done

if [[ -e "$DEST/skills/tdd" || -L "$DEST/skills/tdd" ]]; then
  echo "warning: an upstream 'tdd' skill is installed at $DEST/skills/tdd. It has trigger phrases that can fire in the tester." >&2
  echo "         The workflow uses 'tdd-inner-loop' instead; consider removing 'tdd' (it is not deleted for you)." >&2
fi

echo
echo "Installed agents, CLAUDE.md, skills, and $DEST/team/bin. Backup: $BACKUP"
echo "Next: merge $HERE/settings.hooks.json into $DEST/settings.json (hooks + allow rules)."
