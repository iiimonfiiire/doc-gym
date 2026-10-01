#!/usr/bin/env bash
# Set up Doc Gym on the desktop: local .env, vault folders + notes, service client config,
# Templater hotkey targets. Safe to re-run. Existing vault notes are kept unless you pass --force.
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")" && pwd)"
FORCE="${1:-}"

env_get() { { grep -E "^$1=" "$PROJECT/.env" 2>/dev/null || true; } | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }

echo "== Local .env"
if [[ ! -e "$PROJECT/.env" ]]; then
  cp "$PROJECT/.env.example" "$PROJECT/.env"
  chmod 600 "$PROJECT/.env"
  echo "   created $PROJECT/.env"
  echo "   Fill in the required values (GROQ_API_KEY, DOC_GYM_VAULT, DOC_GYM_SERVICE_URLS, DEPLOY_HOST), then re-run."
  exit 1
fi
chmod 600 "$PROJECT/.env"

VAULT="${DOC_GYM_VAULT:-$(env_get DOC_GYM_VAULT)}"
[[ -n "$VAULT" ]] || { echo "Set DOC_GYM_VAULT in .env to your vault's absolute path." >&2; exit 1; }
[[ -d "$VAULT/.obsidian" ]] || { echo "Not an Obsidian vault: $VAULT" >&2; exit 1; }
if pgrep -xq Obsidian; then
  echo "Obsidian is running. Quit it first so it doesn't overwrite the Templater settings." >&2
  exit 1
fi
GYM="$VAULT/Doc Gym"

echo "== Vault folders: $GYM"
mkdir -p "$GYM"/{Prompts,Submissions,Templates}
cd "$PROJECT/vault-scaffold"
find . -type f -name '*.md' | while read -r f; do
  dest="$GYM/${f#./}"
  if [[ -e "$dest" && "$FORCE" != "--force" ]]; then
    echo "   keep  ${f#./}"
  else
    cp "$f" "$dest" && echo "   copy  ${f#./}"
  fi
done

echo "== Service client config"
/usr/bin/python3 "$PROJECT/scripts/write_client_config.py" "$VAULT" "$PROJECT"

echo "== Templater settings"
/usr/bin/python3 "$PROJECT/scripts/configure_templater.py" "$VAULT"

echo
echo "Next:"
echo " - Deploy the service: ./ops/deploy-server.sh"
echo " - Obsidian > Settings > Hotkeys, search 'Doc Gym' and bind:"
echo "     Templater: Create Doc Gym/Templates/new-workout.md          -> Cmd+Opt+G"
echo "     Templater: Insert Doc Gym/Templates/evaluation-template.md  -> Cmd+Opt+E"
