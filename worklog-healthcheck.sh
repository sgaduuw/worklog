#!/bin/sh
# worklog health check (SessionStart hook).
# Warns if the wl tool is missing/broken or if the log has gone stale,
# so a silent failure (e.g. an accidental file wipe) is caught within a
# session instead of weeks later. BSD stat (macOS).
root="$HOME/Projects"; wl="$root/worklog/wl"; log="$root/work_log.md"

if [ ! -x "$wl" ] || ! python3 "$wl" report --day 1970-01-01 >/dev/null 2>&1; then
  echo "⚠️  work log tool ($wl) is missing or not running. Work may be going unlogged."
elif [ ! -f "$log" ]; then
  echo "⚠️  $log is missing."
else
  age=$(( ($(date +%s) - $(stat -f %m "$log")) / 86400 ))
  [ "$age" -ge 5 ] && echo "⚠️  work_log.md unchanged for $age days. Is logging still working?"
fi
exit 0
