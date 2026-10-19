# Deploy the organiser stack with Docker Compose (AWS EC2)

One server runs everything organisers host. Participants only download the SDK
binary, run it next to their `agent.py`, and open the game page.

```
                 https://HOST  (Caddy: certificate, gzip, routing)
   /                → the Unity WebGL game (build11)
   /download/       → SDK binaries (Linux, Windows, macOS), example agents, guides
   /backend/api/*   → backend :8010   (participant routes only; admin routes stay internal)
   /llm/v1/*        → LLM gateway :8004 → Amazon Bedrock
                        backend → Postgres
```

Files: `docker-compose.yml`, `deploy/Dockerfile` (backend, gateway),
`deploy/Dockerfile.web` + `deploy/Caddyfile` (game page, downloads, HTTPS),
`deploy/.env.example`, `deploy/ec2-setup.sh`, `deploy/loadtest.py`,
`deploy/push-to-server.sh` + `deploy/server-update.sh` (deploy a checkout).

## The event server now, and automatic deploys

- **Server:** EC2 `ec2-13-200-183-134.ap-south-1` (t2.medium: 2 vCPU, 4 GB),
  user `ubuntu`, stack in `~/autopilot` (Compose project `autopilot`), served
  directly at **https://13-200-183-134.sslip.io** on ports 80/443. Caddy got the
  Let's Encrypt certificate by itself (kept in the `caddy-data` volume, renewed
  automatically). Switch to another name with
  `bash ~/autopilot/deploy/serve-direct.sh <hostname>` on the server: it backs up
  `.env`, changes only the site and port keys, stops the tunnel and waits for
  the certificate. The `zeroday` project (`/opt/zeroday`) that used to hold
  80/443 is stopped (containers kept, restart policy set to `no`; bring it back
  with `docker start` of its containers after stopping this `web` service).
  The Compose `tunnel` service (Cloudflare quick tunnel) remains as a fallback:
  `COMPOSE_PROFILES=tunnel` with `BIND_ADDRESS=127.0.0.1`.
- **Every push to `test_branch`** runs `.github/workflows/deploy.yml`: builds and
  smoke-tests the Windows and Linux binaries, downloads the game build (release
  `game-build11`), then `deploy/push-to-server.sh` copies everything to the server
  and `deploy/server-update.sh` rebuilds, restarts what changed and reloads the
  public test cases. The server's `.env` is never copied over or deleted. Only
  changed services restart; the certificate is kept.
- **Freeze during the event:** `gh variable set DEPLOY_PAUSED --body true`
  (and `--body false` after). A deploy that changes the backend restarts it,
  which loses the budget ledgers of matches in progress (see Operate).
- **By hand:** `deploy/push-to-server.sh ubuntu@<host> <ssh-key>`.
- **macOS binaries** are not rebuilt on every push (macOS runners cost 10x):
  run "Build SDK binaries" by hand (`gh workflow run build-binaries.yml`) and
  commit them to `autopilot-sdk-binary/bin/`.
- GitHub secrets: `EC2_HOST`, `EC2_USER`, `EC2_SSH_KEY` (a key made only for
  this workflow, comment `autopilot-github-actions-deploy` in the server's
  `~/.ssh/authorized_keys`; delete that line to revoke), `EC2_KNOWN_HOSTS`.

## 1. Launch the server

- **Region** ap-south-1 (Mumbai): close to participants and to Bedrock.
- **Instance**: c7i.xlarge (4 vCPU, 8 GB) or similar, Ubuntu 24.04+, 30 GB disk.
  Measured need is small (see Load below); what matters is network: every
  participant downloads ~41 MB of game (gzipped) plus a 14-24 MB binary.
- **Security group**: inbound TCP 22 (your IP), 80 and 443 (anywhere).
- **Elastic IP**: attach one, so the address survives a stop/start.

## 2. Install Docker and get the code

```bash
ssh ubuntu@<elastic-ip>
git clone -b deploy-docker https://github.com/nextgen-user/autopilot-unified.git
cd autopilot-unified
sudo bash deploy/ec2-setup.sh && exit      # log in again so docker works without sudo
```

The game build is not in git (190 MB). Either download the release on the server:

```bash
gh auth login        # or: curl -L -H "Authorization: Bearer $GITHUB_TOKEN" -o build11.zip <release asset URL>
gh release download game-build11 -R nextgen-user/autopilot-unified -p build11.zip
unzip -o build11.zip 'build11/Build/*' -d autopilot-game-engine_old
```

or copy it from an organiser machine that has it:

```bash
rsync -a autopilot-game-engine_old/build11/Build/ ubuntu@<elastic-ip>:autopilot/autopilot-game-engine_old/build11/Build/
```

## 3. Secrets

```bash
cp deploy/.env.example .env && chmod 600 .env
```

Fill in `.env` (never commit it):

| Setting | Value |
|---|---|
| `SITE_ADDRESS` | Your domain (A record → the Elastic IP), or `<elastic-ip>.sslip.io` with no domain. Caddy gets a Let's Encrypt certificate for it automatically. HTTPS is required: browsers block an HTTP page on a public address from calling the SDK on `127.0.0.1`. |
| `ADMIN_SECRET`, `SIMULATOR_SECRET`, `EVENT_GATEWAY_TOKEN` | `openssl rand -hex 16` each |
| `GATEWAY_TEAM_SECRET` | **Copy the current one** from `.runtime/quickstart/secrets.env` on the machine that ran `./quickstart.sh`: the team tokens already handed out (README_PARTICIPANT.md) are derived from it. A new secret means new tokens for everyone. |
| `AUTOPILOT_LLM_API_KEY` | Bedrock API key(s), comma-separated |

## 4. Start

```bash
docker compose up -d --build        # first build ~2 min (compresses the game once)
docker compose ps                   # all healthy
curl -s https://$SITE/backend/health
```

Test cases load themselves when the backend starts (public TC01–TC10 and the
hidden ones; edit them in `/admin`). Team IDs are logins only. Team gateway
tokens (normally they come with the login):

```bash
docker compose exec gateway python -m integration.gateway --team-tokens 0004 0005 0006
```

## 5. Participants

Same as README_PARTICIPANT.md, with the new address:

```bash
export AUTOPILOT_GATEWAY_URL=https://HOST/llm/v1
export AUTOPILOT_GATEWAY_KEY=<team token>
./autopilot-linux-x86_64 play --agent agent.py --backend https://HOST/backend
```

then open `https://HOST`, type the team ID and press play. Binaries:
`https://HOST/download/`. Update the URL in README_PARTICIPANT.md once the
address is final.

## Operate

| Task | Command |
|---|---|
| Logs | `docker compose logs -f backend` (or `gateway`, `web`) |
| New binaries / agents / guides | commit them, `git pull`, `docker compose up -d --build web` |
| Backend or gateway change | `git pull`, `docker compose up -d --build backend gateway` |
| Change starting coins | `AUTOPILOT_STARTING_COINS=...` in `.env`, `docker compose up -d backend` |
| Stop (data kept) | `docker compose down` |
| Wipe everything (database too) | `docker compose down -v`, then start again |

**Do not restart the backend during play.** Running matches keep their budget
ledgers and pause snapshots in the backend process; a restart loses them for
matches in progress (new matches are fine). For the same reason the backend
and gateway run one worker each: do not add `--workers`.

## Load (measured)

`deploy/loadtest.py` plays like N participants at once, the way the SDK talks
to the backend: each gets its own team (created through the admin API), logs
in, plays public test cases (a pause every few seconds with a placement or a
state report, then the result), and every 5th also runs a full eval over the
hidden test cases. It deletes its teams afterwards. No LLM calls, free.

```bash
pip install httpx
python deploy/loadtest.py https://HOST/backend ADMIN_SECRET --teams 300 --games 2 --interval 3
```

**On the event server (t2.medium, shared with zeroday), through the public URL,
2026-10-16:** 300 participants, 3 s between pauses (about twice the real
rate), 600 games + 60 evals (300 hidden-case games), 12,720 requests:

| Route | p50 | p99 | max | Errors |
|---|---|---|---|---|
| pause reply (`/api/reinforcements`) | 60 ms | 173 ms | 465 ms | none |
| level (`/api/team`) | 63 ms | 163 ms | 247 ms | none |
| results | 74 ms | 187 ms | 301 ms | none |

Server during the test: backend 39% of one core on average (171% peak while
300 teams were created and logged in), Postgres 5%, memory never below
1.7 GB free. Game download through the tunnel: 72 MB/s aggregate (10 at once,
30 MB each), so 300 first loads take about 3 minutes in total; browsers cache
the build for a day. 200 slow downloads held open at once: all served.

**Directly on 80/443 (no tunnel), same day:** see the next table; the tunnel's
in-flight limit below no longer applies.

**Limits found, and what to do:**

- **Cloudflare quick tunnel: about 200 requests in flight.** 300 logins fired at
  the same instant got 87 HTTP 429s from Cloudflare (they never reached the
  server). Normal play stays far below the limit (the 300-participant test had
  no errors), but a crowd of simultaneous slow requests (logins, LLM calls of
  many agents at once) can hit it. For the event, serve directly on ports
  80/443 (Caddy gets a certificate for `13-200-183-134.sslip.io` or a real
  domain: set `SITE_ADDRESS`, `HTTP_PORT=80`, `HTTPS_PORT=443`,
  `BIND_ADDRESS=0.0.0.0`, remove `COMPOSE_PROFILES=tunnel`), which needs the
  zeroday stack off those ports, or use a named Cloudflare tunnel on a domain.
- **Logins** are deliberately slow (password hashing). 300 at the same instant
  took 14 s; passwords are now hashed with fewer rounds and a passed check is
  remembered, so each game launch after the first login costs nothing.
- **t2.medium CPU credits:** a burstable instance in standard mode drops to
  ~40% of one core when its credits run out. Event load is near that baseline;
  switch the instance to unlimited mode or a t3.large for the event.
- **The LLM provider** is not load-tested (it costs money). 300 teams at the
  gateway's 60 calls/min per team could send thousands of calls per minute to
  Bedrock. Check the account's quota for the model (AWS console → Service
  Quotas → Amazon Bedrock, ap-south-1); `AUTOPILOT_GATEWAY_RATE_PER_MIN`
  lowers the per-team limit if needed.

Earlier fixes found by load tests: digit-only session IDs failing every pause
with HTTP 500 (int32 overflow); 3-10 s pauses after a restart while database
connections opened one by one (the pool now opens at startup); the LLM
gateway's ever-growing cache file (now in memory, bounded).

## Local try-out

```bash
cp deploy/.env.example .env      # SITE_ADDRESS=:80, HTTP_PORT=8088, secrets filled in
docker compose up -d --build
```

Open http://localhost:8088 (a browser treats localhost as secure, so the game
reaches the SDK on 127.0.0.1:5000) and run the SDK with
`--backend http://localhost:8088/backend`.

### Building the Unity release

Use Unity 6000.6.5f1 with WebGL support and the pinned packages in `unity-files/Packages`.
Run the editor with `-batchmode -nographics -quit -projectPath unity-files -executeMethod AutopilotReleaseBuild.WebGL`.
Set `AUTOPILOT_UNITY_BUILD` to the output directory. The builder disables development, debugging and profiling.
Publish `build11.zip` (top-level `build11/`, including Build and StreamingAssets) on release `game-build11`.
The tracked page retains the SDK routing shim; `build-info.json` records the compiled asset hashes.
The deployment workflow rebuilds Windows/Linux SDK binaries and downloads this Unity release.
