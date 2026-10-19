"""Run every demo agent through the live path the game uses.

For each file: the `autopilot play` startup check (static scan + contract),
then one reinforcement pause through a real SDK proxy, the sandboxed agent
worker and a referee. A stand-in referee (same rules as the backend) and a
fake LLM gateway run on loopback, so no backend, database or API key is
needed. Pass --backend to use the real backend referee instead.
"""
import argparse
import json
import logging
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

from autopilot_sdk.local_proxy import AgentFileError, make_proxy

HERE = Path(__file__).resolve().parent
TEAM = "DEMO"

# (file, layer, expected outcome); "approved a/b" means a of b proposals placed.
CASES = [
    ("good_agent.py", "control", "approved 2/2"),
    ("gateway_llm.py", "control", "approved 1/1"),
    ("forbidden_import.py", "static", "rejected"),
    ("direct_provider.py", "static", "rejected"),
    ("missing_function.py", "static", "rejected"),
    ("runtime_provider.py", "sandbox", "blocked"),
    ("local_llm.py", "sandbox", "blocked"),
    ("unlisted_host.py", "sandbox", "blocked"),
    ("raw_ip_socket.py", "sandbox", "blocked"),
    ("obfuscated_import.py", "sandbox", "blocked"),
    ("guard_tamper.py", "sandbox", "blocked"),
    ("subprocess_curl.py", "sandbox", "blocked"),
    ("native_ctypes.py", "sandbox", "blocked"),
    ("swallowed_violation.py", "sandbox", "blocked"),
    ("timeout_agent.py", "limits", "timeout"),
    ("exception_agent.py", "limits", "error"),
    ("bad_output.py", "limits", "error"),
    ("out_of_bounds.py", "referee", "approved 1/2"),
    ("occupied_cell.py", "referee", "approved 0/1"),
    ("duplicate_cells.py", "referee", "approved 1/2"),
]

SNAPSHOT = {
    "teamID": TEAM, "sessionID": "security-demo", "elapsedSeconds": 20.0,
    "counts": {"activeDefenceCount": 1, "activeEnemyCount": 2,
               "enemiesQueued": 3, "totalEnemiesInWave": 5},
    "defences": [{"id": "defenceShield_0", "type": "defenceShield", "health": 600.0,
                  "maxHealth": 600.0, "position": {"x": 1, "y": 2}}],
    "enemies": [
        {"id": "EnemClown_0", "type": "EnemClown", "health": 70.0, "maxHealth": 100.0,
         "position": {"x": 4, "y": 2}, "isAttacking": False, "isDead": False},
        {"id": "EnemyMonk_0", "type": "EnemyMonk", "health": 100.0, "maxHealth": 100.0,
         "position": {"x": 7, "y": 4}, "isAttacking": False, "isDead": False},
    ],
    "candidatePlacements": [],
}


class _Json(BaseHTTPRequestHandler):
    def reply(self, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> dict:
        return json.loads(self.rfile.read(int(self.headers["Content-Length"])))

    def log_message(self, *args):
        pass


class Referee(_Json):
    """Same rules as the backend referee: on the grid, a known defence, not on
    a living defence, not twice in one batch. Never moves a placement."""

    def do_POST(self):  # noqa: N802
        snapshot = self.body()
        taken = {(d["position"]["x"], d["position"]["y"])
                 for d in snapshot["defences"] if d["health"] > 0}
        approved = []
        for item in snapshot["candidatePlacements"]:
            pos = item["defenceGridPosition"]
            cell = (pos["x"], pos["y"])
            if 0 <= cell[0] <= 10 and 0 <= cell[1] <= 5 and item["defenceId"] in (0, 1, 2, 3) \
                    and cell not in taken:
                taken.add(cell)
                approved.append(item)
        self.reply({"placeDefences": bool(approved), "defencePlacementData": approved})


class Gateway(_Json):
    """Fake organiser LLM gateway: always proposes one archer."""

    def do_POST(self):  # noqa: N802
        self.body()
        answer = {"placements": [{"defenceId": 2, "defenceGridPosition": {"x": 1, "y": 4}}]}
        self.reply({"model": "demo", "choices": [{"index": 0, "message": {
            "role": "assistant", "content": json.dumps(answer)}}]})


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def _serve(handler) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _outcome(lines: list[str], decision: dict) -> tuple[str, str]:
    for line in lines:
        if m := re.search(r"agent blocked: (.*)", line, re.S):
            return "blocked", m.group(1)
        if m := re.search(r"exceeded ([\d.]+)s", line):
            return "timeout", f"worker stopped after {m.group(1)} s"
        if m := re.search(r"agent (?:error|load_error): (.*)", line, re.S):
            return "error", m.group(1).strip().splitlines()[0]
    dropped = sum("Dropping malformed candidate" in line for line in lines)
    sent = next((int(m.group(1)) for line in lines
                 if (m := re.search(r"Reinforcement: (\d+) candidate", line))), 0)
    approved = len(decision.get("defencePlacementData") or [])
    detail = ", ".join(
        f"({p['defenceGridPosition']['x']},{p['defenceGridPosition']['y']})"
        for p in decision.get("defencePlacementData") or []) or "nothing placed"
    if dropped:
        detail += f"; {dropped} off-board dropped by the SDK"
    return f"approved {approved}/{sent + dropped}", detail


def run_case(filename: str, referee_url: str, timeout_s: float) -> tuple[str, str]:
    path = HERE / filename
    try:
        proxy = make_proxy("127.0.0.1", 0, path, referee_url, reinforcement_timeout_s=timeout_s)
    except AgentFileError as exc:
        return "rejected", str(exc)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    collect = _Collect()
    logging.getLogger().addHandler(collect)
    try:
        url = f"http://127.0.0.1:{proxy.server_address[1]}/api/reinforcements"
        decision = requests.post(url, json=SNAPSHOT, timeout=timeout_s + 10).json()
    finally:
        logging.getLogger().removeHandler(collect)
        proxy.shutdown()
        proxy.server_close()
    return _outcome(collect.lines, decision)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", help="run one example file only")
    parser.add_argument("--timeout", type=float, default=2.0, help="agent time limit (s)")
    parser.add_argument("--backend", help="real backend URL for the referee, e.g. http://127.0.0.1:8010")
    args = parser.parse_args()
    cases = [c for c in CASES if not args.agent or c[0] == args.agent]
    if not cases:
        parser.error("choose an example file from this directory")

    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    # Lets obfuscated_import.py find an `anthropic` package, as if installed.
    sys.path.insert(0, str(HERE / "fake_site_packages"))
    gateway, gateway_url = _serve(Gateway)
    os.environ["AUTOPILOT_GATEWAY_URL"] = gateway_url + "/v1"
    referee, referee_url = (None, args.backend.rstrip("/")) if args.backend else _serve(Referee)

    print(f"Referee: {'backend ' + referee_url if args.backend else 'stand-in (backend rules)'}"
          f"   LLM gateway: {gateway_url} (fake)   agent time limit: {args.timeout:g} s\n")
    print(f"{'LAYER':8} {'AGENT':24} {'RESULT':14} DETAIL")
    results, failures, layer = [], 0, None
    try:
        for filename, case_layer, expected in cases:
            if layer and case_layer != layer:
                print()
            layer = case_layer
            started = time.monotonic()
            outcome, detail = run_case(filename, referee_url, args.timeout)
            ok = outcome == expected
            failures += not ok
            mark = "" if ok else f"   <-- expected {expected}"
            print(f"{case_layer:8} {filename:24} {outcome:14} {detail[:110]}{mark}")
            results.append({"file": filename, "layer": case_layer, "expected": expected,
                            "outcome": outcome, "detail": detail,
                            "seconds": round(time.monotonic() - started, 2)})
        # The SDK keeps working after every hostile agent.
        after, _ = run_case("good_agent.py", referee_url, args.timeout)
        print(f"\nAfter all of the above, good_agent.py again: {after}")
        failures += after != "approved 2/2"
    finally:
        gateway.shutdown()
        if referee:
            referee.shutdown()
    if not args.agent:
        (HERE / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"\n{len(results) - failures if failures else len(results)}/{len(results)} "
          f"matched the expected outcome." + ("" if args.agent else f" Details: {HERE / 'results.json'}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
