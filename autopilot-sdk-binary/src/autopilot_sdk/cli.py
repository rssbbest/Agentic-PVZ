"""CLI entrypoint (doc §4, §7).

Participants: `autopilot` alone opens the interactive console (interactive.py)
Participants: verify | play [--test-case CODE | --all] | eval
Legacy:       register | submit | status | leaderboard | run --offline-demo
Organisers:   serve (the SDK service the backend calls at SDK_URL)
"""
import argparse
import errno
import hashlib
import sys

from autopilot_sdk import __version__
from autopilot_sdk.config import load_config


def _cmd_version(_args: argparse.Namespace) -> int:
    from autopilot_sdk import updater

    print(f"autopilot {__version__} (build {updater.identity()[:12]})")
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    """Update to the organisers' latest SDK now (the SDK also does this by itself)."""
    import os

    from autopilot_sdk import updater

    backend = args.backend or os.environ.get("AUTOPILOT_BACKEND_URL") or load_config().event_backend_url
    base = updater.download_base(backend)
    manifest = updater.fetch_manifest(base) if base else None
    if manifest is None:
        print(f"Cannot read the latest SDK from {base or backend}")
        return 1
    if updater.wanted(manifest) is None:
        print(f"autopilot {__version__} (build {updater.identity()[:12]}) is the latest SDK")
        return 0
    if updater.disabled():
        print("A newer SDK is out, but updates are turned off (AUTOPILOT_NO_UPDATE)")
        return 1
    updater.update_if_needed(backend)  # starts the new SDK with these arguments on success
    return 1


def _cmd_verify(args: argparse.Namespace) -> int:
    from autopilot_sdk.checks import verify_agent

    report = verify_agent(args.agent, dry_run=not args.no_dry_run)
    for check in report.checks:
        print(f"{'✓' if check.ok else '✗'} {check.name}" + (f": {check.detail}" if check.detail else ""))
    if report.passed:
        print(f"✓ {args.agent} is ready to play")
        return 0
    if any(c.name == "Safety scan" and not c.ok for c in report.checks):
        print("\nOnly the organisers' LLM gateway (autopilot_sdk.llm.LLMClient) may be used for LLM access.")
    print(f"Run `autopilot verify --agent {args.agent}` again after fixing.")
    return 1


def _demo_state(turn: int = 1):  # local offline demo, no backend
    from autopilot_sdk.contracts.schemas import MapInfo, Position, ResourceState, TurnState

    return TurnState(
        match_id="m_local_demo",
        turn_number=turn,
        max_turns=40,
        time_limit_seconds=5.0,
        map=MapInfo(
            width=20,
            height=20,
            obstacles=[Position(x=3, y=3)],
            spawn_points=[Position(x=0, y=0)],
            base_position=Position(x=10, y=10),
        ),
        zombies=[],
        defenses=[],
        resources=ResourceState(currency=250, energy=80),
        score_so_far=0.0,
        events=[],
    )


def _cmd_run(args: argparse.Namespace) -> int:
    from autopilot_sdk.runtime.loader import load_agent_class
    from autopilot_sdk.runtime.orchestrator import TurnOrchestrator
    from autopilot_sdk.telemetry.logger import configure, log_event
    from autopilot_sdk.verifier.static_scan import scan_file

    cfg = load_config()
    configure(match_id="m_local_demo", sdk_version=cfg.sdk_version)

    report = scan_file(args.agent)
    if not report.passed:
        print(f"✗ Static verification failed for {args.agent}:")
        for v in report.violations:
            print(f"  - {v}")
        return 2

    try:
        load_agent_class(args.agent)
    except Exception as exc:
        print(f"✗ Agent contract check failed: {exc}")
        return 2

    if not args.offline_demo:
        print(
            "Matches are run by the backend. Use `autopilot submit --agent "
            f"{args.agent} --team-id <id>` to submit, or re-run with "
            "--offline-demo to exercise one isolated turn locally."
        )
        try:
            with open(args.agent, "rb") as f:
                print(f"source_sha256: {hashlib.sha256(f.read()).hexdigest()}")
        except Exception:
            pass
        return 3

    orch = TurnOrchestrator(args.agent, default_timeout_s=cfg.default_time_limit_s)
    state = _demo_state()
    log_event("offline_demo_start", agent=args.agent)
    decision = orch.run_turn(state)
    log_event(
        "offline_demo_turn",
        turn=state.turn_number,
        actions=len(decision.actions),
        metadata=decision.metadata,
    )
    print(f"✓ offline demo turn complete: {len(decision.actions)} action(s)")
    if decision.metadata:
        print(f"  metadata: {decision.metadata}")
    return 0


def _client():
    from autopilot_sdk.client.backend_client import BackendClient

    cfg = load_config()
    return BackendClient(cfg.backend_base_url, max_retries=cfg.max_backend_retries)


def _print_submission(sub: dict) -> None:
    runs = sub.get("runs", [])
    counts: dict[str, int] = {}
    for r in runs:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    score = sub.get("total_score")
    print(
        f"submission {sub['id']} — {sub['status']}"
        + (f", score {score:.2f}" if score is not None else "")
        + f" ({len(runs)} runs; {breakdown})"
    )


def _cmd_serve(args: argparse.Namespace) -> int:
    import logging

    from autopilot_sdk.server import serve

    cfg = load_config()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    serve(
        args.host or cfg.service_host,
        args.port or cfg.service_port,
        args.timeout or cfg.default_time_limit_s,
        args.reinforcement_timeout or cfg.reinforcement_timeout_s,
        args.agent_dir or cfg.agent_dir,
    )
    return 0


def _cmd_play(args: argparse.Namespace) -> int:
    import logging

    from autopilot_sdk.console import ParticipantLogHandler
    from autopilot_sdk.local_proxy import AgentFileError, LoginError, SuiteError, login, play

    cfg = load_config()
    # Participant-friendly lines; --verbose adds every HTTP request.
    root = logging.getLogger()
    root.handlers[:] = [ParticipantLogHandler()]
    root.setLevel(logging.INFO)
    logging.getLogger("autopilot.http").setLevel(logging.INFO if args.verbose else logging.WARNING)
    mode = getattr(args, "mode", "play")
    backend = (args.backend or cfg.backend_base_url).rstrip("/")
    auth = None
    try:
        if getattr(args, "team", None):
            password = args.password or __import__("getpass").getpass(f"Password for team {args.team}: ")
            reply = login(backend, args.team, password)
            from autopilot_sdk.local_proxy import RunContext, apply_login
            run = RunContext()
            apply_login(run, reply)
            auth = run.auth
            print(f"✓ Logged in as team {reply.get('teamID')} ({reply.get('teamName')})")
        run = play(
            args.host,
            args.port,
            args.agent,
            backend,
            args.reinforcement_timeout or cfg.reinforcement_timeout_s,
            run_all=getattr(args, "all", False),
            test_case=(getattr(args, "test_case", None) or "").upper() or None,
            mode=mode,
            auth=auth,
            board=getattr(args, "board", "private"),
        )
    except (AgentFileError, SuiteError, LoginError) as exc:
        print(f"✗ {exc}")
        return 1
    except OSError as exc:
        if exc.errno not in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", None)):
            raise
        print(f"✗ Port {args.port} is already in use: another `autopilot` (or "
              f"quickstart's SDK) is running. Stop it, or pass --port.")
        return 1
    if mode == "eval":
        return 0 if (run.eval or {}).get("result") else 1
    return 0


def _cmd_register(args: argparse.Namespace) -> int:
    from autopilot_sdk.client.backend_client import BackendError

    try:
        team = _client().create_team(args.name)
    except BackendError as exc:
        print(f"✗ Could not register team: {exc}")
        return 1
    print(f"✓ Registered team '{team['name']}' — team id {team['id']}")
    return 0


def _cmd_submit(args: argparse.Namespace) -> int:
    from autopilot_sdk.client.backend_client import BackendError
    from autopilot_sdk.verifier.static_scan import scan_file

    # Same pre-flight the SDK service runs, so obvious failures never reach
    # the backend (§14: failed verification uploads nothing).
    report = scan_file(args.agent)
    if not report.passed:
        print(f"✗ Static verification failed for {args.agent}:")
        for v in report.violations:
            print(f"  - {v}")
        return 2
    try:
        from autopilot_sdk.runtime.loader import load_agent_class

        load_agent_class(args.agent)
    except Exception as exc:
        print(f"✗ Agent contract check failed: {exc}")
        return 2

    client = _client()
    try:
        sub = client.submit(args.team_id, args.agent)
        print(f"✓ Submitted — submission id {sub['id']}, {len(sub['runs'])} runs queued")
        if args.no_wait:
            return 0
        sub = client.wait_for_submission(sub["id"], timeout_s=args.wait_timeout)
    except BackendError as exc:
        print(f"✗ {exc}")
        return 1
    _print_submission(sub)
    if any(r["status"] == "invalid" for r in sub["runs"]):
        print(f"  Some runs were invalid — see `autopilot status {sub['id']} --runs`.")
    return 0 if sub["status"] == "completed" else 1


def _cmd_status(args: argparse.Namespace) -> int:
    from autopilot_sdk.client.backend_client import BackendError

    client = _client()
    try:
        sub = client.get_submission(args.submission_id)
        _print_submission(sub)
        if args.runs:
            for r in sub["runs"]:
                run = client.get_run(r["id"])
                violations = (run.get("sdk_status") or {}).get("violations") or []
                line = f"  run {run['id']} (config {run['enemy_config_id']}): {run['status']}"
                if run.get("score") is not None:
                    line += f", score {run['score']:.2f}"
                for v in violations:
                    line += f"\n    - {v['type']}: {v['message']}"
                print(line)
    except BackendError as exc:
        print(f"✗ {exc}")
        return 1
    return 0


def _cmd_leaderboard(args: argparse.Namespace) -> int:
    from autopilot_sdk.client.backend_client import BackendError

    try:
        rows = _client().leaderboard(args.limit)
    except BackendError as exc:
        print(f"✗ {exc}")
        return 1
    if not rows:
        print("Leaderboard is empty.")
        return 0
    for row in rows:
        print(f"{row['rank']:>3}. {row['team_name']:<30} {row['score']:.2f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="autopilot", description="Autopilot SDK runner")
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("version", help="Print SDK version")
    pv.set_defaults(func=_cmd_version)

    pupd = sub.add_parser("update", help="Update to the organisers' latest SDK now")
    pupd.add_argument("--backend", default=None, help="the event backend (default: the event server)")
    pupd.set_defaults(func=_cmd_update)

    pver = sub.add_parser("verify", help="Check agent.py and dry-run it on a sample opening and pause")
    pver.add_argument("--agent", required=True, help="Path to participant agent.py")
    pver.add_argument("--no-dry-run", action="store_true", help="only the static checks")
    pver.set_defaults(func=_cmd_verify)

    prun = sub.add_parser("run", help="Run one isolated turn locally (--offline-demo)")
    prun.add_argument("--agent", required=True)
    prun.add_argument("--team-key", default="", help="Team API key (unused offline)")
    prun.add_argument(
        "--offline-demo",
        action="store_true",
        help="Run one isolated turn locally without backend",
    )
    prun.set_defaults(func=_cmd_run)

    preg = sub.add_parser("register", help="Register a team with the backend")
    preg.add_argument("--name", required=True)
    preg.set_defaults(func=_cmd_register)

    psub = sub.add_parser("submit", help="Verify agent.py and submit it to the backend")
    psub.add_argument("--agent", required=True)
    psub.add_argument("--team-id", type=int, required=True)
    psub.add_argument("--no-wait", action="store_true", help="Don't wait for scoring")
    psub.add_argument("--wait-timeout", type=float, default=1800.0, help="Seconds to wait")
    psub.set_defaults(func=_cmd_submit)

    pst = sub.add_parser("status", help="Show a submission's status and score")
    pst.add_argument("submission_id", type=int)
    pst.add_argument("--runs", action="store_true", help="Show every run")
    pst.set_defaults(func=_cmd_status)

    plb = sub.add_parser("leaderboard", help="Show the leaderboard")
    plb.add_argument("--limit", type=int, default=50)
    plb.set_defaults(func=_cmd_leaderboard)

    psrv = sub.add_parser(
        "serve", help="Run the SDK service the backend calls (/reinforcements and /run)"
    )
    psrv.add_argument("--host", default=None, help="Bind address (default 0.0.0.0)")
    psrv.add_argument("--port", type=int, default=None, help="Port (default 8001)")
    psrv.add_argument("--timeout", type=float, default=None, help="Legacy /run agent limit, s")
    psrv.add_argument(
        "--reinforcement-timeout", type=float, default=None,
        help="Reinforcement hook time limit (max 4.0s; backend request timeout is 4.5s)",
    )
    psrv.add_argument(
        "--agent-dir", default=None,
        help="Directory containing team agent files (also AUTOPILOT_AGENT_DIR)",
    )
    psrv.set_defaults(func=_cmd_serve)

    def game_options(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--agent", required=True, help="Path to participant agent.py")
        parser.add_argument(
            "--backend", default=None,
            help="Backend base URL (default AUTOPILOT_BACKEND_URL or http://localhost:8010)",
        )
        parser.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
        parser.add_argument("--port", type=int, default=5000, help="Port the game calls (default 5000)")
        parser.add_argument(
            "--reinforcement-timeout", type=float, default=None,
            help="get_reinforcements limit (play: max 15 s; serve: max 4 s)",
        )
        parser.add_argument("--team", default=None,
                            help="log in from here with this team ID (the game's login does the same)")
        parser.add_argument("--password", default=None, help="with --team; asked for when left out")
        parser.add_argument("--verbose", action="store_true",
                            help="also print every HTTP request the game and agent make")

    pplay = sub.add_parser(
        "play", help="Run agent.py locally for the game on a public test case"
    )
    game_options(pplay)
    pplay.add_argument("--test-case", default=None, help="test case code to play, e.g. TC03")
    pplay.add_argument("--all", action="store_true",
                       help="play every public test case once (press play for each) and print the average")
    pplay.set_defaults(func=_cmd_play, mode="play")

    peval = sub.add_parser(
        "eval", help="Publish a run for the leaderboard: every test case of a board once, one frozen agent.py"
    )
    game_options(peval)
    peval.add_argument("--board", choices=("public", "private"), default="private",
                       help="public: the public test cases (live board); private: the hidden ones (final board, "
                            "when the organisers open it). Default private")
    peval.set_defaults(func=_cmd_play, mode="eval")
    return p


def _utf8_output() -> None:
    """Keep ✓/✗ output from crashing when Windows output is piped or redirected.

    Interactive Windows consoles already accept Unicode; redirected streams fall
    back to the ANSI code page (cp1252), which cannot encode those symbols.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _utf8_output()
    from autopilot_sdk.updater import clean_old

    clean_old()  # NEW: Windows: the program an update replaced
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        from autopilot_sdk.launcher import relaunch_in_terminal, started_without_terminal

        if started_without_terminal() and relaunch_in_terminal():
            return 0  # double-clicked on Linux: the console opens in a terminal window
        from autopilot_sdk.interactive import main as interactive

        return interactive()
    args = build_parser().parse_args(argv)
    if getattr(args, "mode", None) in ("play", "eval"):
        # NEW: play and publish only with the latest SDK (updates and restarts if older)
        from autopilot_sdk import updater

        import os

        backend = (getattr(args, "backend", None) or os.environ.get("AUTOPILOT_BACKEND_URL")
                   or load_config().event_backend_url)
        updater.update_if_needed(backend)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
