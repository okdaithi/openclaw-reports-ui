#!/bin/bash
# Deploy the latest OpenClaw Reports UI and say, in plain words, what
# happened: what changed, whether the web app came back, and whether its
# pages load. Run it on the NUC after merging a pull request:
#
#   ~/openclaw-reports-ui/deploy/deploy.sh
#
# Options:  --restart   restart the app even when there is nothing new
#
# Settings (environment), for testing on another copy:
#   DEPLOY_SERVICE  systemd user unit   (default openclaw-reports-ui)
#   DEPLOY_URL      where the app listens (default http://127.0.0.1:8000)
#   DEPLOY_DIR      the app's folder (default: the folder above this script)
#
# Exit status: 0 deployed and working, 1 a problem (the output says what).

set -uo pipefail

# The whole script is one block, read before it runs: the update in step 1
# may replace this file while it is running.
{
APP_DIR="${DEPLOY_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICE="${DEPLOY_SERVICE:-openclaw-reports-ui}"
URL="${DEPLOY_URL:-http://127.0.0.1:8000}"
PAGES=(/ /reports /digest /countries /charts /geographic /pipeline /database)
FORCE_RESTART=0
[[ "${1:-}" == "--restart" ]] && FORCE_RESTART=1

if [[ -t 1 ]]; then
    B=$(tput bold 2>/dev/null || true); N=$(tput sgr0 2>/dev/null || true)
    GREEN=$(tput setaf 2 2>/dev/null || true); YELLOW=$(tput setaf 3 2>/dev/null || true); RED=$(tput setaf 1 2>/dev/null || true)
else
    B=""; N=""; GREEN=""; YELLOW=""; RED=""
fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%sStep %s of 4%s  %s\n' "$B" "$1" "$N" "$2"; }
note() { printf '             %s\n' "$*"; }
# The app's recent log without the one-line-per-page-request entries
recent_log() {
    journalctl --user -u "$SERVICE" -n 300 --no-pager 2>/dev/null |
        grep -v -E '"(GET|POST|HEAD) [^"]*HTTP/[0-9.]+" [0-9]+' | tail -n "${1:-12}" | sed 's/^/  /'
}
# After step 1 the new code is in place: a failure is a problem with the
# running app, and going back is offered when there is a version to go back to
after_update_problem() {
    printf '\n%sRESULT: PROBLEM%s - %s\n' "$RED$B" "$N" "$1"
    say "Its recent log (page requests left out):"
    recent_log
    if (( CHANGED )); then
        say ""
        say "To go back to the previous version ($OLD):"
        say "  cd $APP_DIR && git reset --hard $OLD && systemctl --user restart $SERVICE"
    fi
    exit 1
}
problem() {
    printf '\n%sRESULT: NOT DEPLOYED%s - %s\n' "$RED$B" "$N" "$1"
    shift
    for line in "$@"; do say "$line"; done
    exit 1
}

cd "$APP_DIR" || problem "cannot open $APP_DIR."
say "${B}OpenClaw Reports UI: deploy${N}"
say "Folder: $APP_DIR"

# 1. Get the latest version
step 1 "Getting the latest version"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    problem "files in $APP_DIR were changed by hand, so the update could mix with them:" \
        "$(git status --short --untracked-files=no | sed 's/^/  /')" \
        "Undo or save those changes, then run this again."
fi
UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null) ||
    problem "this copy's branch doesn't follow a GitHub branch (git upstream not set)."
OLD=$(git rev-parse --short HEAD)
git fetch --quiet "${UPSTREAM%%/*}" 2>/dev/null || problem "could not reach GitHub to fetch the latest version." \
    "Check the network (and that the GitHub token is still valid), then run this again."
NEW=$(git rev-parse --short "$UPSTREAM")
CHANGED=0
if [[ "$OLD" == "$NEW" ]]; then
    note "Already up to date (version $OLD). Nothing new to deploy."
elif ! git merge-base --is-ancestor HEAD "$UPSTREAM"; then
    problem "this copy has commits that are not on GitHub, so it can't simply move forward." \
        "Ask for help before going on; nothing was changed."
else
    git merge --ff-only --quiet "$UPSTREAM" || problem "the update could not be applied; nothing was changed."
    CHANGED=1
    note "Updated from version $OLD to $NEW. What's new:"
    # Merged pull requests: "Merge pull request #11 ..." with the PR title as the body
    git log --first-parent --format='%s%x1f%b' "$OLD..$NEW" | while IFS=$'\x1f' read -r subject body; do
        if [[ "$subject" =~ ^Merge\ pull\ request\ (#[0-9]+) ]]; then
            note "  - ${BASH_REMATCH[1]} ${body%%$'\n'*}"
        elif [[ -n "$subject" ]]; then
            note "  - $subject"
        fi
    done
fi

# 2. Python packages, only when requirements.txt changed
step 2 "Installing Python packages"
if (( CHANGED )) && ! git diff --quiet "$OLD" "$NEW" -- requirements.txt; then
    if [[ -x venv/bin/pip ]] && venv/bin/pip install -q -r requirements.txt; then
        note "Done. The package list had changed and is now installed."
    else
        problem "installing the Python packages failed (see the messages above)." \
            "The new code is in place but the app was not restarted, so the old one is still running." \
            "To go back to the previous version: cd $APP_DIR && git reset --hard $OLD"
    fi
else
    note "Nothing to do: the package list has not changed."
fi

# 3. Restart
step 3 "Restarting the web app"
if (( CHANGED || FORCE_RESTART )); then
    systemctl --user restart "$SERVICE" || after_update_problem "systemd could not restart $SERVICE."
    note "Done. Restarted $SERVICE."
else
    note "Not needed: nothing changed (use --restart to restart anyway)."
fi

# 4. Check it works: wait for /healthz, then load the main pages
step 4 "Checking it works"
HEALTH=""
for _ in $(seq 1 30); do
    HEALTH=$(curl -s -m 5 "$URL/healthz") && [[ "$HEALTH" == \{* ]] && break
    HEALTH=""
    sleep 1
done
if [[ -z "$HEALTH" ]]; then
    after_update_problem "the web app did not answer within 30 seconds."
fi
# Turn the /healthz JSON into plain lines: state=, line= (facts), note= (warnings)
SUMMARY=$(python3 - "$HEALTH" 2>/dev/null <<'PY'
import json, sys
h = json.loads(sys.argv[1])
special = {'mika-usa': 'USA', 'mika-uk': 'UK', 'mika-uae': 'UAE', 'daily-brief': 'Daily Brief',
           'mika-executive-summary': 'Executive Summary'}
def name(series):
    return special.get(series) or series.replace('mika-', '').replace('-', ' ').title()
days = h.get('latest_report_days')
age = {0: 'today', 1: 'yesterday', None: 'never'}.get(days, '%s days ago' % days)
print('state=%s' % h.get('status'))
print('line=Reports found: %s; the newest is dated %s.' % (h.get('reports'), age))
if h.get('db') == 'ok':
    print('line=Database: readable, %s reports.' % h.get('db_reports'))
else:
    print('line=Database: NOT readable (%s).' % h.get('db'))
p = h.get('pipeline') or {}
if p.get('ok', True):
    print("line=Today's reports: everything due so far has arrived.")
else:
    if p.get('late'):
        print('note=Late today: %s (see the Pipeline page).' % ', '.join(map(name, p['late'])))
    if p.get('empty'):
        print('note=Loaded without signals today: %s.' % ', '.join(map(name, p['empty'])))
PY
) || SUMMARY="state=unreadable"
STATE=$(sed -n 's/^state=//p' <<< "$SUMMARY")
note "The web app is answering."
sed -n 's/^line=//p' <<< "$SUMMARY" | while read -r line; do note "$line"; done
NOTES=$(sed -n 's/^note=//p' <<< "$SUMMARY")
[[ -n "$NOTES" ]] && while read -r line; do note "${YELLOW}$line${N}"; done <<< "$NOTES"

FAILED=()
for page in "${PAGES[@]}"; do
    code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' "$URL$page")
    [[ "$code" == 200 ]] || FAILED+=("$page ($code)")
done
if (( ${#FAILED[@]} )); then
    note "${RED}These pages failed to load: ${FAILED[*]}${N}"
else
    note "All ${#PAGES[@]} main pages load."
fi

# Verdict
PUBLIC=$(tailscale serve status 2>/dev/null | awk -v port="${URL##*:}" '
    /^https:\/\// { site = $1 } $0 ~ "proxy http://127.0.0.1:" port { print site; exit }')
VERSION=$(git rev-parse --short HEAD)
say ""
if [[ "$STATE" == "fail" || "$STATE" == "unreadable" || ${#FAILED[@]} -gt 0 ]]; then
    after_update_problem "version $VERSION is running, but not everything works (see step 4)."
fi
if [[ "$STATE" == "degraded" ]]; then
    say "${GREEN}${B}RESULT: SUCCESS${N} - version $VERSION is running and every page loads."
    say "${YELLOW}Note:${N} the reports pipeline has a gap today (above); that is the agent, not this deploy."
else
    say "${GREEN}${B}RESULT: SUCCESS${N} - version $VERSION is running and every page loads."
fi
[[ -n "$PUBLIC" ]] && say "Open it at $PUBLIC"
if (( CHANGED )); then
    say ""
    say "If the new version misbehaves, go back to the previous one ($OLD) with:"
    say "  cd $APP_DIR && git reset --hard $OLD && systemctl --user restart $SERVICE"
fi
exit 0
}
