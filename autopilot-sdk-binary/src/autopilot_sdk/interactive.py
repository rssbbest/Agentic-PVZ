"""The interactive console: `autopilot` with no arguments.

Start the binary once (double-click it); give agent.py and log in with the
team ID and password; choose a mode and a test case; open the game page and
press play. Ctrl+C stops the run and comes back to the menu, where
another test case or mode can be picked without restarting anything. agent.py
edits apply at the next pause in PLAY (a PUBLISH run plays a frozen copy).
PLAY is practice; only PUBLISH PUBLIC / PUBLISH PRIVATE runs reach a leaderboard.

Choices are remembered in ~/.autopilot/state.json for the next start. The
server is the event's (config.event_backend_url); organisers can point it
elsewhere with AUTOPILOT_BACKEND_URL.
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
    info = {"online": False, "cases": [], "hidden": None, "open": None, "error": None}
    try:
        requests.get(backend.rstrip("/") + "/health", timeout=8).raise_for_status()
        info["online"] = True
        info["cases"] = requests.get(backend.rstrip("/") + "/api/test-cases", timeout=8).json()
        resp = requests.get(backend.rstrip("/") + "/api/eval/info", timeout=8)
        info["hidden"] = resp.json().get("hiddenTestCases") if resp.ok else None
        info["open"] = resp.json().get("open") if resp.ok else None
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
    private = ("open" if info.get("open") else "opens later") if info.get("open") is not None else ""
    grid.add_row("TEST CASES", f"{len(cases)} public" + (f"  ·  {hidden} hidden (PUBLISH PRIVATE {private})"
                                                          if hidden is not None else ""))
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
        ("1", "PLAY", "practice one public test case, as often as you like (not on the leaderboard)"),
        ("2", "PUBLISH PUBLIC", "every public test case once with this agent.py: your total goes on the live leaderboard"),
        ("3", "PUBLISH PRIVATE", "every hidden test case once: the final leaderboard (when the organisers open it)"),
        ("4", "VERIFY", "check agent.py and dry-run it"),
        ("A", "agent.py", "choose another agent file"),
        ("L", "switch team", "log in as another team"),
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
def event_backend() -> str:
    """The event server: fixed in the SDK, not asked (AUTOPILOT_BACKEND_URL overrides)."""
    return (os.environ.get("AUTOPILOT_BACKEND_URL") or load_config().event_backend_url).rstrip("/")


def program_dir() -> Path:
    """The folder of the SDK program (where the download's agent.py sits)."""
    return Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve().parent


def choose_agent(state: dict) -> None:
    from autopilot_sdk.local_proxy import AgentFileError, check_agent_file

    while True:
        # The agent.py where the SDK was started (or beside the program) comes
        # first: a path remembered from an older session may be a stale copy.
        here = [p for p in (Path.cwd() / "agent.py", program_dir() / "agent.py") if p.is_file()]
        remembered = state.get("agent") if state.get("agent") and Path(state["agent"]).is_file() else None
        default = str(here[0].resolve()) if here else (remembered or "agent.py")
        path = ask("Path to your agent.py (Q to quit)", default).strip().strip('"').strip("'")
        if path.lower() in ("q", "quit", "exit"):
            raise EOFError
        candidate = Path(path).expanduser()
        if not candidate.is_absolute() and not candidate.exists() and (program_dir() / candidate).exists():
            candidate = program_dir() / candidate  # relative to the program when double-clicked
        if candidate.is_dir():
            candidate = candidate / "agent.py"
        try:
            check_agent_file(candidate)
        except AgentFileError as exc:
            show_agent_problem(exc)
            con.print("[#8a97b1]  Fix the file and press Enter to check it again, or type another path.[/]")
            state["agent"] = str(candidate)
            continue
        state["agent"] = str(candidate.resolve())
        save_state(state)
        con.print(ok(f"agent.py ready: {state['agent']}"))
        return


def show_agent_problem(exc: BaseException) -> None:
    """A broken agent.py, said plainly (the program keeps running)."""
    con.print(Panel(Text(str(exc)), title="[bold red]agent.py needs a fix[/]", title_align="left",
                    border_style="red", box=box.ROUNDED))


def guarded(action, *args) -> None:
    """Run one menu action; whatever goes wrong, say so and come back to the menu."""
    from autopilot_sdk.local_proxy import AgentFileError

    try:
        action(*args)
    except KeyboardInterrupt:  # Ctrl+C while setting up: back to the menu
        con.print("\n[#8a97b1]Stopped.[/]")
    except AgentFileError as exc:
        show_agent_problem(exc)
        con.print("[#8a97b1]  Fix agent.py and choose again (it is read fresh every time).[/]")
    except (EOFError, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 - never close the console on a participant
        con.print(bad(f"[red]Something went wrong: {type(exc).__name__}: {exc}[/]"))
        con.print("[#8a97b1]  You are back at the menu. If it keeps happening, tell the organisers.[/]")


def do_login(state: dict, auth: dict) -> bool:
    from autopilot_sdk.local_proxy import LoginError, RunContext, apply_login, login

    team = ask("Team ID (4 digits, Q to quit)", auth.get("teamID") or state.get("team")).strip()
    if team.lower() in ("q", "quit", "exit"):
        raise EOFError
    password = Prompt.ask(f"[bold {ACCENT}]›[/] Password", password=True, console=con)
    try:
        reply = login(state["backend"], team, password)
    except LoginError as exc:
        con.print(bad(f"[red]Login failed: {exc}[/]"))
        return False
    run = RunContext()
    apply_login(run, reply)
    auth.clear()
    auth.update(run.auth)
    state["team"] = reply.get("teamID")
    save_state(state)
    con.print(ok(f"Logged in as team {reply.get('teamID')} ({reply.get('teamName')})"
                 + (" · LLM token received" if reply.get("llmToken") else "")))
    return True


def startup_login(state: dict, auth: dict) -> None:
    """Every start: log in with the team ID and password the organisers sent."""
    con.print(Rule("[bold]LOG IN[/]  [#8a97b1]team ID and password from the organisers[/]", style="#7aa2ff"))
    while not do_login(state, auth):
        pass


def do_verify(state: dict) -> None:
    from autopilot_sdk.checks import verify_agent

    con.print(Rule(f"[bold]VERIFY[/] {state['agent']}", style="#7aa2ff"))
    with con.status("[#8a97b1]running agent.py on a sample opening and a sample pause…[/]", spinner="dots"):
        report = verify_agent(state["agent"])
    for check in report.checks:
        mark = "[bold green]✓[/]" if check.ok else "[bold red]✗[/]"
        con.print(f"  {mark} [bold]{check.name}[/]  [#8a97b1]{check.detail}[/]")
    con.print(ok("agent.py is ready to play") if report.passed else bad("[red]fix the items above[/]"))


def run_mode(state: dict, info: dict, auth: dict, mode: str, all_cases: bool = False,
             board: str = "private", resumed: bool = False) -> None:
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
        codes = [c["code"] for c in info["cases"]]
        default = state.get("testCase") if state.get("testCase") in codes else codes[0]
        if resumed:  # NEW: after an update, the same test case again
            test_case = default
        else:
            con.print(cases_table(info["cases"]))
            test_case = ask("Test case", default).strip().upper()
        if test_case not in codes:
            con.print(bad(f"[red]{test_case} is not one of {', '.join(codes)}[/]"))
            return
        state["testCase"] = test_case
        save_state(state)
    else:
        count = len(info["cases"]) if board == "public" else info["hidden"]
        kind = "public" if board == "public" else "hidden"
        if not count:
            con.print(bad(f"[red]No {kind} test cases are set up yet: ask the organisers[/]"))
            return
        if board == "private" and info.get("open") is False:
            con.print(bad("[red]PUBLISH PRIVATE is not open yet: the organisers open it when the build phase "
                          "ends. Until then, PUBLISH PUBLIC (2).[/]"))
            return
        if not auth.get("token"):
            con.print("[#8a97b1]Publishing counts for your team: log in first.[/]")
            if not do_login(state, auth):
                return
        con.print(f"[bold yellow]PUBLISH {board.upper()}[/] plays each of the {count} {kind} test cases once with "
                  "the agent.py as it is [bold]now[/] (edits during the run are not used). The total of the "
                  "scores goes to the " + ("live leaderboard (your best run counts)." if board == "public"
                                           else "final leaderboard, revealed by the organisers."))
        if not resumed and not Confirm.ask("  Start?", default=True, console=con):
            return

    run = new_run(mode, agent, backend, test_case=test_case, suite=suite, auth=auth, board=board)
    url = game_url(backend)
    kind = "public" if board == "public" else "hidden"
    title = f"PUBLISH {board.upper()}" if mode == "eval" else mode.upper()
    what = (f"test case [bold]{test_case}[/] (practice)" if test_case else
            f"all {len(suite['cases'])} public test cases" if suite else
            f"{len(info['cases']) if board == 'public' else info['hidden']} {kind} test cases")
    steps = Table.grid(padding=(0, 2))
    steps.add_column(style=f"bold {ACCENT}")
    steps.add_column()
    steps.add_row("1", f"Open [bold underline]{url}[/] in your browser")
    steps.add_row("2", "Type your team ID and press play"
                  + (f": each game is the next {kind} test case" if mode == "eval" else ""))
    steps.add_row("3", "The browser asks to [bold]access other apps and services on this device[/]: "
                       "click [bold]Allow[/] (that is the game reaching this SDK)")
    body = Group(
        Text.from_markup(f"[bold green]● ONLINE[/]   [bold]{title}[/] · {what}"),
        Text.from_markup(f"[#8a97b1]game API http://127.0.0.1:5000 · run log {run.log.dir}[/]"),
        Text(""), steps, Text(""),
        Text.from_markup("[#8a97b1]Ctrl+C stops and returns to the menu. agent.py edits apply at the next pause"
                         + (" (not in a published run)" if mode == "eval" else "") + ".[/]"),
    )
    con.print(Panel(body, title=f"[bold]{title} READY[/]", title_align="left", border_style="green",
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
    state["_outcome"] = outcome  # NEW: the menu goes on after an update only if this one was stopped for it
    if outcome == "outdated":
        con.print(bad("[yellow]The organisers released a new SDK: this one stops here, updates and carries on"
                      + (" (the run continues where it stopped)" if mode == "eval" else "") + ".[/]"))
    con.print(Rule(f"[bold]{title + ' COMPLETE' if outcome == 'finished' else 'STOPPED'}[/]  ·  log saved to "
                   f"[underline]{run.log.dir}[/]", style="#8a97b1"))


RESUME_ENV = "AUTOPILOT_RESUME"  # NEW: the session an update restart carries over (login, what was running)


def update_sdk(state: dict, auth: dict | None = None, action: str | None = None) -> None:
    """NEW: switch to the latest SDK first if the organisers released one. The SDK
    restarts logged in, and goes on with ``action`` (the menu choice it was on)."""
    from autopilot_sdk import updater

    if auth and auth.get("token"):
        os.environ[RESUME_ENV] = json.dumps({"auth": auth, "action": action, "agent": state.get("agent")})
    try:
        updater.update_if_needed(state["backend"], say=lambda text: con.print(f"[bold yellow]{text}[/]"))
    finally:
        os.environ.pop(RESUME_ENV, None)  # no restart happened


def take_resume() -> dict:
    """What the SDK was doing before an update restarted it ({} on a normal start)."""
    try:
        resume = json.loads(os.environ.pop(RESUME_ENV, "") or "{}")
    except ValueError:
        return {}
    return resume if isinstance(resume, dict) else {}


# ── Main loop ─────────────────────────────────────────────────────────────────
def main() -> int:
    from autopilot_sdk.console import ParticipantLogHandler
    import logging

    root = logging.getLogger()
    root.handlers[:] = [ParticipantLogHandler()]
    root.setLevel(logging.INFO)
    logging.getLogger("autopilot.http").setLevel(logging.WARNING)

    if Path.cwd() == Path.home() and getattr(sys, "frozen", False):
        os.chdir(program_dir())  # double-clicked (macOS Terminal starts in home): logs go beside the program
    state, auth = load_state(), {}
    resume = take_resume()
    try:
        banner()
        state["backend"] = event_backend()  # never the address an older SDK saved
        update_sdk(state)
        if resume.get("agent") and Path(resume["agent"]).is_file():
            state["agent"] = resume["agent"]  # NEW: restarted by an update: same agent.py, same login
            con.print(ok(f"Updated SDK {__version__}: carrying on with {state['agent']}"))
        else:
            choose_agent(state)
        info = probe(state["backend"])
        if not info["online"]:
            con.print(bad(f"[red]Cannot reach the event server ({state['backend']}): {info['error']}[/]"))
        if (resume.get("auth") or {}).get("token"):
            auth.update(resume["auth"])
            con.print(ok(f"Still logged in as team {auth.get('teamID')} ({auth.get('teamName')})"))
        else:
            startup_login(state, auth)
        pending = resume.get("action") if resume.get("action") in ("1", "2", "3") else None
        while True:
            if pending:
                choice, resumed, pending = pending, True, None
            else:
                con.print(status_panel(state, info, auth))
                con.print(menu_panel())
                choice, resumed = ask("Choose", "1").strip().lower(), False
            if choice in ("q", "quit", "exit"):
                break
            if choice == "a":
                guarded(choose_agent, state)
                continue
            if choice == "l":
                guarded(do_login, state, auth)
                continue
            if not state.get("agent") or not Path(state["agent"]).is_file():
                con.print(bad("[red]Choose your agent.py first (A)[/]"))
                continue
            if choice == "4":
                guarded(do_verify, state)
                continue
            if choice in ("1", "2", "3"):
                update_sdk(state, auth, choice)  # NEW: never play or publish with an outdated SDK
                if not info["online"]:
                    info = probe(state["backend"])
                    if not info["online"]:
                        con.print(bad(f"[red]The backend is offline: {info['error']}[/]"))
                        continue
                if choice == "1":
                    guarded(run_mode, state, info, auth, "play", False, "private", resumed)
                else:
                    guarded(run_mode, state, info, auth, "eval", False, "public" if choice == "2" else "private",
                            resumed)
                # NEW: the backend may have refused this SDK as outdated: update and go on with this choice
                update_sdk(state, auth, choice if state.pop("_outcome", None) == "outdated" else None)
                info = probe(state["backend"])
                continue
            con.print(bad("[red]Type 1, 2, 3, 4, A or Q[/]"))
    except (KeyboardInterrupt, EOFError):
        con.print()
    except Exception:  # noqa: BLE001 - keep a double-clicked window open to read the error
        con.print_exception()
        if sys.stdin.isatty():
            try:
                input("Press Enter to close.")
            except (EOFError, KeyboardInterrupt):
                pass
        return 1
    con.print(f"[{ACCENT}]Goodbye. Your run logs are in {Path.cwd() / 'autopilot-runs'}[/]")
    return 0

if __name__ == "__main__":
    sys.exit(main())
