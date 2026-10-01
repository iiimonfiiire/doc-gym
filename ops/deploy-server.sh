#!/usr/bin/env bash
# Deploy the Doc Gym service to the server and (re)start it. No sudo needed.
# Reads DEPLOY_HOST (user@host) and optional DEPLOY_DIR from .env.
# First time only: also run ops/server-service-setup.sh on the server with sudo.
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
env_get() { { grep -E "^$1=" "$PROJECT/.env" 2>/dev/null || true; } | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }

HOST="${DEPLOY_HOST:-$(env_get DEPLOY_HOST)}"
DIR="${DEPLOY_DIR:-$(env_get DEPLOY_DIR)}"
DIR="${DIR:-doc-gym}"
[[ -n "$HOST" ]] || { echo "Set DEPLOY_HOST=user@host in .env" >&2; exit 1; }

echo "== Copying code to $HOST:~/$DIR"
ssh "$HOST" "mkdir -p ~/$DIR/scripts ~/.config/systemd/user"
scp -q "$PROJECT"/scripts/{common,generate_prompt,evaluate_draft,service}.py "$HOST:$DIR/scripts/"
sed "s#%h/doc-gym#%h/$DIR#g" "$PROJECT/ops/doc-gym.service" | ssh "$HOST" 'cat > ~/.config/systemd/user/doc-gym.service'

if ! ssh "$HOST" "test -s ~/$DIR/.env"; then
  echo "== Copying .env (API key + service token) to the server"
  scp -q "$PROJECT/.env" "$HOST:$DIR/.env"
fi
ssh "$HOST" "chmod 600 ~/$DIR/.env"

echo "== (Re)starting the service"
ssh "$HOST" 'systemctl --user daemon-reload && systemctl --user enable --quiet doc-gym && systemctl --user restart doc-gym && sleep 2 && systemctl --user --no-pager status doc-gym | head -5'
echo "== Health check (from the server itself, so firewall rules don't matter)"
ssh "$HOST" 'curl -fsS -m 5 http://127.0.0.1:8765/health' && echo
