#!/usr/bin/env bash
# One-command local stack: Postgres, backend, SDK (`autopilot play`), the Unity
# WebGL game (build12), the optional LLM gateway and the live exchange monitor.
#
#   ./quickstart.sh            start everything (same as `up`)
#   ./quickstart.sh organiser  start only what organisers host (database, backend,
#                              levels, LLM gateway, game page); run the SDK
#                              yourself on :5000, as a participant does
#   ./quickstart.sh smoke      send one game-shaped request through the stack
#   ./quickstart.sh tokens [ID...]  each team's own LLM gateway token (send one to each team)
#   ./quickstart.sh watch      live game log in this terminal (agent reasoning, placements)
#   ./quickstart.sh funnel     host the game, backend and LLM gateway publicly through
#                              Tailscale Funnel; participants then run only the SDK
#   ./quickstart.sh funnel off stop hosting publicly
#   ./quickstart.sh tunnel     the same through a Cloudflare quick tunnel (no account;
#                              https://<random>.trycloudflare.com, kept across down/up)
#   ./quickstart.sh tunnel off stop the tunnel
#   ./quickstart.sh status     health of every service
#   ./quickstart.sh logs [svc] follow logs (backend, sdk, gateway, monitor, game)
#   ./quickstart.sh down       stop everything (database data is kept)
#
# Optional settings, read from the environment or .env:
#   AUTOPILOT_LLM_API_KEY   one key or several comma-separated (enables the LLM agent)
#   AUTOPILOT_LLM_BASE_URL  OpenAI-compatible base URL (default: Amazon Bedrock,
#                           https://bedrock-mantle.ap-south-1.api.aws/v1)
#   AUTOPILOT_LLM_MODEL     default qwen.qwen3-next-80b-a3b-instruct
#   AGENT                   agent file to play (default: LLM tool agent with a key,
#                           else autopilot-sdk/examples/reinforcement_agent.py)
#   SDK=source              run the SDK from source instead of the bundled binary
#   GAME_ZIP=path           use a local build12.zip instead of downloading it
#   NO_BROWSER=1            do not open the browser
#   AUTOPILOT_GATEWAY_TOKENS  comma-separated organiser tokens the LLM gateway accepts;
#                           generated once when unset. Teams get their own tokens
#                           from `./quickstart.sh tokens` (60 LLM calls/min each)
#   FUNNEL_PORT             public HTTPS port for `funnel` (443, 8443 or 10000; default 8443)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
RUN="$ROOT/.runtime/quickstart"
LOGS="$RUN/logs"
PIDS="$RUN/pids"
GAME="$ROOT/autopilot-game-engine_old/build12"
GAME_RELEASE="game-build12"
GAME_ASSET="build12.zip"
REPO="nextgen-user/autopilot-unified"
DB_NAME="autopilot-quickstart-db"
DB_PORT=5433
TEAM="TEAM"
PY="$ROOT/.venv/bin/python"
PORTS=(5000 5100 8000 8004 8005 8010 8011 8020)

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

load_env() {
  [[ -f .env ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n $line ]]; do
    [[ $line == *=* ]] || continue
    key="${line%%=*}"; value="${line#*=}"   # split at the first '=' only (keys may end in '=')
    [[ $key =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    [[ -n ${!key:-} ]] && continue          # the real environment wins
    value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
    export "$key=$value"
  done < .env
}

need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required. $2"; }

load_secrets() {
  # Admin/simulator secrets and the gateway token are generated once and kept in
  # .runtime (gitignored), so a publicly hosted stack never runs on known defaults.
  local file="$RUN/secrets.env"
  if [[ ! -f $file ]]; then
    mkdir -p "$RUN"; (umask 077; {
      echo "ADMIN_SECRET=$(openssl rand -hex 16)"
      echo "SIMULATOR_SECRET=$(openssl rand -hex 16)"
      echo "EVENT_GATEWAY_TOKEN=$(openssl rand -hex 12)"
    } > "$file")
  fi
  # Signs per-team gateway tokens (`./quickstart.sh tokens`); added to older files too.
  grep -q '^GATEWAY_TEAM_SECRET=' "$file" || echo "GATEWAY_TEAM_SECRET=$(openssl rand -hex 16)" >> "$file"
  local line key
  while IFS= read -r line; do
    key=${line%%=*}
    [[ -n ${!key:-} ]] || export "$line"   # the real environment / .env wins
  done < "$file"
  export AUTOPILOT_GATEWAY_TOKENS="${AUTOPILOT_GATEWAY_TOKENS:-$EVENT_GATEWAY_TOKEN}"
  export AUTOPILOT_GATEWAY_TEAM_SECRET="${AUTOPILOT_GATEWAY_TEAM_SECRET:-$GATEWAY_TEAM_SECRET}"
  export AUTOPILOT_GATEWAY_KEY="${AUTOPILOT_GATEWAY_KEY:-${AUTOPILOT_GATEWAY_TOKENS%%,*}}"
}

alive() { [[ -f "$PIDS/$1.pid" ]] && kill -0 "$(cat "$PIDS/$1.pid")" 2>/dev/null; }

start() {  # start NAME COMMAND... ; output goes to $LOGS/NAME.log
  local name=$1; shift
  alive "$name" && { say "$name already running"; return; }
  local detach=(); command -v setsid >/dev/null && detach=(setsid)
  nohup "${detach[@]}" "$@" >"$LOGS/$name.log" 2>&1 < /dev/null &
  echo $! > "$PIDS/$name.pid"
}

wait_http() {  # wait_http URL NAME SECONDS
  for _ in $(seq 1 $(( $3 * 4 ))); do
    curl -fsS -o /dev/null "$1" 2>/dev/null && return 0
    alive "$2" || { tail -20 "$LOGS/$2.log" >&2; die "$2 exited during startup (log: $LOGS/$2.log)"; }
    sleep 0.25
  done
  tail -20 "$LOGS/$2.log" >&2; die "$2 did not answer at $1 (log: $LOGS/$2.log)"
}

setup_python() {
  if [[ ! -x $PY ]]; then
    say "Creating Python environment (.venv)"
    if command -v uv >/dev/null; then uv venv -q --python 3.11 .venv
    else python3 -m venv .venv; fi
  fi
  local stamp="$ROOT/.venv/.quickstart-deps"
  local want; want=$(cat autopilot-backend/requirements.txt autopilot-sdk/pyproject.toml | sha1sum | cut -c1-12)
  if [[ ! -f $stamp || $(cat "$stamp") != "$want" ]]; then
    say "Installing backend and SDK dependencies"
    if command -v uv >/dev/null; then
      uv pip install -q --python "$PY" -r autopilot-backend/requirements.txt -e ./autopilot-sdk
    else
      "$PY" -m pip install -q -r autopilot-backend/requirements.txt -e ./autopilot-sdk
    fi
    echo "$want" > "$stamp"
  fi
}

setup_game() {
  compgen -G "$GAME/Build/*.wasm" >/dev/null && return 0
  local zip="${GAME_ZIP:-$RUN/$GAME_ASSET}"
  if [[ ! -f $zip ]]; then
    say "Downloading the prebuilt Unity WebGL game (~45 MB, once)"
    if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
      gh release download "$GAME_RELEASE" -R "$REPO" -p "$GAME_ASSET" -D "$RUN" --clobber
    else
      local auth=()
      [[ -n ${GITHUB_TOKEN:-} ]] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")
      curl -fL "${auth[@]}" -o "$zip" \
        "https://github.com/$REPO/releases/download/$GAME_RELEASE/$GAME_ASSET" ||
        die "Game download failed. The repository is private: run 'gh auth login' or set GITHUB_TOKEN, or pass GAME_ZIP=/path/to/build12.zip"
    fi
  fi
  unzip -q -o "$zip" 'build12/Build/*' 'build12/StreamingAssets/*' -d "$(dirname "$GAME")"   # the zip holds build12/Build/*
  compgen -G "$GAME/Build/*.wasm" >/dev/null || die "$zip does not contain build12/Build"
}

setup_db() {
  if ! docker inspect "$DB_NAME" >/dev/null 2>&1; then
    say "Creating Postgres container $DB_NAME"
    docker run -d --name "$DB_NAME" -e POSTGRES_PASSWORD=password -e POSTGRES_DB=zombie_defense \
      -v autopilot-quickstart-pgdata:/var/lib/postgresql/data \
      -p 127.0.0.1:$DB_PORT:5432 postgres:16-alpine >/dev/null
  else
    docker start "$DB_NAME" >/dev/null
  fi
  for _ in $(seq 1 60); do
    docker exec "$DB_NAME" pg_isready -U postgres -q 2>/dev/null && return 0
    sleep 0.5
  done
  die "Postgres did not become ready"
}


team_tokens() {
  # Each team's own LLM gateway token (rate-limited per team). Without IDs: every
  # team in the backend, by the 4-digit ID typed in the game.
  local ids=("$@")
  if (( ${#ids[@]} == 0 )); then
    mapfile -t ids < <(curl -fsS -H "X-Admin-Secret: $ADMIN_SECRET" http://127.0.0.1:8010/api/team-data |
      "$PY" -c 'import json, sys; print("\n".join("%04d" % t["teamId"] for t in json.load(sys.stdin)))') ||
      die "Backend not reachable: start it, or pass team IDs (./quickstart.sh tokens 0004 0005)"
  fi
  echo "  Team   Token (AUTOPILOT_GATEWAY_KEY)"
  "$PY" -m integration.gateway --team-tokens "${ids[@]}" | sed 's/^/  /'
}

demo_team() {  # a plain login for local tries (team IDs are not levels any more)
  curl -fsS -o /dev/null -XPOST http://127.0.0.1:8010/admin/api/teams -H "X-Admin-Secret: $ADMIN_SECRET" \
    -H 'content-type: application/json' -d "{\"name\":\"$TEAM\"}" 2>/dev/null || true
}

downloads() {
  # What participants download from the game server's /download/: the SDK
  # binaries and the example agents (links, so a rebuild is served at once).
  rm -rf "$RUN/download"; mkdir -p "$RUN/download"
  local f
  for f in "$ROOT"/autopilot-sdk-binary/bin/autopilot-* "$ROOT"/autopilot-sdk-binary/bin/*.whl \
           "$ROOT"/autopilot-sdk/examples/{agent,llm_agent,reinforcement_agent}.py \
           "$ROOT/README_PARTICIPANT.md"; do
    [[ -f $f ]] && ln -s "$f" "$RUN/download/$(basename "$f")"
  done
  return 0
}

sdk_command() {
  local arch; arch="$(uname -s)-$(uname -m)"
  if [[ ${SDK:-binary} != source && $arch == Linux-x86_64 && -x autopilot-sdk-binary/bin/autopilot-linux-x86_64 ]]; then
    echo "$ROOT/autopilot-sdk-binary/bin/autopilot-linux-x86_64"
  else
    echo "$PY -m autopilot_sdk"
  fi
}

up() {  # up [organiser]
  local mode=${1:-all} ports=("${PORTS[@]}")
  [[ $mode == organiser ]] && ports=(8000 8004 8010)
  need docker "Install Docker and make sure the daemon is running."
  need curl ""; need unzip ""
  docker info >/dev/null 2>&1 || die "Docker is not running (try: sudo systemctl start docker)"
  mkdir -p "$LOGS" "$PIDS"
  load_env
  load_secrets
  command -v ss >/dev/null && for port in "${ports[@]}"; do
    # Services bind 127.0.0.1, so only loopback/wildcard listeners clash (not e.g. tailscale serve).
    if ss -ltnH "sport = :$port" 2>/dev/null | awk '{print $4}' \
         | grep -qE '^(127\.0\.0\.1|0\.0\.0\.0|\*|\[::\]|\[::1\]):' && ! alive_on "$port"; then
      die "Port $port is in use by another program (ss -ltnp | grep :$port)"
    fi
  done
  setup_python
  setup_game
  setup_db

  say "Starting backend on :8010"
  (cd autopilot-backend && start backend env \
    DATABASE_URL="postgresql+asyncpg://postgres:password@127.0.0.1:$DB_PORT/zombie_defense" \
    UPLOAD_DIR="$RUN/uploads" ADMIN_SECRET="$ADMIN_SECRET" SIMULATOR_SECRET="$SIMULATOR_SECRET" \
    AUTOPILOT_GATEWAY_TEAM_SECRET="$AUTOPILOT_GATEWAY_TEAM_SECRET" REQUIRE_LOGIN="${REQUIRE_LOGIN:-false}" \
    "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8010)
  wait_http http://127.0.0.1:8010/health backend 60
  demo_team

  local agent="${AGENT:-}"
  if [[ -n ${AUTOPILOT_LLM_API_KEY:-} ]]; then
    say "Starting LLM gateway on :8004 (${AUTOPILOT_LLM_MODEL:-qwen.qwen3-next-80b-a3b-instruct})"
    start gateway env \
      AUTOPILOT_LLM_BASE_URL="${AUTOPILOT_LLM_BASE_URL:-https://bedrock-mantle.ap-south-1.api.aws/v1}" \
      AUTOPILOT_LLM_MODEL="${AUTOPILOT_LLM_MODEL:-qwen.qwen3-next-80b-a3b-instruct}" \
      AUTOPILOT_LLM_API_KEY="$AUTOPILOT_LLM_API_KEY" AUTOPILOT_GATEWAY_TOKENS="$AUTOPILOT_GATEWAY_TOKENS" \
      AUTOPILOT_GATEWAY_TEAM_SECRET="$AUTOPILOT_GATEWAY_TEAM_SECRET" \
      "$PY" -m uvicorn integration.gateway:app --host 127.0.0.1 --port 8004
    wait_http http://127.0.0.1:8004/health gateway 30
    agent="${agent:-autopilot-sdk/examples/llm_tool_agent.py}"
  else
    agent="${agent:-autopilot-sdk/examples/reinforcement_agent.py}"
  fi

  if [[ $mode == organiser ]]; then
    say "Serving the game on :8000"
    downloads; mapfile -t door < <(front_door)
    start game "$PY" tools/serve_game.py "$GAME" --port 8000 --cache "$RUN/game-gzip" "${door[@]}"
    wait_http http://127.0.0.1:8000/index.html game 120  # first start gzips the build
    cat <<EOF

  Organiser side is up: backend http://127.0.0.1:8010, game http://localhost:8000$(
    [[ -n ${AUTOPILOT_LLM_API_KEY:-} ]] && echo ", LLM gateway http://127.0.0.1:8004/v1")

  Now play as a participant, in another terminal:
    autopilot-sdk-binary/bin/autopilot-linux-x86_64 verify --agent my_agent.py
    autopilot-sdk-binary/bin/autopilot-linux-x86_64 play --agent my_agent.py
  then open http://localhost:8000, type a 4-digit ID from the table above,
  fill the URL boxes with http://127.0.0.1:5000/api/team, .../api/reinforcements and
  .../api/results (the game needs them) and press play. Results print in the SDK terminal.
  Stop: Ctrl+C the SDK, then ./quickstart.sh down

EOF
    return
  fi

  say "Starting live monitor on :8020"
  start monitor "$PY" tools/live-monitor/monitor.py --runtime "$RUN" --agent-file "$agent" \
    --tap game-sdk=127.0.0.1:5000=http://127.0.0.1:5100 \
    --tap sdk-backend=127.0.0.1:8011=http://127.0.0.1:8010 \
    --tap agent-gateway=127.0.0.1:8005=http://127.0.0.1:8004
  wait_http http://127.0.0.1:8020/health monitor 20

  say "Starting SDK: autopilot play --agent $agent"
  # shellcheck disable=SC2046
  start sdk env AUTOPILOT_GATEWAY_URL=http://127.0.0.1:8005/v1 \
    AUTOPILOT_LLM_MODEL="${AUTOPILOT_LLM_MODEL:-qwen.qwen3-next-80b-a3b-instruct}" \
    $(sdk_command) play --agent "$agent" --backend http://127.0.0.1:8011 --port 5100
  wait_http http://127.0.0.1:5000/health sdk 30

  say "Serving the game on :8000"
  downloads; mapfile -t door < <(front_door)
    start game "$PY" tools/serve_game.py "$GAME" --port 8000 --cache "$RUN/game-gzip" "${door[@]}"
  wait_http http://127.0.0.1:8000/index.html game 120  # first start gzips the build

  cat <<EOF

  Game:     http://localhost:8000   type a 4-digit ID from the table above,
                                    URL boxes: http://127.0.0.1:5000/api/team,
                                    .../api/reinforcements, .../api/results; press play
  Monitor:  http://localhost:8020   every game / SDK / backend / LLM exchange, live
  Agent:    $agent  (edits apply at the next pause)
  Logs:     ./quickstart.sh logs     Stop: ./quickstart.sh down

EOF
  if [[ -z ${NO_BROWSER:-} ]]; then
    for opener in xdg-open open; do
      command -v $opener >/dev/null && { $opener http://localhost:8020 >/dev/null 2>&1 & $opener http://localhost:8000 >/dev/null 2>&1 & break; }
    done
  fi
}

alive_on() {  # is the process on this port one of ours?
  local pid
  pid=$(ss -ltnpH "sport = :$1" 2>/dev/null | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
  [[ -n $pid ]] || return 1
  for f in "$PIDS"/*.pid; do
    [[ -f $f ]] || continue
    local ours; ours=$(cat "$f")
    [[ $pid == "$ours" ]] && return 0
    pgrep -s "$ours" 2>/dev/null | grep -qx "$pid" && return 0
  done
  return 1
}

down() {
  for f in "$PIDS"/*.pid; do
    [[ -f $f ]] || continue
    local pid; pid=$(cat "$f")
    kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 0.3; done
    kill -9 -- "-$pid" 2>/dev/null || true
    rm -f "$f"
    say "stopped $(basename "$f" .pid)"
  done
  docker stop "$DB_NAME" >/dev/null 2>&1 && say "stopped $DB_NAME (data kept)" || true
}

status() {
  for svc in "backend http://127.0.0.1:8010/health" "sdk http://127.0.0.1:5000/health" \
             "gateway http://127.0.0.1:8004/health" "monitor http://127.0.0.1:8020/health" \
             "game http://127.0.0.1:8000/index.html"; do
    set -- $svc
    if curl -fsS -o /dev/null "$2" 2>/dev/null; then echo "  up    $1  $2"; else echo "  down  $1  $2"; fi
  done
}

smoke() {
  local snap='{"teamID":"TEAM","sessionID":"smoke","elapsedSeconds":20,"counts":{"activeDefenceCount":0,"activeEnemyCount":2,"enemiesQueued":0,"totalEnemiesInWave":2},"defences":[],"enemies":[{"id":"E1","type":"EnemClown","health":45,"maxHealth":100,"position":{"x":3,"y":2},"isAttacking":false,"isDead":false},{"id":"E2","type":"EnemMonk","health":90,"maxHealth":100,"position":{"x":6,"y":4},"isAttacking":false,"isDead":false}],"candidatePlacements":[]}'
  say "game -> SDK: POST /api/team"
  curl -fsS -XPOST localhost:5000/api/team -H 'content-type: application/json' -d "{\"teamID\":\"$TEAM\"}" | head -c 200; echo
  say "game -> SDK -> agent -> backend referee: POST /api/reinforcements"
  local out; out=$(curl -fsS -XPOST localhost:5000/api/reinforcements -H 'content-type: application/json' -d "$snap")
  echo "$out"
  [[ $out == *'"placeDefences": true'* ]] || die "expected approved placements"
  say "OK - open http://localhost:8020 to see these exchanges"
}

front_door() {
  # The game server is the single public entry point: it serves the game and the
  # downloads and forwards the participant routes. Submission routes
  # of the backend are not forwarded. With the monitor running, traffic goes
  # through its taps so participants' exchanges show in `./quickstart.sh watch`.
  local backend=8010 llm=8004
  curl -fsS -o /dev/null http://127.0.0.1:8020/health 2>/dev/null && backend=8011 llm=8005
  printf '%s\n' --mount "/download=$RUN/download" --mount "/leaderboard=$ROOT/pvz-leaderboard" \
    --proxy "/backend/api=http://127.0.0.1:$backend/api" \
    --proxy "/backend/health=http://127.0.0.1:8010/health" \
    --proxy "/backend/register=http://127.0.0.1:8010/register" \
    --proxy "/backend/admin=http://127.0.0.1:8010/admin" \
    --proxy "/llm/v1=http://127.0.0.1:$llm/v1"
}

participant_lines() {  # participant_lines URL HOW_TO_STOP
  local url=$1 token=${AUTOPILOT_GATEWAY_TOKENS%%,*}
  echo "$url" > "$RUN/public-url"
  cat <<EOF

  Public:     $url
    game      $url/
    backend   $url/backend      (/api/* and /health are reachable)
    LLM       $url/llm/v1       (token required, ${AUTOPILOT_GATEWAY_RATE_PER_MIN:-60} calls/min per token)
    downloads $url/download/    (SDK binaries, example agents, README_PARTICIPANT.md)

  Send participants these lines (Linux/macOS):
    export AUTOPILOT_GATEWAY_URL=$url/llm/v1
    export AUTOPILOT_GATEWAY_KEY=$token
    ./autopilot-<os>-<arch> play --agent agent.py --backend $url/backend
  Windows PowerShell:
    \$env:AUTOPILOT_GATEWAY_URL="$url/llm/v1"; \$env:AUTOPILOT_GATEWAY_KEY="$token"
    .\\autopilot-windows-x86_64.exe play --agent agent.py --backend $url/backend
  Then open $url in Chrome or Firefox, type the team ID and press play.

  Stop publishing: $2

EOF
}

public_ready() {
  load_env; load_secrets
  curl -fsS -o /dev/null http://127.0.0.1:8000/backend/health \
    || die "Start the stack first: ./quickstart.sh organiser (or up)"
  [[ -n ${AUTOPILOT_LLM_API_KEY:-} ]] || say "No LLM key set: agents can play, but LLM calls will fail"
}

funnel() {  # funnel on|off : publish through Tailscale Funnel
  need tailscale "Install Tailscale and log in first."
  local port=${FUNNEL_PORT:-8443}
  local ts=(tailscale)
  tailscale funnel status >/dev/null 2>&1 || ts=(sudo tailscale)
  if [[ $1 == off ]]; then
    "${ts[@]}" funnel --https="$port" off && say "Funnel on :$port stopped"
    return
  fi
  public_ready
  "${ts[@]}" funnel --https="$port" off >/dev/null 2>&1 || true   # drop older per-path routes
  "${ts[@]}" funnel --bg --yes --https="$port" http://127.0.0.1:8000 >/dev/null
  local host
  host=$(tailscale status --json | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
  participant_lines "https://$host$([[ $port == 443 ]] || echo ":$port")" "./quickstart.sh funnel off"
}

tunnel() {  # tunnel on|off : publish through a Cloudflare quick tunnel (no account)
  local pidfile="$RUN/tunnel.pid" log="$RUN/logs/tunnel.log" url=""
  # Kept outside $PIDS so `down`/`up` (and tools/release.sh) keep the URL.
  if [[ $1 == off ]]; then
    [[ -f $pidfile ]] && kill "$(cat "$pidfile")" 2>/dev/null && say "Cloudflare tunnel stopped"
    rm -f "$pidfile"; return
  fi
  local cf; cf=$(command -v cloudflared || true)
  [[ -n $cf ]] || die "cloudflared is required: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
  public_ready
  if [[ -f $pidfile ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    url=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$log" | tail -1)
  else
    mkdir -p "$RUN/logs"
    nohup setsid "$cf" tunnel --no-autoupdate --url http://127.0.0.1:8000 >"$log" 2>&1 </dev/null &
    echo $! > "$pidfile"
    for _ in $(seq 1 60); do
      url=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$log" | tail -1)
      [[ -n $url ]] && grep -q "Registered tunnel connection" "$log" && break
      sleep 0.5
    done
  fi
  [[ -n $url ]] || { tail -20 "$log"; die "The Cloudflare tunnel did not start (log: $log)"; }
  for _ in $(seq 1 30); do curl -fsS -o /dev/null "$url/backend/health" 2>/dev/null && break; sleep 1; done
  participant_lines "$url" "./quickstart.sh tunnel off"
}

case "${1:-up}" in
  up) up ;;
  organiser|organizer) up organiser ;;
  down) down ;;
  status) status ;;
  smoke) smoke ;;
  tokens) load_env; load_secrets; team_tokens "${@:2}" ;;
  funnel) funnel "${2:-on}" ;;
  tunnel) tunnel "${2:-on}" ;;
  watch) "$PY" tools/live-monitor/stream.py "${@:2}" ;;
  logs) mkdir -p "$LOGS"; if [[ -n ${2:-} ]]; then tail -n 50 -F "$LOGS/$2.log"; else tail -n 20 -F "$LOGS"/*.log; fi ;;
  *) sed -n '2,24p' "$0"; exit 1 ;;
esac
