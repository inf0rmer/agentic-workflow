#!/usr/bin/env bash
# Self-tests for bin/team-guard and bin/mutation-check, run in throwaway git repos.
#   tests/run.sh           team-guard only (fast, offline)
#   tests/run.sh --slow    also mutation-check (downloads gremlins via go run)
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
GUARD=$ROOT/bin/team-guard
MUT=$ROOT/bin/mutation-check
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
export TEAM_HOLDOUT_ROOT=$WORK/holdout
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

echo '// weakened' >> pkg/a_test.go && git commit -qam tweak
out=$("$GUARD" verify 2>&1) && code=0 || code=$?
check "verify catches committed tampering" "changed since" "$out"
check "verify exits 2 on tampering" "2" "$code"
out=$(printf '{"cwd":"%s"}' "$PWD" | "$GUARD" hook-task 2>&1) && code=0 || code=$?
check "TaskCompleted hook blocks with feedback" "can't complete" "$out"
check "TaskCompleted hook exits 2" "2" "$code"

git revert -q --no-edit HEAD >/dev/null 2>&1 || git revert --no-edit HEAD >/dev/null
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

echo
[[ $fails -eq 0 ]] && echo "all tests passed" || { echo "$fails test(s) failed"; exit 1; }
