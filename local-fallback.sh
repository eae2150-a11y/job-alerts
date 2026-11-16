#!/bin/bash
# Run the watcher from this Mac every 2 hours, for when GitHub's scheduler
# ignores the cron in .github/workflows/watch.yml. Only fires while the Mac is
# awake, which is the one thing it gives up in exchange for actually running.
#
#   ./local-fallback.sh install <ntfy-topic> [seconds-between-runs]
#   ./local-fallback.sh status
#   ./local-fallback.sh uninstall
#
# The topic is written into the plist in ~/Library/LaunchAgents, outside this
# public repo, so it never gets committed.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.emilia.jobwatch"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="/usr/bin/python3"

case "${1:-}" in
install)
  TOPIC="${2:-}"
  EVERY="${3:-7200}"
  if [ -z "$TOPIC" ]; then
    echo "Need the ntfy topic: ./local-fallback.sh install emilia-jobs-xxxxxxx" >&2
    exit 1
  fi
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$REPO/run.py</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>NTFY_TOPIC</key><string>$TOPIC</string>
  </dict>
  <key>StartInterval</key><integer>$EVERY</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$REPO/local-run.log</string>
  <key>StandardErrorPath</key><string>$REPO/local-run.log</string>
</dict>
</plist>
PLISTEOF
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$UID" "$PLIST"
  echo "Installed. Runs every $((EVERY / 60)) minutes while this Mac is awake."
  echo "Log: $REPO/local-run.log"
  ;;
status)
  if launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
    launchctl print "gui/$UID/$LABEL" | grep -E "state|last exit code|runs" || true
    echo "--- last lines of log ---"
    tail -5 "$REPO/local-run.log" 2>/dev/null || echo "(no log yet)"
  else
    echo "Not installed."
  fi
  ;;
uninstall)
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed."
  ;;
*)
  sed -n '2,12p' "${BASH_SOURCE[0]}"
  exit 1
  ;;
esac
