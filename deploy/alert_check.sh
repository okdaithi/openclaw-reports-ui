#!/bin/bash
# Tell you when the reports pipeline or this site needs attention.
#
# Run every 15 minutes by openclaw-reports-alerts.timer. Each run reads
# /healthz and compares it with the last run (a small state file):
#   - a problem seen on two runs in a row sends one message;
#   - a different problem later (another report late) sends an update;
#   - when everything is fine again, one "all clear" message;
#   - nothing is repeated while a problem stays the same.
# Problems: the site not answering, the database unreadable, a report
# expected today that is late, or one loaded without any signals.
#
# Messages go through the agent's own messaging (openclaw message send), so
# they arrive where its reports do. Settings, in
# ~/.config/openclaw-reports-ui/alerts.env (never in this repository):
#   ALERT_CHANNEL=signal            channel name as openclaw knows it
#   ALERT_TARGET=+61...             recipient (a phone number for Signal)
#   ALERT_LINK=https://...:8443     the site's address, for links
#   ALERT_URL=http://127.0.0.1:8000 where to check (default shown)
#
# Usage: alert_check.sh            check and send if needed
#        alert_check.sh --test     send a test message now
#        alert_check.sh --dry-run  check, and print what would be sent

set -uo pipefail
CONFIG="${ALERT_CONFIG:-$HOME/.config/openclaw-reports-ui/alerts.env}"
STATE="${ALERT_STATE:-$HOME/.local/state/openclaw-reports-ui/alert-state}"
# shellcheck disable=SC1090
[[ -f "$CONFIG" ]] && source "$CONFIG"
URL="${ALERT_URL:-http://127.0.0.1:8000}"
LINK="${ALERT_LINK:-$URL}"
MODE="${1:-}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M')" "$*"; }

send() {
    if [[ "$MODE" == "--dry-run" ]]; then log "Would send: $1"; return 0; fi
    if [[ -z "${ALERT_CHANNEL:-}" || -z "${ALERT_TARGET:-}" ]]; then
        log "NOT SENT (set ALERT_CHANNEL and ALERT_TARGET in $CONFIG): $1"
        return 1
    fi
    if openclaw message send --channel "$ALERT_CHANNEL" --target "$ALERT_TARGET" -m "$1" >/dev/null 2>&1; then
        log "Sent: $1"
    else
        log "SENDING FAILED (is the OpenClaw gateway running?): $1"
        return 1
    fi
}

if [[ "$MODE" == "--test" ]]; then
    send "OpenClaw Reports: test message. Alerts are set up; you'll hear when a report is late or the site is down. $LINK/pipeline"
    exit $?
fi

# What is wrong now, as one line ("" when nothing)
HEALTH=$(curl -s -m 20 "$URL/healthz")
PROBLEM=$(python3 - "$HEALTH" 2>/dev/null <<'PY'
import json, sys
special = {'mika-usa': 'USA', 'mika-uk': 'UK', 'mika-uae': 'UAE', 'daily-brief': 'Daily Brief',
           'mika-executive-summary': 'Executive Summary'}
def name(series):
    return special.get(series) or series.replace('mika-', '').replace('-', ' ').title()
try:
    h = json.loads(sys.argv[1])
except ValueError:
    print('the reports site is not answering')
    sys.exit()
parts = []
if h.get('db') != 'ok':
    parts.append('the database cannot be read')
if not h.get('reports'):
    parts.append('no reports are being found')
p = h.get('pipeline') or {}
if p.get('late'):
    parts.append('late today: ' + ', '.join(map(name, p['late'])))
if p.get('empty'):
    parts.append('loaded without signals: ' + ', '.join(map(name, p['empty'])))
print('; '.join(parts))
PY
) || PROBLEM="the reports site is not answering"

mkdir -p "$(dirname "$STATE")"
# State: line 1 the problem last seen, line 2 how many runs in a row, line 3 the problem last sent
LAST=""; RUNS=0; SENT=""
if [[ -f "$STATE" ]]; then
    { read -r LAST; read -r RUNS; read -r SENT; } < "$STATE" || true
fi
[[ "$RUNS" =~ ^[0-9]+$ ]] || RUNS=0
if [[ "$PROBLEM" == "$LAST" ]]; then RUNS=$((RUNS + 1)); else RUNS=1; fi

if [[ -z "$PROBLEM" ]]; then
    if [[ -n "$SENT" ]]; then
        send "OpenClaw Reports: all clear again (was: $SENT). $LINK/pipeline" && SENT=""
    else
        log "OK"
    fi
elif (( RUNS >= 2 )) && [[ "$PROBLEM" != "$SENT" ]]; then
    if [[ -n "$SENT" ]]; then
        send "OpenClaw Reports update: $PROBLEM. $LINK/pipeline" && SENT="$PROBLEM"
    else
        send "OpenClaw Reports needs attention: $PROBLEM. $LINK/pipeline" && SENT="$PROBLEM"
    fi
else
    log "Problem (run $RUNS${SENT:+, already sent}): $PROBLEM"
fi
printf '%s\n%s\n%s\n' "$PROBLEM" "$RUNS" "$SENT" > "$STATE"
