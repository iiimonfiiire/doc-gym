#!/usr/bin/env bash
# Configure Ollama on the server: listener, RAM caps, firewall.
# Usage: sudo bash server-ollama-setup.sh [<allowed-cidr> ...]
#   No networks: Ollama listens on localhost only. That's all the Doc Gym service needs.
#   With networks (e.g. 192.168.1.0/24 100.64.0.0/10): listens on all interfaces, and ufw admits
#   only those networks. Use this if desktop CLIs should reach Ollama directly.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }

DROPIN_DIR=/etc/systemd/system/ollama.service.d
if (( $# )); then HOST=0.0.0.0; else HOST=127.0.0.1; fi

echo "== Removing leftovers from an abandoned 'systemctl edit', if any"
rm -fv "$DROPIN_DIR"/.#override.conf* "$DROPIN_DIR"/..#override.conf*.swp

echo "== Writing $DROPIN_DIR/override.conf"
mkdir -p "$DROPIN_DIR"
cat > "$DROPIN_DIR/override.conf" <<EOF
[Service]
Environment="OLLAMA_HOST=$HOST:11434"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_KEEP_ALIVE=10m"
EOF
cat "$DROPIN_DIR/override.conf"

echo "== Reloading and restarting ollama"
systemctl daemon-reload
systemctl restart ollama
sleep 3
systemctl is-active ollama

if (( $# )); then
  echo "== Firewall: 11434 only from $*"
  for net in "$@"; do
    ufw allow from "$net" to any port 11434 proto tcp comment 'ollama'
  done
  ufw status | grep 11434
fi

echo "== Verify"
systemctl --no-pager show ollama -p Environment
ss -tlnp | grep 11434
