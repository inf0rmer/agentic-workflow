#!/usr/bin/env bash
# Install the Claude Code build into ~/.claude, backing up what it replaces.
# Settings are not touched: merge settings.hooks.json into ~/.claude/settings.json yourself.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
DIST=$ROOT/dist/claude-code
DEST=${CLAUDE_HOME:-$HOME/.claude}
BACKUPS=$DEST/backups

"$ROOT/tests/run.sh"

# A fresh dir per run, even for two runs in the same second.
mkdir -p "$BACKUPS"
BACKUP=$(mktemp -d "$BACKUPS/agentic-workflow-$(date +%Y%m%d-%H%M%S)-XXXXXX")

mkdir -p "$BACKUP/agents" "$DEST/agents" "$DEST/team/bin"
cp "$DEST"/agents/*.md "$BACKUP/agents/" 2>/dev/null || true
cp "$DEST/CLAUDE.md" "$BACKUP/CLAUDE.md" 2>/dev/null || true

cp "$DIST"/agents/*.md "$DEST/agents/"
cp "$DIST/CLAUDE.md" "$DEST/CLAUDE.md"
install -m 0755 "$DIST"/bin/* "$DEST/team/bin/"

# Back up what is there, remove it, and copy fresh: nothing is written through a symlink, and files
# of the user's never merge into ours.
install_skill() {
  mkdir -p "$BACKUP/skills" || return 1
  if [[ -L "$target" ]]; then
    cp -P "$target" "$BACKUP/skills/$name" || return 1
    rm "$target" || return 1
    echo "note: $target was a symlink; backed up the link and replaced it with a real directory."
  elif [[ -e "$target" ]]; then
    cp -RP "$target" "$BACKUP/skills/$name" || return 1
    rm -rf "$target" || return 1
  fi
  mkdir -p "$target" || return 1
  cp -R "$skill". "$target/" || return 1
}

for skill in "$DIST"/skills/*/; do
  name=$(basename "$skill")
  target="$DEST/skills/$name"
  saved_denylist=
  if [[ "$name" == jev-decide && -f "$target/denylist.txt" ]]; then
    saved_denylist=$(mktemp)
    cp -L "$target/denylist.txt" "$saved_denylist"   # follows a symlink to read the real file
  fi
  if ! install_skill; then
    echo "agentic-workflow install: skill $name failed: could not replace $target (already-installed skills are backed up in $BACKUP)" >&2
    exit 1
  fi
  if [[ -n "$saved_denylist" ]]; then
    cp "$saved_denylist" "$target/denylist.txt"
    rm -f "$saved_denylist"
    echo "note: kept your existing jev-decide denylist.txt ($target)."
  fi
  # The denylist is yours, not the repo's: create it once, never overwrite it.
  if [[ "$name" == jev-decide && ! -f "$target/denylist.txt" ]]; then
    printf '%s\n' "# Names that must never be sent to Jev: one per line, matched case-insensitively as whole words." \
      > "$target/denylist.txt"
    echo "note: created an empty jev-decide denylist.txt; none existed."
  fi
done

if [[ -e "$DEST/skills/tdd" || -L "$DEST/skills/tdd" ]]; then
  echo "warning: an upstream 'tdd' skill is installed at $DEST/skills/tdd. It has trigger phrases that can fire in the tester." >&2
  echo "         The workflow uses 'tdd-inner-loop' instead; consider removing 'tdd' (it is not deleted for you)." >&2
fi

echo
echo "Installed agents, CLAUDE.md, skills, and $DEST/team/bin. Backup: $BACKUP"
echo "Next: merge $HERE/settings.hooks.json into $DEST/settings.json (hooks + allow rules)."
