#!/usr/bin/env bash
# Deploy this checkout to a server running Docker Compose.
#
#   deploy/push-to-server.sh user@host [ssh-key]
#
# Copies the repository (with the game build and SDK binaries in place) to
# ~/autopilot on the server, then rebuilds and restarts the stack there and
# reloads the public test cases. The server's .env (secrets) is never copied,
# overwritten or deleted. Used by .github/workflows/deploy.yml on every push to
# test_branch; works the same by hand.
set -euo pipefail
TARGET=${1:?usage: deploy/push-to-server.sh user@host [ssh-key]}
KEY=${2:-}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=20)
[[ -n $KEY ]] && SSH+=(-i "$KEY")

compgen -G "$ROOT/autopilot-game-engine_old/build12/Build/*.wasm" >/dev/null && [[ -f $ROOT/autopilot-game-engine_old/build12/index.html ]] ||
  { echo "autopilot-game-engine_old/build12: the page or Build/ is missing (unzip build12.zip from release game-build12 into autopilot-game-engine_old)" >&2; exit 1; }

echo "==> Copying to $TARGET:~/autopilot"
rsync -az --delete -e "${SSH[*]}" \
  --exclude .git --exclude .venv --exclude .runtime --exclude '__pycache__' --exclude '*.pyc' \
  --exclude .env --exclude '.env.*' --exclude autopilot-runs --exclude node_modules \
  --exclude 'autopilot-sdk*/build' --exclude 'autopilot-sdk*/dist' --exclude '.pytest_cache' \
  --exclude /unity-files --include '/autopilot-game-engine_old/build12/' --exclude '/autopilot-game-engine_old/*/' \
  "$ROOT/" "$TARGET:autopilot/"

echo "==> Building and restarting on the server"
"${SSH[@]}" "$TARGET" "bash autopilot/deploy/server-update.sh" </dev/null
