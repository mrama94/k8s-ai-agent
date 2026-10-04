#!/usr/bin/env bash
# Stops everything started by scripts/portforward.sh, plus any hand-started kubectl port-forward.
# The wrapper goes first: it restarts a forward within a second if only the kubectl process dies.
# The [k] and \. tricks keep these patterns from matching this script's own command line.
WRAPPER='scripts/portforward\.sh'
FORWARD='[k]ubectl.* port-forward'
pkill -f "$WRAPPER" 2>/dev/null
pkill -f "$FORWARD" 2>/dev/null
sleep 1
if pgrep -f "$WRAPPER|$FORWARD" >/dev/null; then
  echo "Still running:"; pgrep -fl "$WRAPPER|$FORWARD"; exit 1
fi
echo "All port-forwards stopped."
