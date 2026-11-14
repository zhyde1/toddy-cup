#!/usr/bin/env bash
# Game-time poller, run by .github/workflows/update.yml.
#
# GitHub starts scheduled workflows late or skips most of them, so one run does the polling itself.
# update.py reads the real NBA schedule from ESPN and writes .poll_state ("<games on now: 0|1> <seconds
# until the next window opens, or -1>"). A window opens 15 minutes before a tip-off and closes 3.5 hours
# after it. This script then:
#   - games on now      -> refresh every 5 minutes
#   - none on now, next window opens within 8 hours -> sleep until it opens
#   - otherwise          -> stop (the hourly schedule starts the next run)
# A run lasts at most 5.5 hours (GitHub's limit is 6) and starts a fresh run if there is more to cover.
#
# Settings can be overridden by environment variables (used for testing):
#   POLL_SECONDS   wait between refreshes during games   (default 300)
#   MAX_SECONDS    longest one run keeps going            (default 19800 = 5.5 hours)
#   MAX_IDLE_WAIT  longest idle wait worth sleeping for   (default 28800 = 8 hours)
#   FORCE_WINDOW   1 = treat as games on, 0 = treat as no games, ignoring the schedule
set -u

POLL_SECONDS="${POLL_SECONDS:-300}"
MAX_SECONDS="${MAX_SECONDS:-19800}"
MAX_IDLE_WAIT="${MAX_IDLE_WAIT:-28800}"
DATA_FILE="docs/data.json"
STATE_FILE=".poll_state"
ACTIVE=0
WAIT=-1

# Used only if ESPN's schedule could not be read on this pass: assume a wide window.
broad_window() {
  local h
  h=$(TZ=America/New_York date +%-H)
  [ "$h" -ge 11 ] || [ "$h" -lt 3 ]
}

read_state() {
  if [ -n "${FORCE_WINDOW:-}" ]; then ACTIVE="$FORCE_WINDOW"; WAIT=-1; return; fi
  if [ -f "$STATE_FILE" ]; then
    read -r ACTIVE WAIT < "$STATE_FILE"
  else
    if broad_window; then ACTIVE=1; else ACTIVE=0; fi
    WAIT=-1
  fi
}

push_if_needed() {
  git fetch -q origin main || return 0
  if [ -n "$(git log origin/main..HEAD --oneline)" ]; then
    if git rebase -q origin/main; then
      git push -q origin HEAD:main || echo "Push failed. Will retry on the next pass."
    else
      git rebase --abort
      echo "Could not rebase onto the latest main. Will retry on the next pass."
    fi
  fi
}

end=$(( $(date +%s) + MAX_SECONDS ))
status=0
fails=0
timed_out=0

while true; do
  rm -f "$STATE_FILE"
  if python update.py; then
    status=0; fails=0
  else
    status=1; fails=$(( fails + 1 )); echo "update.py failed on this pass ($fails in a row)"
    if [ "$fails" -ge 3 ]; then
      echo "Three failures in a row (is the ESPN cookie still valid?). Stopping so this run shows as failed."
      exit 1
    fi
  fi

  git add "$DATA_FILE"
  if ! git diff --cached --quiet; then
    git commit -q -m "Update cup data"
    echo "Saved new data"
  fi
  push_if_needed

  read_state
  now=$(date +%s)
  remaining=$(( end - now ))
  if [ "$ACTIVE" = "1" ]; then
    nap="$POLL_SECONDS"
    echo "Games on now. Next check in ${nap}s."
    if [ "$nap" -ge "$remaining" ]; then timed_out=1; break; fi
  elif [ "$WAIT" -ge 0 ] && [ "$WAIT" -le "$MAX_IDLE_WAIT" ]; then
    nap="$WAIT"
    echo "No games right now. Next window opens in ${nap}s."
    if [ "$nap" -ge "$remaining" ]; then
      # Not enough time left in this run: sleep out the rest of it, then pass the baton on.
      [ "$remaining" -gt 0 ] && sleep "$remaining"
      timed_out=1; break
    fi
  else
    echo "No games on or coming up soon. Stopping until the next scheduled run."
    break
  fi
  sleep "$nap"
done

# Out of time with more games to cover? Hand over to a fresh run so coverage has no gap.
if [ "$timed_out" = "1" ]; then
  gh workflow run update.yml --ref main || echo "Could not start the next run. The hourly schedule will pick it up."
fi

exit "$status"
