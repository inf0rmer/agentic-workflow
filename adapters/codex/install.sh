#!/usr/bin/env bash
# Install the Codex build into ~/.codex and ~/.agents, backing up what it replaces.
# hooks.json is not merged automatically: see the message at the end.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
DIST=$ROOT/dist/codex
DEST=${CODEX_HOME:-$HOME/.codex}
SKILLS=$HOME/.agents/skills
BACKUP=$DEST/backups/agentic-workflow-$(date +%Y%m%d-%H%M%S)

"$ROOT/tests/run.sh"

mkdir -p "$BACKUP/agents" "$DEST/agents" "$DEST/team/bin" "$DEST/rules" "$SKILLS"
cp "$DEST"/agents/*.toml "$BACKUP/agents/" 2>/dev/null || true
cp "$DEST/AGENTS.md" "$BACKUP/AGENTS.md" 2>/dev/null || true

cp "$DIST/AGENTS.md" "$DEST/AGENTS.md"
cp "$DIST"/agents/*.toml "$DEST/agents/"
install -m 0755 "$DIST"/bin/* "$DEST/team/bin/"
sed "s#@HOME@#$HOME#g" "$HERE/team.rules.template" > "$DEST/rules/agentic-workflow.rules"

for skill in "$DIST"/skills/*/; do
  name=$(basename "$skill")
  target="$SKILLS/$name"
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
  cp -R "$skill". "$SKILLS/$name/"
  # The denylist is yours, not the repo's: create it once, never overwrite it.
  [[ "$name" != jev-decide || -f "$SKILLS/$name/denylist.txt" ]] || printf '%s\n' \
    "# Names that must never be sent to Jev: one per line, matched case-insensitively as whole words." \
    > "$SKILLS/$name/denylist.txt"
done

if [[ -e "$SKILLS/tdd" || -L "$SKILLS/tdd" ]]; then
  echo "warning: an upstream 'tdd' skill is installed at $SKILLS/tdd. It has trigger phrases that can fire in the tester." >&2
  echo "         The workflow uses 'tdd-inner-loop' instead; consider removing 'tdd' (it is not deleted for you)." >&2
fi

echo
echo "Installed AGENTS.md, agents, team tools, rules, and skills. Backup: $BACKUP"
echo "Next:"
echo "  1. Merge $HERE/hooks.json into $DEST/hooks.json (keep your existing hooks)."
echo "  2. In Codex, run /hooks and trust the two team-guard hooks; untrusted hooks don't run."
