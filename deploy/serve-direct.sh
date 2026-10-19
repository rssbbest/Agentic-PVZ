#!/usr/bin/env bash
# Run on the server: stop using the Cloudflare tunnel and serve directly on
# ports 80/443 with an automatic HTTPS certificate for HOSTNAME.
#
#   bash ~/autopilot/deploy/serve-direct.sh 13-200-183-134.sslip.io
#
# Needs ports 80 and 443 free. Changes only these keys of ~/autopilot/.env
# (a copy is kept as .env.bak-<time>): SITE_ADDRESS, HTTP_PORT, HTTPS_PORT,
# BIND_ADDRESS, COMPOSE_PROFILES.
set -euo pipefail
HOST=${1:?usage: serve-direct.sh HOSTNAME}
cd ~/autopilot
if ss -ltnH '( sport = :80 or sport = :443 )' | grep -q .; then
  echo "Ports 80/443 are in use by another program; nothing changed:" >&2
  ss -ltnp '( sport = :80 or sport = :443 )' >&2 || true
  exit 1
fi
cp -p .env ".env.bak-$(date +%Y%m%d-%H%M%S)"
set_key() {  # set_key KEY VALUE  (VALUE empty: remove the key)
  grep -v "^$1=" .env > .env.new || true
  [[ -n $2 ]] && echo "$1=$2" >> .env.new
  cat .env.new > .env && rm -f .env.new
}
set_key SITE_ADDRESS "$HOST"
set_key HTTP_PORT 80
set_key HTTPS_PORT 443
set_key BIND_ADDRESS 0.0.0.0
set_key COMPOSE_PROFILES ""
docker compose stop tunnel 2>/dev/null || true
docker compose up -d web 2>&1 | grep -vE "Running|Waiting|Healthy" || true
for _ in $(seq 1 60); do
  curl -fsS -o /dev/null --max-time 10 "https://$HOST/backend/health" && { echo "Live: https://$HOST"; exit 0; }
  sleep 3
done
echo "https://$HOST does not answer yet; certificate log:" >&2
docker compose logs --tail 30 web >&2
exit 1
