"""Shared helpers for talking to Jev: credentials, the outbound data check, and the API call."""
import json
import os
import re
import subprocess
import urllib.error
import urllib.request

URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = os.environ.get("JEV_MODEL", "typesafe/jev-1.13")
OP_REF = os.environ.get("JEV_OP_REF", "op://Private/OpenRouter Grafana/password")

# Defaults to denylist.txt in the skill folder, wherever the harness installed it.
DENYLIST = os.path.expanduser(
    os.environ.get("JEV_DENYLIST", os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "denylist.txt"))
)

# Jev is a third party: nothing that identifies a person, a customer, or a secret may leave
# this machine. This is a backstop for the SKILL.md rules, not a substitute for them.
SENSITIVE_PATTERNS = {
    "email address": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "phone number": r"(?<![\w.])\+?\d[\d ().-]{8,}\d(?![\w.])",
    "IP address": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    "UUID": r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    "credential": r"sk-[\w-]{16,}|gh[pousr]_\w{20,}|gl(?:c|sa)_\w{20,}|AKIA[0-9A-Z]{16}"
    r"|eyJ[\w-]{10,}\.[\w-]{10,}|-----BEGIN|(?:password|secret|token|api[_ -]?key)\s*[:=]\s*\S+",
    "Grafana Cloud stack or org URL": r"[\w-]+\.grafana(?:-dev|-ops)?\.net|grafana\.com/orgs/",
    "customer system link": r"salesforce\.com|force\.com|zendesk\.com|support-escalations",
    "customer/tenant identifier": r"\b(?:org|stack|instance|tenant|customer|account|user)"
    r"[ _-]?(?:id|slug)?\s*[:#=]?\s*\d{3,}\b",
}


def sensitive_findings(text):
    hits = [name for name, pat in SENSITIVE_PATTERNS.items() if re.search(pat, text, re.IGNORECASE)]
    if os.path.exists(DENYLIST):
        for line in open(DENYLIST):
            term = line.strip()
            if term and not term.startswith("#") and re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE):
                hits.append("denylisted name")
                break
    return hits


KEYCHAIN_SERVICE = "openrouter-api-key"


def _keychain(*args):
    return subprocess.run(
        ["security", *args, "-s", KEYCHAIN_SERVICE, "-a", os.environ.get("USER", "")],
        capture_output=True, text=True, timeout=10,
    )


def api_key(refresh=False):
    """Env var, then the macOS Keychain copy, then 1Password (which refills the Keychain).

    The Keychain copy avoids a Touch ID prompt every time 1Password's CLI session lapses;
    1Password stays the source of truth.
    """
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    if not refresh:
        cached = _keychain("find-generic-password", "-w")
        if cached.returncode == 0 and cached.stdout.strip():
            return cached.stdout.strip()
    key = subprocess.run(
        ["op", "read", OP_REF], check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()
    _keychain("add-generic-password", "-U", "-w", key)
    return key


def decide(state, questions, timeout=30):
    """Send one Decisions request and return the parsed response body."""
    data = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    for attempt in range(2):
        req = urllib.request.Request(
            URL,
            data=data,
            headers={"Authorization": f"Bearer {api_key(refresh=attempt > 0)}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            # A rotated key leaves a stale Keychain copy; refetch it from 1Password once.
            if e.code != 401 or attempt or os.environ.get("OPENROUTER_API_KEY"):
                raise


def _under(path, root):
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def workspace_dir(cwd=None):
    """The team-guard workspace dir containing cwd, or None.

    Same rules as `team-guard workspace-path` (kept as a copy because the two scripts are installed at
    different relative paths per harness): an attached worktree's pointer file wins, then location under
    the workspace root. A missing workspace.json or a dangling pointer means "not in a workspace".
    """
    d = os.path.realpath(cwd or os.getcwd())
    while not os.path.isdir(d) and os.path.dirname(d) != d:
        d = os.path.dirname(d)
    r = subprocess.run(["git", "rev-parse", "--absolute-git-dir", "--git-common-dir"],
                       cwd=d, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    lines = r.stdout.split("\n")
    if r.returncode == 0 and len(lines) >= 2:
        git_dir, common = os.path.realpath(lines[0]), os.path.realpath(os.path.join(d, lines[1]))
        if git_dir != common:  # a linked worktree
            try:
                with open(os.path.join(git_dir, "team-workspace")) as f:
                    ptr = f.readline().strip()
            except OSError:
                ptr = ""
            if ptr:
                return ptr if os.path.isfile(os.path.join(ptr, "workspace.json")) else None
    root = os.path.realpath(os.path.expanduser(os.environ.get("TEAM_WORKSPACE_ROOT") or "~/.agentic-workflow/work"))
    if d != root and _under(d, root):
        feature = os.path.relpath(d, root).split(os.sep)[0]
        wsdir = os.path.join(root, feature)
        if not feature.startswith(".") and os.path.isfile(os.path.join(wsdir, "workspace.json")):
            return wsdir
    return None
