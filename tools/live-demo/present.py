"""Guided live demo: drives the real stack and explains it on screen.

    ./quickstart.sh demo            (or: .venv/bin/python tools/live-demo/present.py)
    options: --team 0003  --pace 1.0  --voice  --no-vscode  --skip-change  --swiftshader

Needs `./quickstart.sh` running. Opens the agent file in VS Code, then a full
screen browser "stage" with the code, the live monitor and the real Unity game.
Captions, boxes and arrows explain each step while it happens:

  1. agent.py: strategy prompt, the tools the LLM may call, the tool loop
  2. monitor: the request path and the services
  3. game: type the team ID, press play
  4. the level spawns as configured; each pause: board -> LLM tool calls ->
     reasoning -> proposals -> referee -> placed in the game, inside the hold
  5. live change: one sentence in agent.py turns the strategy into a shield
     wall; a new game shows it with no restart
  6. safety: the agent is broken on purpose; the pause is skipped, the game
     carries on, the log says why; then it is restored
  7. summary with this run's real numbers

The agent file is always restored, even on Ctrl+C.
"""
import argparse
import asyncio
import glob
import json
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

MONITOR = "http://127.0.0.1:8020"
GAME_URL = "http://localhost:8000/"
SHIELD_OFF = '    # "Strategy: build a shield wall'
SHIELD_ON = '    "Strategy: build a shield wall'
CRASH_OFF = "    # 1 / 0"
CRASH_ON = "    1 / 0"
# Positions inside the 1280x720 game canvas (measured on the build6 start screen; build7 unchecked).
TEAM_BOX = (203, 376)
TEAM_AREA = (165, 330, 330, 70)
URL_AREA = (140, 435, 855, 155)
PLAY = (1160, 371)
PLAY_AREA = (1095, 305, 135, 135)
SPAWN_AREA = (900, 60, 370, 600)


def http_json(path):
    with urllib.request.urlopen(MONITOR + path, timeout=5) as r:
        return json.load(r)


class Demo:
    def __init__(self, page, args):
        self.page, self.args, self.speaker, self.shot = page, args, None, 0
        self.monitor = page.frame_locator("#monitor")
        self.code = page.frame_locator("#code")
        self.game = page.frame_locator("#game")

    # ── presentation primitives ────────────────────────────────────────────
    async def say(self, step, title, text, secs=6.0):
        print(f"\n[{step}] {title}\n    {re.sub('<[^>]+>', '', text)}", flush=True)
        await self.page.evaluate("([s, t, x]) => demo.caption(s, t, x)", [step, title, text])
        if self.args.shots:
            self.shot += 1
            await asyncio.sleep(0.6)
            await self.page.screenshot(path=f"{self.args.shots}/{self.shot:02d}.png")
        if self.args.voice and shutil.which("spd-say"):
            if self.speaker and self.speaker.poll() is None:
                self.speaker.terminate()
            self.speaker = subprocess.Popen(["spd-say", "-r", "-5", f"{title}. {re.sub('<[^>]+>', '', text)}"])
        await self.wait(secs)

    async def wait(self, secs):
        await asyncio.sleep(secs * self.args.pace)

    async def clear(self):
        await self.page.evaluate("demo.clear()")

    async def mode(self, m):
        await self.page.evaluate("m => demo.mode(m)", m)
        await asyncio.sleep(0.8)

    async def box(self, rect, label="", kind="", below=False):
        if rect:
            await self.page.evaluate("([r, l, k, b]) => demo.box(r, l, k, b)", [rect, label, kind, below])

    async def arrow(self, a, b, label=""):
        if a and b:
            await self.page.evaluate("([a, b, l]) => demo.arrow(a, b, l)", [a, b, label])

    async def rect(self, frame, selector, nth=0):
        loc = frame.locator(selector).nth(nth)
        try:
            await loc.scroll_into_view_if_needed(timeout=3000)
            await asyncio.sleep(0.3)
            return await loc.bounding_box(timeout=3000)
        except Exception:
            return None

    async def lines(self, first_text, last_text=None):
        """Rectangle around code lines, found by their text."""
        first = self.code.locator(".ln", has_text=first_text).first
        try:
            await first.scroll_into_view_if_needed(timeout=3000)
            await asyncio.sleep(0.4)
            a = await first.bounding_box()
            b = await self.code.locator(".ln", has_text=last_text).first.bounding_box() if last_text else a
            return {"x": a["x"], "y": a["y"], "width": max(a["width"], 900) * 0.62, "height": b["y"] + b["height"] - a["y"]}
        except Exception:
            return None

    async def canvas(self, x, y, w=0, h=0):
        """A rectangle given in game-canvas pixels (1280x720), in stage coordinates.

        The game iframe is CSS-scaled, so positions are worked out from the
        iframe's on-screen rectangle and the canvas offset inside the iframe.
        """
        frame = await self.page.evaluate("""() => {
            const r = document.getElementById('game').getBoundingClientRect();
            return {x: r.x, y: r.y, width: r.width};
        }""")
        game = next((f for f in self.page.frames if f.url.startswith(GAME_URL)), None)
        c = await game.evaluate("""() => {
            const r = document.querySelector('#unity-canvas').getBoundingClientRect();
            return {x: r.x, y: r.y, width: r.width};
        }""")
        s = frame["width"] / 1290             # iframe CSS scale
        k = s * c["width"] / 1280             # canvas pixels -> stage pixels
        ox, oy = frame["x"] + c["x"] * s, frame["y"] + c["y"] * s
        return {"x": ox + x * k, "y": oy + y * k, "width": w * k, "height": h * k}

    # ── game control ───────────────────────────────────────────────────────
    async def load_game(self):
        await self.page.evaluate("u => demo.loadGame(u)", GAME_URL + "?t=" + str(time.time()))

    async def game_ready(self, timeout=90):
        frame = None
        for _ in range(timeout * 2):
            frame = next((f for f in self.page.frames if f.url.startswith(GAME_URL)), None)
            try:
                if frame and await frame.evaluate(
                        "getComputedStyle(document.querySelector('#unity-loading-bar')).display") == "none":
                    await asyncio.sleep(4)  # menu animation
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return False

    async def start_game(self, explain):
        team = self.args.team
        if explain:
            await self.clear()
            await self.box(await self.canvas(*TEAM_AREA), "1 · team ID: 4 digits")
            await self.say("Step 3 · Game", "Log in with the team ID",
                           f"The game identifies the team by a 4-digit ID. <b>{team}</b> is our team; the backend looks up "
                           "its level (enemy waves, spawn timing, reinforcement rules).", 6)
        p = await self.canvas(*TEAM_BOX)
        await self.page.mouse.click(p["x"], p["y"])
        await asyncio.sleep(0.4)
        await self.page.keyboard.type(team, delay=180)
        if explain:
            await self.clear()
            await self.box(await self.canvas(*URL_AREA), "2 · leave empty: the game talks to the local SDK", "green")
            await self.say("Step 3 · Game", "No URLs needed",
                           "Empty boxes mean the game uses its built-in address, 127.0.0.1:5000. That is the SDK "
                           "running on the participant's machine; the agent's code never leaves it.", 6)
            await self.clear()
            await self.box(await self.canvas(*PLAY_AREA), "3 · play")
            await self.say("Step 3 · Game", "Start", "Pressing play.", 2.5)
        await self.page.mouse.click(*(lambda r: (r["x"], r["y"]))(await self.canvas(*PLAY)))
        await self.clear()

    # ── monitor helpers ────────────────────────────────────────────────────
    async def game_ids(self):
        return set(await self.monitor.locator("#games > .game").evaluate_all("els => els.map(e => e.id)"))

    async def new_game(self, before, timeout=40):
        for _ in range(timeout * 2):
            ids = await self.game_ids()
            new = [i for i in ids - before if i]
            if new:
                return "#" + new[0]
            await asyncio.sleep(0.5)
        return None

    async def pauses_seen(self, game):
        return await self.monitor.locator(f"{game} .pause").count()

    async def wait_pause(self, game, n, timeout=60):
        """Wait until pause n of this game is answered; returns its selector, or None if the game ended."""
        sel = f"{game} .pause[data-n='{n}']"
        for _ in range(timeout * 2):
            if await self.monitor.locator(f"{sel}:not(.pending)").count():
                return sel
            if await self.monitor.locator(f"{game} .result").count():
                return None
            await asyncio.sleep(0.5)
        return None

    async def wait_result(self, game, timeout=120):
        for _ in range(timeout * 2):
            if await self.monitor.locator(f"{game} .result").count():
                return True
            await asyncio.sleep(0.5)
        return False

    async def explain_pause(self, sel, detailed=True):
        m = self.monitor
        game_canvas = await self.canvas(0, 0, 1280, 720)
        await self.clear()
        await self.box(await self.rect(m, f"{sel} .board"), "the board the game sent", "")
        await self.box(await self.rect(m, f"{sel} .steps > .step >> nth=0"), "① snapshot", "", True)
        await self.say("Step 5 · Reinforcement pause", "The game froze and sent the live board",
                       "Every pause, Unity sends every unit's position and health: blue = defences, red = enemies. "
                       "The game waits, holding time still, for the agent's answer.", 7 if detailed else 4)
        await self.clear()
        rounds = await m.locator(f"{sel} .round").count()
        calls = await m.locator(f"{sel} .call").evaluate_all("els => els.map(e => e.textContent.split('(')[0])")
        if rounds:
            await self.box(await self.rect(m, f"{sel} .step.llm"), f"② LLM: {rounds} round(s)", "purple")
            if calls:
                await self.say("Step 5 · Reinforcement pause", "The agent asked the LLM, and the LLM used the agent's tools",
                               f"Qwen on Amazon Bedrock called the participant's own Python tools: <b>{', '.join(dict.fromkeys(calls))}</b>. "
                               "They ran inside the agent's sandbox and their results went back to the model.", 8 if detailed else 5)
            else:
                await self.say("Step 5 · Reinforcement pause", "The agent asked the LLM",
                               "Qwen on Amazon Bedrock got the board summary. It had the participant's tools available, but "
                               "this time it had enough information to answer directly.", 7 if detailed else 5)
            if await m.locator(f"{sel} .thought").count():
                await self.clear()
                await self.box(await self.rect(m, f"{sel} .thought"), "the model's reasoning", "purple")
                await self.say("Step 5 · Reinforcement pause", "Its reasoning, in its own words",
                               "This sentence is the model's explanation for its placements.", 5)
        else:
            await self.box(await self.rect(m, f"{sel} .steps > .step >> nth=1"), "②", "")
            await self.say("Step 5 · Reinforcement pause", "No model call this time",
                           "The card says why: a short pause leaves no time for the LLM, or there was nothing to do.", 5)
        await self.clear()
        props = await self.rect(m, f"{sel} .steps > .step >> nth=2")
        verdict = await self.rect(m, f"{sel} .steps > .step >> nth=3")
        await self.box(props, "③ agent's proposal", "")
        await self.box(verdict, "④ referee: approved", "green", True)
        await self.arrow(verdict, {"x": game_canvas["x"] + game_canvas["width"] * 0.25, "y": game_canvas["y"] + game_canvas["height"] * 0.5,
                                   "width": 1, "height": 1}, "placed in the game")
        await self.say("Step 5 · Reinforcement pause", "Proposal → referee → game",
                       "The backend referee checks every placement (on the board, free cell, no duplicates) and never "
                       "moves one. Approved defences appear in the game when it resumes.", 7 if detailed else 5)
        await self.clear()
        badge = await self.rect(m, f"{sel} .pause-title .badge >> nth=-1")
        await self.box(badge, "inside the game's time limit", "green")
        await self.say("Step 5 · Reinforcement pause", "Fast enough for a live game",
                       "The whole loop (game → SDK → LLM with tools → referee → game) finished inside the pause the "
                       "level allows. The SDK sizes the agent's time budget to each level automatically.", 5)
        await self.clear()


async def run(args):
    agent_path = Path(http_json("/code/source")["path"] or "")
    if not agent_path.is_file():
        raise SystemExit("Start the stack first: ./quickstart.sh")
    original = agent_path.read_text("utf-8")
    if args.vscode and shutil.which("code"):
        line = next((i + 1 for i, l in enumerate(original.splitlines()) if l.startswith("RULES")), 1)
        subprocess.Popen(["code", "-g", f"{agent_path}:{line}"])
        print("\n[Step 0 · VS Code] This is the participant's agent.py, a normal Python file.")
        print("    It defines get_reinforcements(state); the game calls it at every pause.")
        await asyncio.sleep(8 * args.pace)

    chrome = None
    cached = sorted(glob.glob(str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux*/chrome")))
    size = re.search(r"(\d+)x(\d+) pixels", subprocess.run(["xdpyinfo"], capture_output=True, text=True).stdout or "")
    width, height = (int(size.group(1)), int(size.group(2))) if size else (1920, 1080)
    launch = {"headless": False, "args": [
        "--start-maximized", f"--window-size={width},{height}", "--window-position=0,0", "--autoplay-policy=no-user-gesture-required",
        "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows", "--hide-crash-restore-bubble"]}
    if args.swiftshader:
        launch["args"] += ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(**launch)
        except Exception:
            chrome = cached[-1] if cached else None
            browser = await p.chromium.launch(executable_path=chrome, **launch)
        ctx = await browser.new_context(no_viewport=True)
        page = await ctx.new_page()
        await page.goto(MONITOR + "/demo")
        d = Demo(page, args)
        try:
            await d.load_game()  # warm up the game while the code is explained
            await page.evaluate("""h => demo.panel(h)""", """
              <h1>Autopilot: live agent run</h1>
              <h2>Everything you are about to see is live: the real Unity game, the participant SDK, the backend
                  referee and an LLM on Amazon Bedrock.</h2>
              <ul>
                <li class="flow">The participant's code: <b>agent.py</b></li>
                <li class="flow">The live monitor: every exchange between the components</li>
                <li class="flow">A real game: the LLM decides reinforcements at every pause</li>
                <li class="flow">Change one line of agent.py: the game follows it, no restart</li>
                <li class="flow">Break the agent on purpose: the game keeps running</li>
                <li class="flow">This run's numbers</li>
              </ul>""")
            await d.wait(7)
            await page.evaluate("demo.panel('')")

            # 1. code
            await d.mode("code")
            await d.say("Step 1 · Participant code", "agent.py: the participant writes one function",
                        "<b>get_reinforcements(state)</b> receives the live board at every pause and returns placements. "
                        "This view is the file the SDK is running right now.", 6)
            await d.box(await d.lines("RULES = (", "fences:"), "strategy, in plain English")
            await d.say("Step 1 · Participant code", "The strategy is a prompt",
                        "These sentences tell the LLM how to play. Changing a sentence changes the behaviour; we will do "
                        "that live in a minute.", 7)
            await d.clear()
            await d.box(await d.lines("def threatened_rows", "def threatened_rows"), "tool 1", "purple")
            await d.box(await d.lines("def lane_report", "def lane_report"), "tool 2", "purple")
            await d.box(await d.lines("def check_cells", "def check_cells"), "tool 3", "purple")
            await d.say("Step 1 · Participant code", "The participant's own tools for the LLM",
                        "Each <b>@tool</b> is a plain Python function. The SDK turns it into a tool the model can call and "
                        "runs it locally, inside a sandbox with the network guard on.", 8)
            await d.clear()
            await d.box(await d.lines("run = run_tool_loop(", "model=MODEL, deadline_s"), "LLM call with a deadline")
            await d.say("Step 1 · Participant code", "One call runs the whole tool conversation",
                        "<b>run_tool_loop</b> sends the tools, runs the ones the model asks for, and forces a final answer "
                        "before the game's time limit. API keys never appear here; the organiser's gateway holds them.", 8)
            await d.clear()

            # 2. monitor
            await d.mode("monitor")
            m = d.monitor
            await d.box(await d.rect(m, ".flow"), "the request path")
            await d.say("Step 2 · Live monitor", "How the pieces talk",
                        "Game → SDK (runs agent.py) → backend referee. The agent reaches the LLM only through the "
                        "organiser's gateway. Every message on these arrows is recorded live.", 8)
            await d.clear()
            await d.box(await d.rect(m, "#health"), "all services up", "green")
            await d.box(await d.rect(m, "#exPanel"), "raw HTTP traffic", "")
            await d.box(await d.rect(m, "#logPanel"), "service logs", "", True)
            await d.say("Step 2 · Live monitor", "Nothing is hidden",
                        "Service health, every HTTP request and response, and the logs of all services, live.", 6)
            await d.clear()

            # 3. game
            await d.mode("game")
            if not await d.game_ready():
                await d.say("Step 3 · Game", "The game is still loading", "Waiting for Unity…", 1)
                await d.game_ready(120)
            before = await d.game_ids()
            await d.start_game(explain=True)
            await page.evaluate("demo.gameLive(true)")
            await d.mode("split")

            # 4. level + spawning
            game = await d.new_game(before)
            if game:
                await d.box(await d.rect(m, f"{game} .game-head"), "level from the backend")
                await d.box(await d.canvas(*SPAWN_AREA), "enemies enter here", "", True)
                await d.arrow(await d.rect(m, f"{game} .level"), await d.canvas(*SPAWN_AREA), "same waves")
                await d.say("Step 4 · Level", "Enemies spawn exactly as configured",
                            "The backend sent this level: enemy types, counts and allowed rows, spawn timing, and how often "
                            "the game pauses for reinforcements. Watch them enter from the right in those rows.", 9)
                await d.clear()

                # 5. first pause
                await d.say("Step 5 · Reinforcement pause", "Waiting for the first pause…",
                            "Every few seconds of game time the game freezes and asks the agent for help.", 1)
                sel = await d.wait_pause(game, 1)
                for _ in range(3):
                    # The game sometimes ends a game at the very first spawn, before any
                    # pause (seen on fresh and reused tabs alike); start it again.
                    if sel or not await d.monitor.locator(f"{game} .result").count():
                        break
                    await d.clear()
                    await d.say("Step 5 · Reinforcement pause", "The game ended at the first spawn; starting it again",
                                "An intermittent issue inside the compiled game build (the backend sent the same level). "
                                "A fresh start fixes it.", 3)
                    await d.load_game()
                    await d.game_ready()
                    before = await d.game_ids()
                    await d.start_game(explain=False)
                    game = await d.new_game(before) or game
                    sel = await d.wait_pause(game, 1)
                if sel:
                    await d.explain_pause(sel)
                # 6. live change, in the same running game
                if not args.skip_change and SHIELD_OFF in original:
                    await d.clear()
                    await d.mode("code-game")
                    await d.say("Step 6 · Live change", "Change one sentence of the strategy, while the game runs",
                                "We switch on one line: <b>build a shield wall, shields only</b>. No restart and no redeploy: "
                                "the SDK re-reads agent.py at every pause.", 6)
                    agent_path.write_text(original.replace(SHIELD_OFF, SHIELD_ON, 1), "utf-8")
                    target = await d.pauses_seen(game) + 1
                    await asyncio.sleep(1.5)
                    await d.box(await d.rect(d.code, ".ln.changed"), "saved: new strategy", "green")
                    await d.say("Step 6 · Live change", "Saved", "The same file is open in VS Code. Now watch the next pause.", 5)
                    await d.clear()
                    await d.mode("split")
                    await d.say("Step 6 · Live change", "Waiting for the next pause…", "Same game, same SDK, new strategy.", 1)
                    sel = await d.wait_pause(game, target)
                    if sel:
                        await d.clear()
                        await d.box(await d.rect(m, f"{sel} .steps > .step >> nth=2"), "now: shields (Sh)", "green")
                        await d.box(await d.rect(m, f"{sel} .thought"), "its reasoning now", "purple", True)
                        game_canvas = await d.canvas(0, 0, 1280, 720)
                        await d.arrow(await d.rect(m, f"{sel} .steps > .step >> nth=3"),
                                      {"x": game_canvas["x"] + game_canvas["width"] * 0.3, "y": game_canvas["y"] + game_canvas["height"] * 0.55,
                                       "width": 1, "height": 1}, "shields in the game")
                        await d.say("Step 6 · Live change", "The agent follows the new strategy immediately",
                                    "The model's reasoning and its proposals switched to shields, and they appear in the running "
                                    "game. That was one edited sentence.", 9)
                        await d.clear()

                        # 7. safety
                        agent_path.write_text(original.replace(SHIELD_OFF, SHIELD_ON, 1).replace(CRASH_OFF, CRASH_ON, 1), "utf-8")
                        target = await d.pauses_seen(game) + 1  # first pause that starts after this save
                        await d.say("Step 7 · Safety", "Now we break the agent on purpose",
                                    "We add <b>1 / 0</b> at the top of get_reinforcements, a crash. A participant's bug must "
                                    "never stop the game.", 3)
                        sel2 = await d.wait_pause(game, target)
                        agent_path.write_text(original, "utf-8")  # restore right away
                        if sel2:
                            await d.box(await d.rect(m, "#logPanel"), "agent crashed → pause skipped; the SDK log shows the error")
                            await d.say("Step 7 · Safety", "The game kept running",
                                        "The crash stayed inside the agent's own process: the pause was skipped, the log shows the "
                                        "error, and the game carried on. The file is already restored.", 9)
                            await d.clear()
                            target = await d.pauses_seen(game) + 1
                            sel3 = await d.wait_pause(game, target, 40)
                            if sel3:
                                await d.box(await d.rect(m, f"{sel3} .pause-title"), "restored: LLM plans again", "green")
                                await d.say("Step 7 · Safety", "Fixed file, next pause: back to normal",
                                            "No restart needed in either direction.", 6)
                                await d.clear()
                agent_path.write_text(original, "utf-8")
                if await d.wait_result(game, 25):
                    await d.box(await d.rect(m, f"{game} .result"), "result posted and scored", "green")
                    await d.say("Game over", "The result went to the backend and was scored",
                                "Unity posts the full battle log; the backend evaluator scores it and stores it.", 6)
                    await d.clear()

            # 8. summary
            agent_path.write_text(original, "utf-8")
            ev = http_json("/history")
            turns = [e for e in ev if e.get("kind") == "start" and e.get("turn")]
            llm = [e for e in ev if e.get("kind") == "end" and e.get("hop") == "agent-gateway" and e.get("status") == 200]
            appr = sum(len((e.get("response") or {}).get("defencePlacementData") or [])
                       for e in ev if e.get("kind") == "end" and e.get("hop") == "game-sdk"
                       and isinstance(e.get("response"), dict) and "placeDefences" in e["response"])
            scores = [e["response"]["score"] for e in ev if e.get("kind") == "end" and isinstance(e.get("response"), dict) and "score" in e["response"]]
            avg = round(sum(e["ms"] for e in llm) / len(llm)) if llm else 0
            await d.mode("monitor")
            await page.evaluate("h => demo.panel(h)", f"""
              <h1>Ready</h1><h2>Measured live during this session</h2>
              <div class="big">
                <div><b>{len(turns)}</b><span>reinforcement pauses answered</span></div>
                <div><b>{len(llm)}</b><span>LLM calls (Qwen, Bedrock Mumbai)</span></div>
                <div><b>{avg} ms</b><span>average LLM call</span></div>
                <div><b>{appr}</b><span>defences approved by the referee</span></div>
                <div><b>{scores[-1] if scores else '–'}</b><span>last game's backend score</span></div>
              </div>
              <ul>
                <li>One command setup: <b>./quickstart.sh</b> (database, backend, SDK, game, LLM gateway, monitor)</li>
                <li>Participant code runs locally; keys stay with the organiser gateway</li>
                <li>LLM tool calling with the participant's own Python tools</li>
                <li>Agent time limit adapts to each level's pause</li>
                <li>Agent crashes and timeouts never stop the game</li>
                <li>Live edits apply at the next pause, no restart</li>
                <li>SDK binaries for Linux and Windows (Windows built and tested in a VM)</li>
                <li>Latest GDSC backend merged; 10 test-case levels loaded</li>
                <li>92 automated SDK + gateway tests passing</li>
                <li>Everything above is on GitHub: test_branch</li>
              </ul>""")
            await d.say("Summary", "Ready to run the event", "Questions?", 1)
            print("\nDemo finished. The stage stays open; press Ctrl+C to close it.")
            while not args.exit:
                await asyncio.sleep(3600)
        finally:
            agent_path.write_text(original, "utf-8")
            if d.speaker and d.speaker.poll() is None:
                d.speaker.terminate()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--team", default="0003", help="4-digit team ID to play (default 0003)")
    parser.add_argument("--pace", type=float, default=1.0, help="caption time multiplier (1.3 = slower)")
    parser.add_argument("--voice", action="store_true", help="read captions aloud (spd-say)")
    parser.add_argument("--no-vscode", dest="vscode", action="store_false", help="do not open VS Code first")
    parser.add_argument("--skip-change", action="store_true", help="skip the live-change and safety steps")
    parser.add_argument("--swiftshader", action="store_true", help="software WebGL if the game renders black")
    parser.add_argument("--shots", help="save a screenshot at every caption into this folder (testing)")
    parser.add_argument("--exit", action="store_true", help="close the stage when the demo ends")
    try:
        asyncio.run(run(parser.parse_args()))
    except KeyboardInterrupt:
        print("\nStopped; agent.py restored.")


if __name__ == "__main__":
    main()
