#!/usr/bin/env bash
# Refuse to publish cards that are not from this run. Shared by streak.yml and
# scripts/test_schedule.py, so the check the job runs is the check that is
# tested - on 2026-09-24 the workflow grepped for a stamp the generator never
# wrote, and every run went red.
set -euo pipefail

day="${CARD_DAY:?CARD_DAY was not set by the render step}"
slot="${CARD_SLOT:?CARD_SLOT was not set by the gate step}"
dir="${CARD_OUT:-dist}"

for c in streak streak-light activity-graph activity-graph-light; do
  f="$dir/$c.svg"
  [ -s "$f" ] || { echo "::error::$f is empty"; exit 1; }
  grep -q "</svg>" "$f" || { echo "::error::$f is truncated"; exit 1; }
  grep -qF "<!-- rendered $day -->" "$f" \
    || { echo "::error::$f was not rendered today ($day)"; exit 1; }
  grep -qF "<!-- slot $slot -->" "$f" \
    || { echo "::error::$f was not rendered for slot $slot"; exit 1; }
done

# The graph's aria-label ends on the last day plotted. If that is not today,
# something rendered stale data and must not be committed.
for c in activity-graph activity-graph-light; do
  grep -qF "and $day" "$dir/$c.svg" \
    || { echo "::error::$dir/$c.svg does not plot through $day"; exit 1; }
done

echo "all four cards verified current through $day, slot $slot"
