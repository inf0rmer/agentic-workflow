#!/usr/bin/env bash
# Self-tests for bin/team-guard and bin/mutation-check, run in throwaway git repos.
#   tests/run.sh           team-guard only (fast, offline)
#   tests/run.sh --slow    also mutation-check (downloads gremlins via go run)
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
GUARD=$ROOT/core/bin/team-guard
MUT=$ROOT/core/bin/mutation-check
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
export TEAM_HOLDOUT_ROOT=$WORK/holdout
# Keep the user's global and system git config (commit signing, hooks, aliases) out of the tests.
: > "$WORK/empty-gitconfig"
export GIT_CONFIG_GLOBAL=$WORK/empty-gitconfig GIT_CONFIG_NOSYSTEM=1
fails=0

check() { # name, expected, actual
  if [[ "$3" == *"$2"* ]]; then echo "ok   $1"; else echo "FAIL $1: expected '$2' in: $3"; fails=$((fails + 1)); fi
}

new_repo() {
  rm -rf "$WORK/repo" && mkdir -p "$WORK/repo/pkg" && cd "$WORK/repo"
  git init -q -b feat/x && git config user.email t@t && git config user.name t
  echo 'func TestA(t *testing.T) {}' > pkg/a_test.go
  echo 'package pkg' > pkg/a.go
  git add -A && git commit -qm init
}

pretool() { # agent, tool, path
  printf '{"cwd":"%s","agent_type":"%s","tool_name":"%s","tool_input":{"file_path":"%s"}}' "$PWD" "$1" "$2" "$3" | "$GUARD" hook-pretool
}

echo "== build"
for adapter in "$ROOT"/adapters/*/; do
  h=$(basename "$adapter")
  check "dist/$h is up to date" "up to date" "$("$ROOT/build.py" --check "$h" 2>&1 || true)"
done

echo "== team-guard"
new_repo
echo new > pkg/b_test.go
check "refuses to seal untracked file" "not tracked" "$("$GUARD" seal pkg/b_test.go 2>&1 || true)"
rm pkg/b_test.go
echo '// dirty' >> pkg/a_test.go
check "refuses to seal uncommitted change" "uncommitted" "$("$GUARD" seal pkg/a_test.go 2>&1 || true)"
git checkout -q pkg/a_test.go

check "seals committed file" "sealed 1 file" "$("$GUARD" seal pkg/a_test.go)"
check "verify passes when untouched" "unchanged" "$("$GUARD" verify)"

echo 'func A() {}' >> pkg/a.go && git commit -qam impl
check "production changes don't trip verify" "unchanged" "$("$GUARD" verify)"

check "sealing installs the pre-commit hook" "installed" "$("$GUARD" git-hook status)"
echo '// weakened' >> pkg/a_test.go
out=$(git commit -qam tweak 2>&1) && code=0 || code=$?
check "pre-commit refuses a commit changing a sealed test" "changes sealed tests" "$out"
check "pre-commit exits non-zero" "1" "$code"
git commit -q --no-verify -am tweak   # simulate bypassing the hook
out=$("$GUARD" verify 2>&1) && code=0 || code=$?
check "verify catches committed tampering" "changed since" "$out"
check "verify exits 2 on tampering" "2" "$code"
out=$(printf '{"cwd":"%s"}' "$PWD" | "$GUARD" hook-task 2>&1) && code=0 || code=$?
check "TaskCompleted hook blocks with feedback" "can't complete" "$out"
check "TaskCompleted hook exits 2" "2" "$code"

sealed=$("$GUARD" list | python3 -c 'import json,sys; print(json.load(sys.stdin)["files"]["pkg/a_test.go"])')
git checkout -q "$sealed" -- pkg/a_test.go
out=$(git commit -qm "restore sealed test" 2>&1) && code=0 || code=$?
check "pre-commit allows restoring the sealed version" "0" "$code"
check "verify passes after restoring" "unchanged" "$("$GUARD" verify)"

rm pkg/a_test.go
check "verify catches deletion" "deleted" "$("$GUARD" verify 2>&1 || true)"
git checkout -q pkg/a_test.go

check "engineer edit of sealed test is denied" '"deny"' "$(pretool software-engineer Edit pkg/a_test.go)"
check "engineer write of sealed test (abs path) is denied" '"deny"' "$(pretool software-engineer Write "$PWD/pkg/a_test.go")"
[[ -z "$(pretool tester Edit pkg/a_test.go)" ]] && echo "ok   tester edit produces no decision" || { echo "FAIL tester edit"; fails=$((fails + 1)); }
[[ -z "$(pretool software-engineer Edit pkg/a.go)" ]] && echo "ok   engineer may edit production code" || { echo "FAIL engineer prod edit"; fails=$((fails + 1)); }
[[ -z "$(printf '{"cwd":"%s","tool_name":"Edit","tool_input":{"file_path":"pkg/a_test.go"}}' "$PWD" | "$GUARD" hook-pretool)" ]] \
  && echo "ok   unidentified agent is left to TaskCompleted detection" || { echo "FAIL unidentified agent"; fails=$((fails + 1)); }

H=$("$GUARD" holdout-path)
check "holdout path is per repo and branch" "repo/feat-x" "$H"
check "engineer can't read holdout" '"deny"' "$(pretool software-engineer Read "$H/scenario-1.md")"
[[ -z "$(pretool reviewer Read "$H/scenario-1.md")" ]] && echo "ok   reviewer may read holdout" || { echo "FAIL reviewer holdout"; fails=$((fails + 1)); }

check "unseal is logged" '"action": "unseal"' "$("$GUARD" unseal pkg/a_test.go >/dev/null && "$GUARD" list)"
echo '// sanctioned fix' >> pkg/a_test.go
out=$(git commit -qam "tester fix" 2>&1) && code=0 || code=$?
check "unsealed test can be committed" "0" "$code"

echo "== git hook chaining"
new_repo
printf '#!/bin/sh\necho foreign-hook-ran\n' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
check "status sees a foreign hook" "foreign" "$("$GUARD" git-hook status)"
check "install chains the existing hook" "chaining" "$("$GUARD" git-hook install)"
echo x >> pkg/a.go
check "chained hook still runs" "foreign-hook-ran" "$(git commit -am chained 2>&1)"
check "uninstall restores the previous hook" "restored" "$("$GUARD" git-hook uninstall)"
check "previous hook is back in place" "foreign-hook-ran" "$(cat .git/hooks/pre-commit)"
"$GUARD" seal pkg/a_test.go >/dev/null
check "verify warns when the hook is gone" "not installed" "$(rm .git/hooks/pre-commit && "$GUARD" verify 2>&1)"

echo "== codex hooks"
new_repo
"$GUARD" seal pkg/a_test.go >/dev/null
patch() { # agent, patch text
  python3 -c 'import json,sys; print(json.dumps({"cwd":sys.argv[1],"agent_type":sys.argv[2],"tool_name":"apply_patch","tool_input":{"command":sys.argv[3]}}))' "$PWD" "$1" "$2" | "$GUARD" hook-pretool
}
P_PROD=$'*** Begin Patch\n*** Update File: pkg/a.go\n@@\n+// y\n*** End Patch'
P_MIXED=$'*** Begin Patch\n*** Update File: pkg/a.go\n@@\n+// y\n*** Update File: pkg/a_test.go\n@@\n+// z\n*** End Patch'
P_DELETE=$'*** Begin Patch\n*** Delete File: pkg/a_test.go\n*** End Patch'
[[ -z "$(patch software-engineer "$P_PROD")" ]] && echo "ok   apply_patch on production code is allowed" || { echo "FAIL codex prod patch"; fails=$((fails + 1)); }
check "apply_patch touching a sealed test is denied" '"deny"' "$(patch software-engineer "$P_MIXED")"
check "apply_patch deleting a sealed test is denied" '"deny"' "$(patch software-engineer "$P_DELETE")"
[[ -z "$(patch tester "$P_MIXED")" ]] && echo "ok   tester's apply_patch is allowed" || { echo "FAIL codex tester patch"; fails=$((fails + 1)); }
echo '// weakened' >> pkg/a_test.go
check "SubagentStop sends the subagent back" '"decision": "block"' "$(printf '{"cwd":"%s","stop_hook_active":false}' "$PWD" | "$GUARD" hook-stop)"
check "SubagentStop only warns on the second stop" '"systemMessage"' "$(printf '{"cwd":"%s","stop_hook_active":true}' "$PWD" | "$GUARD" hook-stop)"
git checkout -q pkg/a_test.go
[[ -z "$(printf '{"cwd":"%s"}' "$PWD" | "$GUARD" hook-stop)" ]] && echo "ok   SubagentStop is silent when clean" || { echo "FAIL stop clean"; fails=$((fails + 1)); }

echo "== linked worktree"
new_repo
git worktree add -q "$WORK/wt" -b feat/wt
cd "$WORK/wt"
"$GUARD" seal pkg/a_test.go >/dev/null
echo '// weakened' >> pkg/a_test.go
check "pre-commit guards commits in a linked worktree" "changes sealed tests" "$(git commit -qam tweak 2>&1 || true)"
git checkout -q pkg/a_test.go
cd "$WORK/repo"
echo '// main checkout edit' >> pkg/a_test.go
out=$(git commit -qam "unrelated checkout" 2>&1) && code=0 || code=$?
check "seals in one worktree don't block another" "0" "$code"
cd "$WORK"

if [[ "${1:-}" == "--slow" ]]; then
  echo "== mutation-check"
  rm -rf "$WORK/mut" && mkdir -p "$WORK/mut/calc" && cd "$WORK/mut"
  git init -q -b main && git config user.email t@t && git config user.name t
  printf 'module example.com/mut\n\ngo 1.22\n' > go.mod
  printf 'package calc\n\nfunc Untouched(a int) bool { return a > 3 }\n\nfunc Clamp(v, lo, hi int) int { return v }\n' > calc/calc.go
  git add -A && git commit -qm base && git checkout -qb feat
  cat > calc/calc.go <<'EOF'
package calc

func Untouched(a int) bool { return a > 3 }

func Clamp(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}
EOF
  cat > calc/calc_test.go <<'EOF'
package calc

import "testing"

func TestClamp(t *testing.T) {
	if Clamp(5, 0, 10) != 5 {
		t.Fatal("mid")
	}
	if Clamp(-3, 0, 10) != 0 {
		t.Fatal("low")
	}
}
EOF
  git add -A && git commit -qm feat
  out=$("$MUT" --base main --go-only 2>&1)
  check "boundary mutants on changed lines survive" "CONDITIONALS_BOUNDARY        calc/calc.go:6" "$out"
  check "reports a mutation score" "mutation score: 50%" "$out"
  [[ "$out" != *"calc/calc.go:3"* ]] && echo "ok   untouched code is filtered out" || { echo "FAIL untouched filter"; fails=$((fails + 1)); }
fi

echo "== workspaces (tests/test_workspace_*.py)"
python3 "$ROOT/tests/run_workspace_tests.py" 'test_workspace_*.py' || fails=$((fails + 1))

echo
[[ $fails -eq 0 ]] && echo "all tests passed" || { echo "$fails test(s) failed"; exit 1; }
