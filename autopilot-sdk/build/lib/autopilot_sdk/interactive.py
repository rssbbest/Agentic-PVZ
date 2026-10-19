"""The interactive console: `autopilot` with no arguments.

Start the binary once; choose agent.py, a mode and a test case; open the game
page and press play. Ctrl+C stops the run and comes back to the menu, where
another test case or mode can be picked without restarting anything. agent.py
edits apply at the next pause in play (eval plays a frozen copy).

Choices are remembered in ~/.autopilot/state.json for the next start.
"""
from __future__ import annotations

import errno
import json
import os
import sys
from pathlib import Path

import requests
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from autopilot_sdk import __version__
from autopilot_sdk.config import load_config

STATE_FILE = Path.home() / ".autopilot" / "state.json"
LOGO = [
    r"    ___   __  ____________  ____  ______    ____  ______",
    r"   /   | / / / /_  __/ __ \/ __ \/  _/ /   / __ \/_  __/",
    r"  / /| |/ / / / / / / / / / /_/ // // /   / / / / / /   ",
    r" / ___ / /_/ / / / / /_/ / ____// // /___/ /_/ / / /    ",
    r"/_/  |_\____/ /_/  \____/_/   /___/_____/\____/ /_/     ",
]
GRADIENT = ["#35e0c2", "#3fd0d0", "#52bde0", "#66aaf0", "#7aa2ff"]
ACCENT = "#35e0c2"

con = Console(highlight=False)


# ── State ─────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


# ── Pieces ────────────────────────────────────────────────────────────────────
def banner() -> None:
    con.print()
    pad = " " * max(0, (con.width - max(map(len, LOGO))) // 2)  # centre the logo as one block
    for line, colour in zip(LOGO, GRADIENT):
        con.print(Text(pad + line, style=f"bold {colour}"), overflow="crop", no_wrap=True)
    con.print(Align.center(Text(f"AGENT CONSOLE  ·  SDK {__version__}", style="bold #8a97b1")))
    con.print()


def ok(text: str) -> str:
    return f"[bold green]●[/] {text}"


def bad(text: str) -> str:
    return f"[bold red]●[/] {text}"


def probe(backend: str) -> dict:
    """What the backend offers: health, public test cases, hidden count."""
    info = {"online": False, "cases": [], "hidden": None, "error": None}
    try:
        requests.get(backend.rstrip("/") + "/health", timeout=8).raise_for_status()
        info["online"] = True
        info["cases"] = requests.get(backend.rstrip("/") + "/api/test-cases", timeout=8).json()
        resp = requests.get(backend.rstrip("/") + "/api/eval/info", timeout=8)
        info["hidden"] = resp.json().get("hiddenTestCases") if resp.ok else None
    except (requests.RequestException, ValueError) as exc:
        info["error"] = str(exc).split("\n")[0][:120]
    return info


def status_panel(state: dict, info: dict, auth: dict) -> Panel:
    from autopilot_sdk.local_proxy import gateway_upstream

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="#8a97b1", justify="right")
    grid.add_column()
    backend = state.get("backend", "")
    grid.add_row("BACKEND", (ok(backend) if info["online"] else bad(f"{backend}  [red]{info['error'] or 'offline'}[/]")))
    cases = info["cases"]
    hidden = info["hidden"]
    grid.add_row("TEST CASES", f"{len(cases)} public" + (f"  ·  {hidden} hidden (eval)" if hidden is not None else ""))
    agent = state.get("agent")
    grid.add_row("AGENT", (ok(agent) if agent and Path(agent).is_file() else bad(agent or "not chosen")))
    team = auth.get("teamID")
    grid.add_row("TEAM", ok(f"{team} · {auth.get('teamName')}") if team
                 else "[#8a97b1]not logged in (log in on the game page, or [bold]L[/] here)[/]")
    llm = gateway_upstream(backend) if backend else None
    key = "token set" if os.environ.get("AUTOPILOT_GATEWAY_KEY") else "token comes with your login"
    grid.add_row("LLM", f"{llm}  ·  {key}" if llm else "[#8a97b1]no gateway (AUTOPILOT_GATEWAY_URL)[/]")
    return Panel(grid, title="[bold]SYSTEM STATUS[/]", title_align="left", border_style=ACCENT, box=box.ROUNDED,
                 padding=(1, 2))


def menu_panel() -> Panel:
    items = Table.grid(padding=(0, 2))
    items.add_column(style=f"bold {ACCENT}", justify="right")
    items.add_column()
    items.add_column(style="#8a97b1")
    for key, name, hint in (
        ("1", "PLAY", "run one public test case, as often as you like"),
        ("2", "PLAY ALL", "every public test case once, then the average"),
        ("3", "EVAL", "official run: every hidden test case once, scored by the backend"),
        ("4", "VERIFY", "check agent.py and dry-run it"),
        ("A", "agent.py", "choose another agent file"),
        ("L", "log in", "team ID and password (the game's login does the same)"),
        ("B", "backend", "change the server address"),
        ("Q", "quit", ""),
    ):
        items.add_row(key, name, hint)
    return Panel(items, title="[bold]MISSION[/]", title_align="left", border_style="#7aa2ff", box=box.ROUNDED,
                 padding=(1, 2))


def cases_table(cases: list[dict]) -> Table:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold #8a97b1", pad_edge=False)
    table.add_column("CODE", style=f"bold {ACCENT}")
    table.add_column("NAME")
    table.add_column("ENEMIES", justify="right")
    for c in cases:
        table.add_row(c.get("code", "?"), str(c.get("name", "")), str(c.get("enemies", "")))
    return table


def ask(prompt: str, default: str | None = None) -> str:
    return Prompt.ask(f"[bold {ACCENT}]›[/] {prompt}", default=default, console=con)


# ── Actions ───────────────────────────────────────────────────────────────────
def choose_backend(state: dict) -> None:
    default = state.get("backend") or os.environ.get("AUTOPILOT_BACKEND_URL") or load_config().event_backend_url
    url = ask("Backend address", default).strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    state["backend"] = url
    os.environ.pop("AUTOPILOT_GATEWAY_UPSTREAM", None)  # derive the LLM gateway from the new address
    save_state(state)


def choose_agent(state: dict) -> None:
    from autopilot_sdk.local_proxy import AgentFileError, check_agent_file

    while True:
        path = ask("Path to your agent.py", state.get("agent") or "agent.py").strip().strip('"').strip("'")
        candidate = Path(path).expanduser()
        if candidate.is_dir():
            candidate = candidate / "agent.py"
        try:
            check_agent_file(candidate)
        except AgentFileError as exc:
            con.print(bad(f"[red]{exc}[/]"))
            if not Confirm.ask("  Choose another file?", default=True, console=con):
                return
            continue
        state["agent"] = str(candidate.resolve())
        save_state(state)
        con.print(ok(f"agent.py ready: {state['agent']}"))
        return


def do_login(state: dict, auth: dict) -> None:
    from autopilot_sdk.local_proxy import LoginError, RunContext, apply_login, login

    team = ask("Team ID (4 digits)", auth.get("teamID") or state.get("team"))
    password = Prompt.ask(f"[bold {ACCENT}]›[/] Password", password=True, console=con)
    try:
        reply = login(state["backend"], team, password)
    except LoginError as exc:
        con.print(bad(f"[red]Login failed: {exc}[/]"))
        return
    run = RunContext()
    apply_login(run, reply)
    auth.clear()
    auth.update(run.auth)
    state["team"] = reply.get("teamID")
    save_state(state)
    con.print(ok(f"Logged in as team {reply.get('teamID')} ({reply.get('teamName')})"
                 + (" · LLM token received" if reply.get("llmToken") else "")))


def do_verify(state: dict) -> None:
    from autopilot_sdk.checks import verify_agent

    con.print(Rule(f"[bold]VERIFY[/] {state['agent']}", style="#7aa2ff"))
    with con.status("[#8a97b1]running agent.py on a sample opening and a sample pause…[/]", spinner="dots"):
        report = verify_agent(state["agent"])
    for check in report.checks:
        mark = "[bold green]✓[/]" if check.ok else "[bold red]✗[/]"
        con.print(f"  {mark} [bold]{check.name}[/]  [#8a97b1]{check.detail}[/]")
    con.print(ok("agent.py is ready to play") if report.passed else bad("[red]fix the items above[/]"))


def run_mode(state: dict, info: dict, auth: dict, mode: str, all_cases: bool = False) -> None:
    from autopilot_sdk.local_proxy import (
        SuiteError, check_agent_file, game_url, new_run, new_suite, serve_run,
    )

    agent, backend = state["agent"], state["backend"]
    check_agent_file(Path(agent))
    test_case, suite = None, None
    if mode == "play" and all_cases:
        try:
            from autopilot_sdk.local_proxy import fetch_test_cases
            suite = new_suite(fetch_test_cases(backend))
        except SuiteError as exc:
            con.print(bad(f"[red]{exc}[/]"))
            return
    elif mode == "play":
        if not info["cases"]:
            con.print(bad("[red]The backend has no public test cases[/]"))
            return
        con.print(cases_table(info["cases"]))
        codes = [c["code"] for c in info["cases"]]
        default = state.get("testCase") if state.get("testCase") in codes else codes[0]
        test_case = ask("Test case", default).strip().upper()
        if test_case not in codes:
            con.print(bad(f"[red]{test_case} is not one of {', '.join(codes)}[/]"))
            return
        state["testCase"] = test_case
        save_state(state)
    else:
        if not info["hidden"]:
            con.print(bad("[red]No hidden test cases are set up yet: ask the organisers[/]"))
            return
        if not auth.get("token"):
            con.print("[#8a97b1]Eval counts for your team: log in now, or on the game page before pressing play.[/]")
            if Confirm.ask("  Log in here now?", default=True, console=con):
                do_login(state, auth)
        con.print(f"[bold yellow]EVAL[/] plays each of the {info['hidden']} hidden test cases once with the "
                  "agent.py as it is [bold]now[/] (edits during the eval are not used). The backend records the "
                  "score, your agent.py and this run's log.")
        if not Confirm.ask("  Start the eval?", default=True, console=con):
            return

    run = new_run(mode, agent, backend, test_case=test_case, suite=suite, auth=auth)
    url = game_url(backend)
    what = (f"test case [bold]{test_case}[/]" if test_case else
            f"all {len(suite['cases'])} public test cases" if suite else f"{info['hidden']} hidden test cases")
    steps = Table.grid(padding=(0, 2))
    steps.add_column(style=f"bold {ACCENT}")
    steps.add_column()
    steps.add_row("1", f"Open [bold underline]{url}[/] in your browser")
    steps.add_row("2", "Log in with your team ID and password (or type your team ID)")
    steps.add_row("3", "Press play" + (": each game is the next hidden test case" if mode == "eval" else ""))
    body = Group(
        Text.from_markup(f"[bold green]● ONLINE[/]   mode [bold]{mode.upper()}[/] · {what}"),
        Text.from_markup(f"[#8a97b1]game API http://127.0.0.1:5000 · run log {run.log.dir}[/]"),
        Text(""), steps, Text(""),
        Text.from_markup("[#8a97b1]Ctrl+C stops and returns to the menu. agent.py edits apply at the next pause"
                         + (" (not in eval)" if mode == "eval" else "") + ".[/]"),
    )
    con.print(Panel(body, title=f"[bold]{mode.upper()} READY[/]", title_align="left", border_style="green",
                    box=box.HEAVY, padding=(1, 2)))
    try:
        outcome = serve_run("127.0.0.1", 5000, agent, backend, run, load_config().reinforcement_timeout_s)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", None), 10048):
            con.print(bad("[red]Port 5000 is in use: another autopilot is running. Close it and try again.[/]"))
            return
        raise
    auth.clear()
    auth.update(run.auth)  # a login made on the game page carries over
    con.print()
    con.print(Rule(f"[bold]{'EVAL COMPLETE' if outcome == 'finished' else 'STOPPED'}[/]  ·  log saved to "
                   f"[underline]{run.log.dir}[/]", style="#8a97b1"))


# ── Main loop ─────────────────────────────────────────────────────────────────
def main() -> int:
    from autopilot_sdk.console import ParticipantLogHandler
    import logging

    root = logging.getLogger()
    root.handlers[:] = [ParticipantLogHandler()]
    root.setLevel(logging.INFO)
    logging.getLogger("autopilot.http").setLevel(logging.WARNING)

    state, auth = load_state(), {}
    try:
        banner()
        if os.environ.get("AUTOPILOT_BACKEND_URL"):
            state["backend"] = os.environ["AUTOPILOT_BACKEND_URL"].rstrip("/")
        if not state.get("backend"):
            choose_backend(state)
        if not state.get("agent") or not Path(state["agent"]).is_file():
            choose_agent(state)
        info = probe(state["backend"])
        while True:
            con.print(status_panel(state, info, auth))
            con.print(menu_panel())
            choice = ask("Choose", "1").strip().lower()
            if choice in ("q", "quit", "exit"):
                break
            if choice == "b":
                choose_backend(state)
                info = probe(state["backend"])
                continue
            if choice == "a":
                choose_agent(state)
                continue
            if choice == "l":
                do_login(state, auth)
                continue
            if not state.get("agent") or not Path(state["agent"]).is_file():
                con.print(bad("[red]Choose your agent.py first (A)[/]"))
                continue
            if choice == "4":
                do_verify(state)
                continue
            if choice in ("1", "2", "3"):
                if not info["online"]:
                    info = probe(state["backend"])
                    if not info["online"]:
                        con.print(bad(f"[red]The backend is offline: {info['error']}[/]"))
                        continue
                try:
                    run_mode(state, info, auth, "eval" if choice == "3" else "play", all_cases=choice == "2")
                except KeyboardInterrupt:  # Ctrl+C while setting up a run: back to the menu
                    con.print("\n[#8a97b1]Stopped.[/]")
                info = probe(state["backend"])
                continue
            con.print(bad("[red]Type 1, 2, 3, 4, A, L, B or Q[/]"))
    except (KeyboardInterrupt, EOFError):
        con.print()
    con.print(f"[{ACCENT}]Goodbye. Your run logs are in ./autopilot-runs[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
