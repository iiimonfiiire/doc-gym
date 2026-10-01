#!/usr/bin/env bash
# One-time root setup for the Doc Gym service on the server.
# Usage: sudo bash server-service-setup.sh <allowed-cidr> [<allowed-cidr> ...]
#   e.g. sudo bash server-service-setup.sh 192.168.1.0/24 100.64.0.0/10   (LAN + Tailscale)
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
(( $# )) || { echo "Pass the networks allowed to reach port 8765, e.g. 100.64.0.0/10" >&2; exit 1; }
USER_NAME="${SUDO_USER:?Run via sudo from the account that will run the service}"

echo "== Let $USER_NAME's user services run without an SSH session (lingering)"
loginctl enable-linger "$USER_NAME"
loginctl show-user "$USER_NAME" -p Linger

echo "== Firewall: 8765 only from $*"
for net in "$@"; do
  ufw allow from "$net" to any port 8765 proto tcp comment 'doc-gym'
done
ufw status | grep 8765
