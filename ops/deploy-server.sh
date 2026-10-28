#!/usr/bin/env bash
# Deploy the Doc Gym service to the server and (re)start it. No sudo needed.
# Reads DEPLOY_HOST (user@host) and optional DEPLOY_DIR from .env.
# Vendors PAKT at the ref in pakt.lock with your local git credentials, so the server needs no GitHub token.
# First time only: also run ops/server-service-setup.sh on the server with sudo.
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
env_get() { { grep -E "^$1=" "$PROJECT/.env" 2>/dev/null || true; } | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }

HOST="${DEPLOY_HOST:-$(env_get DEPLOY_HOST)}"
DIR="${DEPLOY_DIR:-$(env_get DEPLOY_DIR)}"
DIR="${DIR:-doc-gym}"
[[ -n "$HOST" ]] || { echo "Set DEPLOY_HOST=user@host in .env" >&2; exit 1; }

echo "== Vendoring PAKT (pakt.lock)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
"$PROJECT/ops/vendor-pakt.sh" "$BUILD/vendor"

echo "== Copying code to $HOST:~/$DIR"
ssh "$HOST" "mkdir -p ~/$DIR/scripts ~/.config/systemd/user && rm -rf ~/$DIR/vendor.new"
scp -q "$PROJECT"/scripts/{common,generate_prompt,evaluate_draft,signal_guide,service}.py "$HOST:$DIR/scripts/"
scp -qr "$BUILD/vendor" "$HOST:$DIR/vendor.new"
ssh "$HOST" "rm -rf ~/$DIR/vendor && mv ~/$DIR/vendor.new ~/$DIR/vendor"
sed "s#%h/doc-gym#%h/$DIR#g" "$PROJECT/ops/doc-gym.service" | ssh "$HOST" 'cat > ~/.config/systemd/user/doc-gym.service'

if ! ssh "$HOST" "test -s ~/$DIR/.env"; then
  echo "== Copying .env (API key + service token) to the server"
  scp -q "$PROJECT/.env" "$HOST:$DIR/.env"
fi
ssh "$HOST" "chmod 600 ~/$DIR/.env"

echo "== Fetching and caching the style guide on the server"
# The guide is pinned, so the server downloads it once. The service retries at startup if this fails.
ssh "$HOST" "cd ~/$DIR/scripts && /usr/bin/python3 signal_guide.py" \
  || echo "   warning: the style guide isn't cached yet. Workouts list no Signal requirements, and grading fails, until it is."

echo "== (Re)starting the service"
ssh "$HOST" 'systemctl --user daemon-reload && systemctl --user enable --quiet doc-gym && systemctl --user restart doc-gym && sleep 2 && systemctl --user --no-pager status doc-gym | head -5'
echo "== Health check (from the server itself, so firewall rules don't matter)"
ssh "$HOST" 'curl -fsS -m 5 http://127.0.0.1:8765/health' && echo
