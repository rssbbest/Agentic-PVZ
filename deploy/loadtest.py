"""Load test: N participants play at once against a hosted backend, the way
the SDK talks to it (no LLM calls, so it costs nothing).

    python deploy/loadtest.py https://HOST/backend ADMIN_SECRET --teams 300 --games 2 --interval 3

Each simulated participant gets its own team (created through the admin API,
named "loadtest-…"), logs in, then:
  * plays --games public test cases: POST /api/team with testCase, a pause
    every --interval seconds (POST /api/reinforcements with one candidate, or
    POST /api/state, as the SDK does), POST /api/results;
  * one in --eval-every participants also runs an eval: /api/eval/start, every
    hidden slot, /api/eval/{id}/finish with a small log.
Starts are spread over --ramp seconds. At the end every loadtest team is
deleted again (their games and evals go with them), unless --keep.
Prints requests per second, latency percentiles and every error per route.
Needs httpx.
"""
import argparse
import asyncio
import base64
import gzip
import random
import time

import httpx

lat: dict[str, list[float]] = {}
errors: dict[str, int] = {}
games_done = {"play": 0, "eval_slots": 0, "evals": 0}


def note(route: str, seconds: float, problem: str | None = None) -> None:
    lat.setdefault(route, []).append(seconds)
    if problem:
        errors[f"{route} {problem}"] = errors.get(f"{route} {problem}", 0) + 1


async def call(c: httpx.AsyncClient, method: str, route: str, body=None, token=None, label=None):
    t = time.monotonic()
    label = label or route
    try:
        r = await c.request(method, route, json=body, headers={"Authorization": f"Bearer {token}"} if token else None)
        ok = 200 <= r.status_code < 300
        note(label, time.monotonic() - t, None if ok else str(r.status_code))
        return r.json() if ok and r.content else None
    except Exception as exc:  # noqa: BLE001 - every failure is a finding
        note(label, time.monotonic() - t, type(exc).__name__)
        return None


def snapshot(team, sid, k, total, defences):
    alive = max(1, min(4, total - k // 2))
    enemies = [{"id": f"EnemyPirate_{j}", "type": random.choice(["EnemClown", "EnemyMonk", "EnemyWizard", "EnemyPirate"]),
                "health": 150.0, "maxHealth": 200.0, "position": {"x": random.randrange(2, 11), "y": random.randrange(6)},
                "isAttacking": False, "isDead": False} for j in range(alive)]
    return {"teamID": team, "sessionID": sid, "elapsedSeconds": 0.4 if k == 0 else 3.0 * k,
            "counts": {"activeDefenceCount": len(defences), "activeEnemyCount": alive,
                       "enemiesQueued": max(0, total - k), "totalEnemiesInWave": total},
            "defences": defences, "enemies": enemies}


async def one_game(c, team, token, level_request, pauses, interval, label):
    level = await call(c, "POST", "/api/team", {"teamID": team, **level_request}, token, f"/api/team ({label})")
    if not level or not level.get("sessionID"):
        return None
    sid, total = level["sessionID"], sum(e.get("count", 1) for e in level.get("enemies", []))
    defences = []
    for k in range(pauses):
        snap = snapshot(team, sid, k, total, defences)
        if random.random() < 0.3:  # the agent proposed nothing: the SDK reports the state
            await call(c, "POST", "/api/state", snap, token)
        else:
            free = [(x, y) for x in range(7) for y in range(6)
                    if not any(d["position"] == {"x": x, "y": y} for d in defences)]
            x, y = random.choice(free)
            body = {**snap, "candidatePlacements": [{"defenceId": random.randrange(4), "defenceGridPosition": {"x": x, "y": y}}]}
            decision = await call(c, "POST", "/api/reinforcements", body, token)
            for p in (decision or {}).get("defencePlacementData") or []:
                pos = p.get("defenceGridPosition") or {}
                defences.append({"id": f"d{len(defences)}", "type": "defenceSoilder", "health": 150.0,
                                 "maxHealth": 150.0, "position": {"x": pos.get("x", 0), "y": pos.get("y", 0)}})
        await asyncio.sleep(interval * random.uniform(0.8, 1.2))
    killed = random.randrange(0, total + 1)
    result = await call(c, "POST", "/api/results", {"teamID": team, "sessionID": sid, "success": killed == total,
                                                    "durationSeconds": pauses * 3.0, "enemiesKilled": killed}, token)
    return result


async def participant(c, i, args, cases, team, token):
    await asyncio.sleep(random.uniform(0, args.ramp))
    for _ in range(args.games):
        if await one_game(c, team, token, {"testCase": random.choice(cases)}, args.pauses, args.interval, "play"):
            games_done["play"] += 1
    if args.eval_every and i % args.eval_every == 0:
        ev = await call(c, "POST", "/api/eval/start", {"agentSource": "def get_reinforcements(state):\n    return []\n",
                                                        "sdkVersion": "loadtest"}, token)
        if ev:
            for slot in range(1, ev["slots"] + 1):
                if await one_game(c, team, token, {"evalId": ev["evalId"], "evalSlot": slot}, args.pauses,
                                  args.interval, "eval"):
                    games_done["eval_slots"] += 1
            log = base64.b64encode(gzip.compress(b'{"kind":"loadtest"}\n' * 200)).decode()
            if await call(c, "POST", f"/api/eval/{ev['evalId']}/finish", {"log": log, "summary": {}}, token,
                          "/api/eval/finish"):
                games_done["evals"] += 1


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("backend")
    ap.add_argument("admin_secret")
    ap.add_argument("--teams", type=int, default=300)
    ap.add_argument("--games", type=int, default=2, help="public test cases each participant plays")
    ap.add_argument("--pauses", type=int, default=12, help="pauses per game")
    ap.add_argument("--interval", type=float, default=3.0, help="seconds between pauses (real games: ~6)")
    ap.add_argument("--eval-every", type=int, default=5, help="every Nth participant also runs an eval (0: none)")
    ap.add_argument("--ramp", type=float, default=30.0, help="spread the starts over this many seconds")
    ap.add_argument("--keep", action="store_true", help="keep the loadtest teams afterwards")
    args = ap.parse_args()
    admin = {"X-Admin-Secret": args.admin_secret}
    limits = httpx.Limits(max_connections=args.teams + 20, max_keepalive_connections=args.teams + 20)
    async with httpx.AsyncClient(base_url=args.backend.rstrip("/"), timeout=60, limits=limits) as c:
        cases = [t["code"] for t in (await c.get("/api/test-cases")).json()]
        stamp = int(time.time()) % 100000
        print(f"creating {args.teams} loadtest teams…", flush=True)

        async def make(i):
            r = await c.post("/admin/api/teams", headers=admin,
                             json={"name": f"loadtest-{stamp}-{i}", "password": "loadtest-pass", "approved": True})
            r.raise_for_status()
            team = r.json()["teamID"]
            login = (await c.post("/api/login", json={"teamID": team, "password": "loadtest-pass"})).json()
            return team, login["token"], r.json()["id"]

        made = []
        for chunk in range(0, args.teams, 25):
            made += await asyncio.gather(*(make(i) for i in range(chunk, min(args.teams, chunk + 25))))
        print(f"teams {made[0][0]}..{made[-1][0]}; playing", flush=True)
        t0 = time.monotonic()
        try:
            await asyncio.gather(*(participant(c, i, args, cases, team, token)
                                   for i, (team, token, _) in enumerate(made)))
        finally:
            wall = time.monotonic() - t0
            total = sum(len(v) for v in lat.values())
            print(f"\n{args.teams} participants · {total} requests in {wall:.0f} s = {total / wall:.0f} req/s · "
                  f"games: {games_done}")
            for route, v in sorted(lat.items()):
                v.sort()
                q = lambda p: v[min(len(v) - 1, int(p * len(v)))] * 1000  # noqa: E731
                print(f"  {route:24s} n={len(v):6d}  p50 {q(.5):6.0f} ms  p95 {q(.95):6.0f} ms  "
                      f"p99 {q(.99):6.0f} ms  max {v[-1] * 1000:6.0f} ms")
            print("  errors:", errors or "none")
            if not args.keep:
                for chunk in range(0, len(made), 25):
                    await asyncio.gather(*(c.delete(f"/admin/api/teams/{tid}", headers=admin)
                                           for _, _, tid in made[chunk:chunk + 25]))
                print(f"deleted the {len(made)} loadtest teams")


if __name__ == "__main__":
    asyncio.run(main())
