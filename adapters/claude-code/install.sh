#!/usr/bin/env bash
# Install the Claude Code build into ~/.claude, backing up what it replaces.
# Then retire files this build no longer ships (moved into the backup, never deleted), remove the old
# team-guard hook entries from settings.json (after a backup) and write team/installed.json.
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

mkdir -p "$BACKUP/agents" "$DEST/agents" "$DEST/team/bin" "$DEST/team/docs"
cp "$DEST"/agents/*.md "$BACKUP/agents/" 2>/dev/null || true
cp "$DEST/CLAUDE.md" "$BACKUP/CLAUDE.md" 2>/dev/null || true

cp "$DIST"/agents/*.md "$DEST/agents/"
cp "$DIST/CLAUDE.md" "$DEST/CLAUDE.md"
install -m 0755 "$DIST"/bin/* "$DEST/team/bin/"
if [[ -d "$ROOT/docs" ]]; then cp "$ROOT"/docs/*.md "$DEST/team/docs/"; fi

# Back up what is there, remove it, and copy fresh: nothing is written through a symlink, and files
# of the user's never merge into ours. Returns 2 when the backup failed (nothing was removed).
install_skill() {
  mkdir -p "$BACKUP/skills" || return 2
  if [[ -L "$target" ]]; then
    cp -P "$target" "$BACKUP/skills/$name" || return 2
    rm "$target" || return 1
    echo "note: $target was a symlink; backed up the link and replaced it with a real directory."
  elif [[ -e "$target" ]]; then
    cp -RP "$target" "$BACKUP/skills/$name" || return 2
    rm -rf "$target" || return 1
  fi
  mkdir -p "$target" || return 1
  cp -R "$skill". "$target/" || return 1
}

for skill in "$DIST"/skills/*/; do
  name=$(basename "$skill")
  target="$DEST/skills/$name"
  rc=0
  install_skill || rc=$?
  if [[ $rc -ne 0 ]]; then
    if [[ $rc -eq 2 ]]; then
      echo "agentic-workflow install: skill $name failed: could not back up $target; nothing was removed" >&2
    else
      echo "agentic-workflow install: skill $name failed: could not replace $target (the original is backed up in $BACKUP)" >&2
    fi
    exit 1
  fi
done

"$ROOT/scripts/install-cleanup" --harness claude-code --dest "$DEST" --skills "$DEST/skills" \
  --backup "$BACKUP" --dist "$DIST" --docs "$ROOT/docs" --settings "$DEST/settings.json"

echo
echo "Installed agents, CLAUDE.md, skills, and $DEST/team/bin. Backup: $BACKUP"
