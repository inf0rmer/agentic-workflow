#!/usr/bin/env bash
# Self-tests for team-guard, the installers and the build, run offline in throwaway sandboxes.
#   tests/run.sh
#   VENDOR_UPSTREAM_CLONE=<clone of mattpocock/skills at the pin> tests/run.sh   also checks the vendored skills byte for byte
#   VENDOR_UPSTREAM_CLONE_TRAILOFBITS=<clone of trailofbits/skills at the pin>    does the same for the second source
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
# Keep the user's global and system git config (commit signing, hooks, aliases) out of the tests.
: > "$WORK/empty-gitconfig"
export GIT_CONFIG_GLOBAL=$WORK/empty-gitconfig GIT_CONFIG_NOSYSTEM=1
fails=0

check() { # name, expected, actual
  if [[ "$3" == *"$2"* ]]; then echo "ok   $1"; else echo "FAIL $1: expected '$2' in: $3"; fails=$((fails + 1)); fi
}

echo "== build (dist is up to date, instructions fit the size limit)"
for adapter in "$ROOT"/adapters/*/; do
  h=$(basename "$adapter")
  check "dist/$h is up to date" "up to date" "$("$ROOT/build.py" --check "$h" 2>&1 || true)"
done

echo "== workspaces, signing, installers, skills (tests/test_workspace_*.py)"
python3 "$ROOT/tests/run_workspace_tests.py" 'test_workspace_*.py' || fails=$((fails + 1))

echo
[[ $fails -eq 0 ]] && echo "all tests passed" || { echo "$fails test(s) failed"; exit 1; }
