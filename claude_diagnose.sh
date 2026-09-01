#!/usr/bin/env bash
# Diagnostic follow-up for patosx.service, run periodically via cron.
#
# The fast watchdog (healthcheck.sh) already auto-restarts on crash/hang and
# appends a snapshot to watchdog_incidents.log. This script checks whether
# any *new* incidents have landed since the last run, and if so, invokes
# Claude Code headlessly to investigate root cause and, if it finds a clear
# code-level bug, push a fix to a new branch for manual review.
#
# Safety boundaries (do not loosen without thinking it through):
#   - allowedTools scopes branch creation and pushes to the fix/incident-*
#     prefix specifically (not a bare wildcard) - "git push origin main" is
#     rejected by the tool permission system itself, not just by instruction.
#   - It never runs systemctl/start.sh itself - restarts are the watchdog's
#     job only, never this script's.
#   - It never merges anything.
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INCIDENT_LOG="$PROJECT_DIR/watchdog_incidents.log"
MARKER_FILE="$PROJECT_DIR/.claude_diagnose_offset"
RUN_LOG="$PROJECT_DIR/claude_diagnose.log"

cd "$PROJECT_DIR" || exit 1

# cron runs with a minimal environment (no .bashrc), so node/claude (installed
# via nvm, no sudo available on this box) need to be put on PATH explicitly.
export NVM_DIR="$HOME/.nvm"
[[ -s "$NVM_DIR/nvm.sh" ]] && source "$NVM_DIR/nvm.sh"
nvm use default >/dev/null 2>&1 || true

# Optional local secrets/config file (chmod 600), kept out of git via .gitignore:
#   CLAUDE_CODE_OAUTH_TOKEN=...  (from `claude setup-token` - uses your Claude
#   subscription, not separate pay-per-token API billing)
#   NTFY_TOPIC=...   (phone push via ntfy.sh - see README)
[[ -f "$PROJECT_DIR/.env.diagnose" ]] && source "$PROJECT_DIR/.env.diagnose"

notify() {
  local message="$1"
  [[ -z "${NTFY_TOPIC:-}" ]] && return 0
  curl -fsS -m 10 \
    -H "Title: PatosX diagnosis" \
    -H "Priority: default" \
    -H "Tags: mag" \
    -d "$message" \
    "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1 || true
}

if ! command -v claude >/dev/null 2>&1; then
  echo "$(date -Is) [skip] claude CLI not installed" >>"$RUN_LOG"
  exit 0
fi

if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
  echo "$(date -Is) [skip] CLAUDE_CODE_OAUTH_TOKEN not set in this cron environment - run 'claude setup-token' and add it to .env.diagnose" >>"$RUN_LOG"
  exit 0
fi

if [[ ! -f "$INCIDENT_LOG" ]]; then
  exit 0
fi

total_lines="$(wc -l <"$INCIDENT_LOG")"
last_offset="0"
[[ -f "$MARKER_FILE" ]] && last_offset="$(cat "$MARKER_FILE")"

if ((total_lines <= last_offset)); then
  exit 0
fi

new_incidents="$(tail -n +"$((last_offset + 1))" "$INCIDENT_LOG")"

PROMPT="A watchdog script just auto-restarted the patosx Discord bot (systemd service patosx.service, repo at $PROJECT_DIR) after detecting a crash or hang. Here is the new incident data (systemctl status + last journal lines at the time of the incident):

---
$new_incidents
---

Investigate root cause: check recent git log for what was deployed just before the incident, check main.py for anything that could explain a hang or crash (blocking calls, unbounded loops, unhandled exceptions), and cross-reference with the incident's journal output.

If you find a clear, narrowly-scoped code bug that plausibly explains this incident, fix it: create a new git branch off main named fix/incident-<short-description>, commit the fix with a clear message explaining the incident and the fix, and push that branch to origin. Do NOT merge to main, do NOT touch main directly, do NOT push to main, and do NOT restart or deploy the service yourself.

If you are not confident about the root cause, do not guess-fix — instead write a short diagnosis to $PROJECT_DIR/claude_diagnose_findings.log (append, with a timestamp header) explaining what you found and did not find, and stop there without touching git or main.py."

claude_output="$(mktemp)"
if timeout 900 claude -p "$PROMPT" \
  --model claude-sonnet-5 \
  --effort high \
  --output-format json \
  --allowedTools "Bash(git checkout -b fix/incident-*),Bash(git add *),Bash(git commit *),Bash(git push origin fix/incident-*),Bash(git log*),Bash(git show*),Bash(git diff*),Bash(journalctl*),Read,Write,Edit" \
  >"$claude_output" 2>&1; then
  echo "$total_lines" >"$MARKER_FILE"
  summary="$(python3 -c '
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("result", "Diagnosis complete - see claude_diagnose.log."))
except Exception:
    print("Diagnosis complete - see claude_diagnose.log.")
' "$claude_output" 2>/dev/null)"
  notify "${summary:0:1000}"
else
  claude_exit=$?
  echo "$(date -Is) [error] claude run failed or timed out (exit $claude_exit) - leaving offset at $last_offset so the next run retries these incidents" >>"$RUN_LOG"
  notify "Diagnostic run failed or timed out (exit $claude_exit) - will retry next cycle."
fi
cat "$claude_output" >>"$RUN_LOG"
rm -f "$claude_output"
