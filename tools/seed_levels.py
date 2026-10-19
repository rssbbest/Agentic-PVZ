"""Create game teams and their levels through the backend's admin API.

    python tools/seed_levels.py --team TEAM
    python tools/seed_levels.py --test-cases autopilot-backend/test_cases/reinforcement_test_cases.json

`--team` copies the backend's default level (seeded for TEAM1) to a team.
`--test-cases` creates one team per case, named TC01, TC02 ..., with the case's
enemies, spawn settings and game settings, and the default starting defences
(none: levels start with an empty board).

The game's team box takes four digits and the backend resolves digits as the
team's numeric ID, so the ID to type (e.g. 0008) is printed for every team.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

LEVEL_KEYS = ("defencePlacementData", "enemies", "spawnSettings", "gameSettings")


def call(method, url, body=None, secret=None):
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Admin-Secret"] = secret
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def default_level(backend, secret):
    code, rows = call("GET", f"{backend}/api/team-data", secret=secret)
    if code != 200 or not rows:
        sys.exit(f"Could not read team data from the backend (HTTP {code})")
    row = next((r for r in rows if r.get("teamName", "").lower() == "team1"), rows[0])
    return {k: row[k] for k in LEVEL_KEYS if k in row}


def upsert(backend, secret, name, level):
    code, _ = call("POST", f"{backend}/teams", {"name": name})
    if code not in (200, 201, 409):
        sys.exit(f"Could not create team {name} (HTTP {code})")
    code, body = call("POST", f"{backend}/api/team-data/{name}", level, secret)
    if code != 200:
        sys.exit(f"Could not store the level for {name} (HTTP {code}): {body}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="http://127.0.0.1:8010")
    parser.add_argument("--admin-secret", default="dev-admin")
    parser.add_argument("--team", action="append", default=[])
    parser.add_argument("--test-cases")
    args = parser.parse_args()
    base = default_level(args.backend, args.admin_secret)
    rows = []
    for name in args.team:
        upsert(args.backend, args.admin_secret, name, base)
        rows.append((name, "default level"))
    if args.test_cases:
        with open(args.test_cases, encoding="utf-8") as stream:
            cases = json.load(stream)
        for case in cases:
            name = f"TC{int(case['testCaseId']):02d}"
            level = {**base, **{k: case[k] for k in ("enemies", "spawnSettings", "gameSettings") if k in case}}
            upsert(args.backend, args.admin_secret, name, level)
            hold = case.get("gameSettings", {}).get("reinforcementHoldSeconds")
            total = sum(e.get("count", 0) for e in case.get("enemies", []))
            rows.append((name, f"{case.get('name', '')} ({total} enemies, hold {hold}s)"))
    ids = {r["teamName"].lower(): r["teamId"]
           for r in call("GET", f"{args.backend}/api/team-data", secret=args.admin_secret)[1]}
    print("  Type in the game   Team")
    for name, label in rows:
        print(f"  {ids.get(name.lower(), 0):04d}               {name}  {label}")


if __name__ == "__main__":
    main()
