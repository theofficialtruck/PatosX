#!/usr/bin/env bash
# Watchdog for patosx.service, run every 5 min via cron.
#
# Detects three failure signatures:
#   1. Crash: systemd no longer reports the service as active.
#   2. Hang: systemd thinks it's active, but heartbeat.txt (written every 15s
#      by the bot's write_heartbeat loop) hasn't been touched recently - the
#      failure mode from the 2026-08-31 outage, where the process stayed
#      "active" for 11+ hours without ever servicing Discord again.
#   3. Heartbeat never started: systemd reports active well past startup, but
#      heartbeat.txt was never created at all (e.g. write_heartbeat crashed,
#      or an older build without the heartbeat loop got deployed) - without
#      this check that state looks identical to "healthy" to case 2's logic.
#
# On any failure it restarts the existing deployed build via systemctl (no
# git pull - deploys only happen through ./start.sh, run deliberately) and
# appends a diagnostic snapshot to watchdog_incidents.log for later review.
#
# All sudo calls use -n (non-interactive): if passwordless sudo ever stops
# being configured for these commands, the script fails fast and logs it
# instead of hanging cron forever waiting on a password prompt that will
# never come.
set -uo pipefail

SERVICE="patosx.service"
PROJECT_DIR="/home/thetruck/patosx"
HEARTBEAT_FILE="$PROJECT_DIR/heartbeat.txt"
INCIDENT_LOG="$PROJECT_DIR/watchdog_incidents.log"
RUN_LOG="$PROJECT_DIR/watchdog_cron.log"
HEARTBEAT_STALE_SECS=180
HEARTBEAT_GRACE_SECS=90

# Optional local config file (chmod 600), kept out of git via .gitignore:
#   NTFY_TOPIC=...   (phone push via ntfy.sh - see README)
#   CLAUDE_CODE_OAUTH_TOKEN=...   (used by claude_diagnose.sh, not this script)
[[ -f "$PROJECT_DIR/.env.diagnose" ]] && source "$PROJECT_DIR/.env.diagnose"

notify() {
  local message="$1"
  [[ -z "${NTFY_TOPIC:-}" ]] && return 0
  curl -fsS -m 10 \
    -H "Title: PatosX watchdog" \
    -H "Priority: high" \
    -H "Tags: warning" \
    -d "$message" \
    "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1 || true
}

log_incident() {
  local reason="$1"
  notify "Restarting patosx: $reason"
  {
    echo "===== INCIDENT $(date -Is) ====="
    echo "Reason: $reason"
    echo "--- systemctl status ---"
    sudo -n systemctl --no-pager --full status "$SERVICE" 2>&1 | head -20
    echo "--- last 40 journal lines ---"
    sudo -n journalctl -u "$SERVICE" -n 40 --no-pager 2>&1
    echo
  } >>"$INCIDENT_LOG"
}

restart_service() {
  if ! sudo -n systemctl restart "$SERVICE" 2>>"$RUN_LOG"; then
    echo "$(date -Is) [error] sudo -n systemctl restart failed - passwordless sudo may not be configured for this command" >>"$RUN_LOG"
    notify "ERROR: detected a problem but the restart command itself failed - patosx may still be down. Check passwordless sudo config."
  fi
}

ACTIVE_STATE="$(systemctl is-active "$SERVICE" 2>/dev/null || echo unknown)"

if [[ "$ACTIVE_STATE" != "active" ]]; then
  log_incident "systemd reports service is '$ACTIVE_STATE' (not active)"
  restart_service
  exit 0
fi

now_epoch="$(date +%s)"

if [[ ! -f "$HEARTBEAT_FILE" ]]; then
  active_since="$(systemctl show -p ActiveEnterTimestamp --value "$SERVICE" 2>/dev/null)"
  active_since_epoch="$(date -d "$active_since" +%s 2>/dev/null || echo "$now_epoch")"
  uptime_secs=$((now_epoch - active_since_epoch))
  if ((uptime_secs > HEARTBEAT_GRACE_SECS)); then
    log_incident "heartbeat file missing after ${uptime_secs}s uptime (write_heartbeat loop never started, crashed, or an older build without it is deployed)"
    restart_service
  fi
  exit 0
fi

hb_epoch="$(date -r "$HEARTBEAT_FILE" +%s 2>/dev/null || echo 0)"
age=$((now_epoch - hb_epoch))
if ((age > HEARTBEAT_STALE_SECS)); then
  log_incident "heartbeat stale for ${age}s (process active but not servicing async tasks - suspected hang)"
  restart_service
  exit 0
fi

exit 0
