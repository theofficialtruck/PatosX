#!/usr/bin/env bash
# Watchdog for patosx.service, run every 5 min via cron.
#
# Detects two failure modes:
#   1. Crash: systemd no longer reports the service as active.
#   2. Hang: systemd thinks it's active, but heartbeat.txt (written every 15s
#      by the bot's write_heartbeat loop) hasn't been touched recently - the
#      failure mode from the 2026-08-31 outage, where the process stayed
#      "active" for 11+ hours without ever servicing Discord again.
#
# On either failure it restarts the existing deployed build via systemctl
# (no git pull - deploys only happen through ./start.sh, run deliberately)
# and appends a diagnostic snapshot to watchdog_incidents.log for later
# review.
set -uo pipefail

SERVICE="patosx.service"
PROJECT_DIR="/home/thetruck/patosx"
HEARTBEAT_FILE="$PROJECT_DIR/heartbeat.txt"
INCIDENT_LOG="$PROJECT_DIR/watchdog_incidents.log"
HEARTBEAT_STALE_SECS=180

log_incident() {
  local reason="$1"
  {
    echo "===== INCIDENT $(date -Is) ====="
    echo "Reason: $reason"
    echo "--- systemctl status ---"
    sudo systemctl --no-pager --full status "$SERVICE" 2>&1 | head -20
    echo "--- last 40 journal lines ---"
    sudo journalctl -u "$SERVICE" -n 40 --no-pager 2>&1
    echo
  } >> "$INCIDENT_LOG"
}

ACTIVE_STATE="$(systemctl is-active "$SERVICE" 2>/dev/null || echo unknown)"

if [[ "$ACTIVE_STATE" != "active" ]]; then
  log_incident "systemd reports service is '$ACTIVE_STATE' (not active)"
  sudo systemctl restart "$SERVICE"
  exit 0
fi

if [[ -f "$HEARTBEAT_FILE" ]]; then
  hb_epoch="$(date -r "$HEARTBEAT_FILE" +%s 2>/dev/null || echo 0)"
  now_epoch="$(date +%s)"
  age=$(( now_epoch - hb_epoch ))
  if (( age > HEARTBEAT_STALE_SECS )); then
    log_incident "heartbeat stale for ${age}s (process active but not servicing async tasks - suspected hang)"
    sudo systemctl restart "$SERVICE"
    exit 0
  fi
fi

exit 0
