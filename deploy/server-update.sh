#!/usr/bin/env bash
# Runs on the server after deploy/push-to-server.sh copied the code: rebuild,
# restart, reload the public test cases, print the public URL.
set -euo pipefail
cd ~/autopilot
test -f .env || { echo "~/autopilot/.env is missing (copy deploy/.env.example and fill it in)" >&2; exit 1; }
nice -n 10 docker compose build 2>&1 | tail -3
docker compose up -d 2>&1 | grep -vE "Running|Waiting|Healthy" || true
for _ in $(seq 1 90); do
  docker compose exec -T backend python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=3)" >/dev/null 2>&1 && break
  sleep 2
done
docker image prune -f >/dev/null
docker compose ps --format '{{.Service}}\t{{.Status}}'
site=$(grep -E '^SITE_ADDRESS=' .env | tail -1 | cut -d= -f2- || true)
if [[ -n $site && $site != :* ]]; then
  url="https://$site"
else
  url=$(docker compose logs tunnel 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)
fi
if [[ -n $url ]]; then
  echo "Public URL: $url"
  curl -fsS -o /dev/null --max-time 20 "$url/backend/health" && echo "Public URL answers"
fi
