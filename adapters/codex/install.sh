#!/usr/bin/env bash
# Install the Codex build into ~/.codex and ~/.agents, backing up what it replaces.
# hooks.json is not merged automatically: see the message at the end.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
DIST=$ROOT/dist/codex
DEST=${CODEX_HOME:-$HOME/.codex}
SKILLS=$HOME/.agents/skills
BACKUPS=$DEST/backups

"$ROOT/tests/run.sh"

# A fresh dir per run, even for two runs in the same second.
mkdir -p "$BACKUPS"
BACKUP=$(mktemp -d "$BACKUPS/agentic-workflow-$(date +%Y%m%d-%H%M%S)-XXXXXX")

mkdir -p "$BACKUP/agents" "$DEST/agents" "$DEST/team/bin" "$DEST/rules" "$SKILLS"
cp "$DEST"/agents/*.toml "$BACKUP/agents/" 2>/dev/null || true
cp "$DEST/AGENTS.md" "$BACKUP/AGENTS.md" 2>/dev/null || true

cp "$DIST/AGENTS.md" "$DEST/AGENTS.md"
cp "$DIST"/agents/*.toml "$DEST/agents/"
install -m 0755 "$DIST"/bin/* "$DEST/team/bin/"
sed "s#@HOME@#$HOME#g" "$HERE/team.rules.template" > "$DEST/rules/agentic-workflow.rules"

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

fail_jev() {
  echo "agentic-workflow install: skill jev-decide failed: $1" >&2
  exit 1
}

# The saved denylist lives in this temp dir, which goes away on every exit path.
SAVED_DIR=
trap '[[ -z "$SAVED_DIR" ]] || rm -rf "$SAVED_DIR"' EXIT

for skill in "$DIST"/skills/*/; do
  name=$(basename "$skill")
  target="$SKILLS/$name"
  deny_link=
  deny_saved=
  if [[ "$name" == jev-decide ]]; then
    if [[ -L "$target/denylist.txt" ]]; then
      # Keep the link itself; the file it points to is never read or written.
      deny_link=$(readlink "$target/denylist.txt") || fail_jev "could not read the denylist.txt link in $target"
    elif [[ -f "$target/denylist.txt" ]]; then
      SAVED_DIR=$(mktemp -d) || fail_jev "could not make a temp dir to keep your denylist.txt"
      deny_saved=$SAVED_DIR/denylist.txt
      cp -p "$target/denylist.txt" "$deny_saved" || fail_jev "could not save your denylist.txt ($target/denylist.txt)"
    fi
  fi
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
  if [[ -n "$deny_link" ]]; then
    rm -f "$target/denylist.txt" && ln -s "$deny_link" "$target/denylist.txt" \
      || fail_jev "could not restore your denylist.txt link in $target"
    echo "note: kept your existing jev-decide denylist.txt link ($target)."
  elif [[ -n "$deny_saved" ]]; then
    cp -p "$deny_saved" "$target/denylist.txt" || fail_jev "could not restore your denylist.txt in $target"
    rm -rf "$SAVED_DIR"; SAVED_DIR=
    echo "note: kept your existing jev-decide denylist.txt ($target)."
  fi
  # The denylist is yours, not the repo's: create it once, never overwrite it.
  if [[ "$name" == jev-decide && ! -e "$target/denylist.txt" && ! -L "$target/denylist.txt" ]]; then
    printf '%s\n' "# Names that must never be sent to Jev: one per line, matched case-insensitively as whole words." \
      > "$target/denylist.txt" || fail_jev "could not create the default denylist.txt in $target"
    echo "note: created an empty jev-decide denylist.txt; none existed."
  fi
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
