#!/usr/bin/env bash
# Install the Coder Agents build into ~/.coder, backing up what it replaces. Run it when the workspace
# image is built: a chat pins a snapshot of its skills when it starts, so they must already be on disk.
#   CODER_MODEL_STRONG=<id> CODER_MODEL_STANDARD=<id> CODER_MODEL_FAST=<id> adapters/coder-agents/install.sh
# The IDs are the deployment's model_config_id values, which `list_subagent_models` shows.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
DIST=$ROOT/dist/coder-agents
DEST=$HOME/.coder
BACKUPS=$DEST/backups

# Check every ID before anything is written, and keep them to a plain identifier so they can't
# change how the roster is rewritten below.
for var in CODER_MODEL_STRONG CODER_MODEL_STANDARD CODER_MODEL_FAST; do
  value=${!var:-}
  if [[ -z "$value" ]]; then
    echo "agentic-workflow install: $var is not set (a model_config_id from list_subagent_models)" >&2
    exit 1
  fi
  if [[ ! "$value" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "agentic-workflow install: $var is not a plain model_config_id (letters, digits, '.', '_' and '-' only)" >&2
    exit 1
  fi
done

mkdir -p "$BACKUPS"
BACKUP=$(mktemp -d "$BACKUPS/agentic-workflow-$(date +%Y%m%d-%H%M%S)-XXXXXX")
mkdir -p "$BACKUP/skills" "$DEST/skills" "$DEST/team/bin" "$DEST/team/docs"

# Back it up and remove it first, so a symlinked AGENTS.md is replaced and never written through.
if [[ -e "$DEST/AGENTS.md" || -L "$DEST/AGENTS.md" ]]; then
  cp -L "$DEST/AGENTS.md" "$BACKUP/AGENTS.md" 2>/dev/null || cp -P "$DEST/AGENTS.md" "$BACKUP/AGENTS.md"
  rm "$DEST/AGENTS.md"
fi
sed -e "s|REPLACE-WITH-MODEL-CONFIG-ID-STRONG|$CODER_MODEL_STRONG|g" \
    -e "s|REPLACE-WITH-MODEL-CONFIG-ID-STANDARD|$CODER_MODEL_STANDARD|g" \
    -e "s|REPLACE-WITH-MODEL-CONFIG-ID-FAST|$CODER_MODEL_FAST|g" \
    "$DIST/AGENTS.md" > "$DEST/AGENTS.md"
if grep -q 'REPLACE-WITH' "$DEST/AGENTS.md"; then
  echo "agentic-workflow install: a model placeholder is left in $DEST/AGENTS.md" >&2
  exit 1
fi

install -m 0755 "$DIST"/bin/* "$DEST/team/bin/"
if [[ -d "$ROOT/docs" ]]; then cp "$ROOT"/docs/*.md "$DEST/team/docs/"; fi

# Back up what is there, remove it, and copy fresh: nothing is written through a symlink, and files
# of the user's never merge into ours.
for skill in "$DIST"/skills/*/; do
  name=$(basename "$skill")
  target=$DEST/skills/$name
  if [[ -L "$target" ]]; then
    cp -P "$target" "$BACKUP/skills/$name"
    rm "$target"
  elif [[ -e "$target" ]]; then
    cp -RP "$target" "$BACKUP/skills/$name"
    rm -rf "$target"
  fi
  mkdir -p "$target"
  cp -R "$skill". "$target/"
done

echo "Installed AGENTS.md, skills and $DEST/team/bin. Backup: $BACKUP"
echo "Set TEAM_SANDBOX=1 in the workspace image so 'team-guard sign' skips signing there."
