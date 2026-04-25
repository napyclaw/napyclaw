#!/bin/sh
# Fetch TS_AUTHKEY from Infisical via the Python SDK, then hand off to containerboot.
set -e

export TS_AUTHKEY=$(python3 /fetch_secret.py TS_AUTHKEY)

if [ -z "$TS_AUTHKEY" ]; then
  echo "ERROR: Could not fetch TS_AUTHKEY from Infisical."
  exit 1
fi

exec /usr/local/bin/tailscaled --state=/var/lib/tailscale/tailscaled.state &
sleep 2
/usr/local/bin/tailscale up --authkey="$TS_AUTHKEY" --hostname=napyclaw-comms
wait
