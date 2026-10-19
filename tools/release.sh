#!/usr/bin/env bash
# Rebuild the SDK binaries and redeploy the hosted stack after a change.
#
#   tools/release.sh                 test, build Linux + Windows, restart the stack, publish, smoke test
#   tools/release.sh --no-windows    skip the Windows VM build
#   tools/release.sh --no-deploy     only test and build
#   tools/release.sh --organiser     restart without the local SDK/monitor (what participants use)
#   tools/release.sh --commit        commit the rebuilt binaries as "binary"
#   tools/release.sh --push          commit them and push the current branch
#
# macOS cannot be cross-built: on a Mac run
#   cd autopilot-sdk-binary && python3 -m venv .venv && . .venv/bin/activate \
#     && pip install ".[build]" && python build_binary.py
# and copy dist/autopilot-macos-<arch> into autopilot-sdk-binary/bin/.
#
# Windows builds run in the light Windows VM (DOM, default win10-light) through
# its guest agent. Files for the VM go to ~/vm/share/MAIN (Z:\MAIN in the
# guest) under a new versioned name every run. The VM is started if needed and
# left running.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
BIN="$ROOT/autopilot-sdk-binary/bin"
SHARE="${SHARE:-$HOME/vm/share/MAIN}"
DOM="${DOM:-win10-light}"
LOG="$ROOT/.runtime/release"
WINDOWS=1 DEPLOY=1 MODE=up COMMIT=0 PUSH=0
for arg in "$@"; do
  case $arg in
    --no-windows) WINDOWS=0 ;;
    --no-deploy) DEPLOY=0 ;;
    --organiser|--organizer) MODE=organiser ;;
    --commit) COMMIT=1 ;;
    --push) COMMIT=1 PUSH=1 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }
mkdir -p "$LOG"
[[ -x $PY ]] || die "Run ./quickstart.sh once first (it creates .venv)"

say "Syncing autopilot-sdk into autopilot-sdk-binary"
for dir in src/autopilot_sdk examples tests docs; do
  [[ -d autopilot-sdk/$dir ]] && rsync -a --exclude __pycache__ "autopilot-sdk/$dir/" "autopilot-sdk-binary/$dir/"
done

say "Running tests"
"$PY" -m pytest -q autopilot-sdk/tests integration/tests 2>&1 | tail -1 | tee "$LOG/tests.txt"
grep -q " failed" "$LOG/tests.txt" && die "Tests failed (see: $PY -m pytest -q autopilot-sdk/tests integration/tests)"

say "Building the Linux binary"
(cd autopilot-sdk-binary && nice -n 19 "$PY" build_binary.py >"$LOG/build-linux.log" 2>&1) \
  || { tail -20 "$LOG/build-linux.log"; die "Linux build failed (log: $LOG/build-linux.log)"; }
# Replace by rename: the running SDK may still be executing the old file.
cp autopilot-sdk-binary/dist/autopilot-linux-x86_64 "$BIN/.autopilot-linux.new"
mv "$BIN/.autopilot-linux.new" "$BIN/autopilot-linux-x86_64"
"$BIN/autopilot-linux-x86_64" --help >/dev/null || die "The new Linux binary does not start"

if (( WINDOWS )); then
  command -v wincmd >/dev/null || die "wincmd not found (or pass --no-windows)"
  win() { DOM="$DOM" wincmd "$@" 2>/dev/null; }
  if [[ $(virsh -c qemu:///system domstate "$DOM" 2>/dev/null) != running ]]; then
    say "Starting the Windows VM ($DOM)"
    winvm light >/dev/null 2>&1 || true
  fi
  say "Waiting for the Windows guest agent"
  for _ in $(seq 1 60); do [[ $(win 'Write-Output ready' | tr -d '\r') == ready ]] && break; sleep 5; done
  [[ $(win 'Write-Output ready' | tr -d '\r') == ready ]] || die "The Windows guest agent did not answer"

  n=1; while compgen -G "$SHARE/autopilot-release-*-v$n.*" >/dev/null; do n=$((n + 1)); done
  src="autopilot-release-src-v$n.zip" script="autopilot-release-build-v$n.ps1" exe="autopilot-release-windows-v$n.exe"
  say "Packing the SDK for the VM ($src)"
  (cd autopilot-sdk-binary && zip -qr "$SHARE/$src" . -x 'bin/*' 'dist/*' 'build/*' '*__pycache__*' '*.egg-info/*' '*.spec')
  cat > "$SHARE/$script" <<EOF
\$ErrorActionPreference = 'Continue'
\$work = 'C:\\apbuild'
New-Item -ItemType Directory -Force \$work | Out-Null
Start-Transcript -Path "\$work\\build.log" -Force
try {
    \$py = 'C:\\Program Files\\Python311\\python.exe'
    if (-not (Test-Path \$py)) {
        curl.exe -sSL -o "\$work\\python-3.11.9-amd64.exe" https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
        Start-Process "\$work\\python-3.11.9-amd64.exe" -ArgumentList '/quiet','InstallAllUsers=1','PrependPath=0','Include_test=0','Include_launcher=0' -Wait
    }
    \$src = "\$work\\autopilot-sdk"
    if (Test-Path \$src) { Remove-Item -Recurse -Force \$src }
    Expand-Archive 'Z:\\MAIN\\$src' \$src -Force
    if (-not (Test-Path "\$work\\venv")) { & \$py -m venv "\$work\\venv" }
    \$vpy = "\$work\\venv\\Scripts\\python.exe"
    & \$vpy -m pip install -q "\$src[dev,build]"
    if (\$LASTEXITCODE) { throw "pip failed \$LASTEXITCODE" }
    Push-Location \$src
    & \$vpy -m pytest -q 2>&1 | Out-String | Write-Host
    Write-Host "pytest exit \$LASTEXITCODE"
    & \$vpy build_binary.py *> "\$work\\pyinstaller.log"
    if (\$LASTEXITCODE) { Get-Content "\$work\\pyinstaller.log" -Tail 20 | Write-Host; throw "build failed \$LASTEXITCODE" }
    Pop-Location
    Copy-Item "\$src\\dist\\autopilot-windows-x86_64.exe" 'Z:\\MAIN\\$exe' -Force
    Write-Host '== DONE'
} catch {
    Write-Host "== FAILED: \$_"
} finally {
    Stop-Transcript
}
EOF
  say "Building the Windows binary in the VM ($script)"
  win "Remove-Item C:\\apbuild\\build.log -ErrorAction SilentlyContinue; Start-Process powershell.exe -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','Z:\\MAIN\\$script' -WindowStyle Hidden" >/dev/null
  status=""
  for _ in $(seq 1 120); do
    status=$(win 'Get-Content C:\apbuild\build.log -ErrorAction SilentlyContinue' | tr -d '\r' || true)
    grep -qE '^== (DONE|FAILED)' <<<"$status" && break
    sleep 10
  done
  printf '%s\n' "$status" > "$LOG/build-windows.log"
  grep -E 'passed|failed|pytest exit' <<<"$status" | tail -2 | sed 's/^/    windows tests: /'
  grep -q '^== DONE' <<<"$status" || die "Windows build failed or timed out (log: $LOG/build-windows.log)"
  grep -q 'pytest exit 0' <<<"$status" || say "Windows tests reported failures; check $LOG/build-windows.log before shipping"
  cp "$SHARE/$exe" "$BIN/autopilot-windows-x86_64.exe"
fi

if (( DEPLOY )); then
  say "Restarting the stack ($MODE)"
  ./quickstart.sh down >/dev/null 2>&1 || true
  NO_BROWSER=1 ./quickstart.sh "$MODE" >"$LOG/up.log" 2>&1 || { tail -20 "$LOG/up.log"; die "Stack did not start"; }
  say "Publishing (Cloudflare tunnel, plus Tailscale Funnel when available)"
  ./quickstart.sh tunnel >"$LOG/funnel.txt" 2>&1 || { cat "$LOG/funnel.txt"; die "Cloudflare tunnel failed"; }
  command -v tailscale >/dev/null && ./quickstart.sh funnel >"$LOG/tailscale.txt" 2>&1 || true
  url=$(grep -m1 -o 'https://[^ ]*' "$LOG/funnel.txt")

  say "Smoke test: the new binary as a participant against $url"
  set -a; . .runtime/quickstart/secrets.env; set +a
  port=5399
  AUTOPILOT_GATEWAY_URL="$url/llm/v1" AUTOPILOT_GATEWAY_KEY="${AUTOPILOT_GATEWAY_TOKENS:-$EVENT_GATEWAY_TOKEN}" \
    "$BIN/autopilot-linux-x86_64" play --agent autopilot-sdk/examples/reinforcement_agent.py \
    --backend "$url/backend" --port "$port" >"$LOG/smoke-sdk.log" 2>&1 &
  sdk=$!
  trap 'kill $sdk 2>/dev/null || true' EXIT
  for _ in $(seq 1 40); do curl -fsS -o /dev/null "http://127.0.0.1:$port/health" 2>/dev/null && break; sleep 0.25; done
  team=$(curl -fsS -XPOST "http://127.0.0.1:$port/api/team" -H 'content-type: application/json' -d '{"teamID":"0003"}') \
    || { tail -20 "$LOG/smoke-sdk.log"; die "Smoke test: /api/team through the public backend failed"; }
  grep -q sessionID <<<"$team" || die "Smoke test: no session in $team"
  curl -fsS -o /dev/null "$url/" || die "Smoke test: the public game page did not load"
  kill $sdk 2>/dev/null || true
  say "Smoke test passed"
  sed -n '/Public/,$p' "$LOG/funnel.txt"
fi

if (( COMMIT )); then
  # The synced SDK copy and the binaries belong together.
  git add autopilot-sdk-binary/src autopilot-sdk-binary/examples autopilot-sdk-binary/tests autopilot-sdk-binary/docs
  git diff --cached --quiet || git commit -q -m "Sync the SDK into autopilot-sdk-binary"
  git add "$BIN/autopilot-linux-x86_64" $( (( WINDOWS )) && echo "$BIN/autopilot-windows-x86_64.exe")
  git diff --cached --quiet || git commit -q -m "binary"
  say "Committed: $(git log --oneline -1)"
  if (( PUSH )); then
    branch=$(git branch --show-current)
    git pull --rebase -q origin "$branch" || die "git pull --rebase failed; resolve it, then push"
    git push -q origin "$branch" || die "git push failed"
    git fetch -q origin "$branch"
    [[ $(git rev-parse HEAD) == $(git rev-parse "origin/$branch") ]] || die "origin/$branch does not match HEAD after the push"
    say "Pushed $branch ($(git rev-parse --short HEAD))"
  fi
fi
say "Done. macOS: build on a Mac (see the top of this script)."
