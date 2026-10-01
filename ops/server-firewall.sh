#!/usr/bin/env bash
# Replace every ufw rule for one TCP port with exactly the networks you list.
# Usage: sudo bash server-firewall.sh <port> [<allowed-cidr> ...]
#   sudo bash server-firewall.sh 8765 100.64.0.0/10   # Doc Gym over Tailscale only
#   sudo bash server-firewall.sh 11434                # close Ollama to the network entirely
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
PORT="${1:?Usage: server-firewall.sh <port> [<allowed-cidr> ...]}"
shift
[[ "$PORT" =~ ^[0-9]+$ ]] || { echo "Port must be a number." >&2; exit 1; }

echo "== Removing existing rules for port $PORT"
# Delete from the highest rule number down, so the numbering of the rest doesn't shift.
mapfile -t NUMS < <(ufw status numbered | grep -E "^\[ *[0-9]+\] +$PORT(/tcp)? " | sed -E 's/^\[ *([0-9]+)\].*/\1/' | sort -rn)
for n in "${NUMS[@]}"; do
  ufw --force delete "$n" >/dev/null && echo "   deleted rule $n"
done

if (( $# )); then
  echo "== Allowing $PORT/tcp from $*"
  for net in "$@"; do
    ufw allow from "$net" to any port "$PORT" proto tcp >/dev/null && echo "   allowed $net"
  done
else
  echo "== No networks given: port $PORT stays closed"
fi

echo "== Rules for $PORT now:"
ufw status | grep -E "^$PORT(/tcp)? " || echo "   (none)"
