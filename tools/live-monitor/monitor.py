"""Live exchange monitor for the local Autopilot game.

Three transparent HTTP taps sit on the wire between the components and record
every request and response; service log files are followed too. Everything is
streamed to a dashboard (http://127.0.0.1:8020) over server-sent events and
appended to exchanges.jsonl.

    game (browser) -> :5000 tap -> SDK `autopilot play` (:5100)
    SDK            -> :8011 tap -> backend (:8010)
    agent (in SDK) -> :8005 tap -> LLM gateway (:8004) -> Gemini

A request on a nested hop (SDK->backend, agent->gateway) made while the game's
reinforcement request is open is attached to it as one "turn".
"""
import argparse
import asyncio
import itertools
import json
import os
import time
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

HERE = Path(__file__).resolve().parent
HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailers", "transfer-encoding", "upgrade", "content-length", "content-encoding", "host",
}
MAX_BODY_CHARS = 200_000
HISTORY = 5000


class Hub:
    def __init__(self, journal: Path):
        self.events: list[dict] = []
        self.subscribers: set[asyncio.Queue] = set()
        self.ids = itertools.count(1)
        self.open_turn: int | None = None
        self.journal = journal.open("a", encoding="utf-8")

    def publish(self, event: dict) -> None:
        event.setdefault("ts", time.time())
        self.events.append(event)
        del self.events[:-HISTORY]
        if event.get("kind") != "log":
            self.journal.write(json.dumps(event) + "\n")
            self.journal.flush()
        for queue in list(self.subscribers):
            if queue.qsize() < 2000:
                queue.put_nowait(event)


def _decode(raw: bytes, content_type: str):
    if not raw:
        return None
    text = raw.decode("utf-8", "replace")
    if "json" in content_type or text[:1] in "{[":
        try:
            return json.loads(text)
        except ValueError:
            pass
    return text[:MAX_BODY_CHARS]


def make_tap(hub: Hub, hop: str, upstream: str) -> Starlette:
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=False)

    async def forward(request: Request) -> Response:
        body = await request.body()
        path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        event_id = next(hub.ids)
        is_turn = hop == "game-sdk" and request.method == "POST" and request.url.path == "/api/reinforcements"
        # Level start also runs the agent (opening placements); group its calls under it.
        opens = is_turn or (hop == "game-sdk" and request.method == "POST" and request.url.path == "/api/team")
        parent = None if hop == "game-sdk" else hub.open_turn
        if opens:
            hub.open_turn = event_id
        started = time.time()
        hub.publish({
            "kind": "start", "id": event_id, "hop": hop, "method": request.method, "path": path,
            "turn": is_turn, "parent": parent, "ts": started,
            "request": _decode(body, request.headers.get("content-type", "")),
        })
        # Servers tolerate stray whitespace (e.g. "Bearer " with no key); httpx does not.
        headers = {k: v.strip() for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
        status, resp_headers, content, error = 502, {}, b"", None
        try:
            upstream_resp = await client.request(
                request.method, upstream + path, content=body, headers=headers
            )
            status, content = upstream_resp.status_code, upstream_resp.content
            resp_headers = {
                k: v for k, v in upstream_resp.headers.items() if k.lower() not in HOP_HEADERS
            }
        except httpx.HTTPError as exc:
            error = f"{type(exc).__name__}: {exc}"
            content = json.dumps({"detail": f"upstream unreachable ({hop})"}).encode()
            resp_headers = {"content-type": "application/json"}
        finally:
            if opens and hub.open_turn == event_id:
                hub.open_turn = None
        hub.publish({
            "kind": "end", "id": event_id, "hop": hop, "status": status, "error": error,
            "ms": round((time.time() - started) * 1000, 1),
            "response": _decode(content, resp_headers.get("content-type", "")),
        })
        return Response(content, status_code=status, headers=resp_headers)

    methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
    return Starlette(routes=[
        Route("/", forward, methods=methods),
        Route("/{path:path}", forward, methods=methods),
    ])


async def follow_logs(hub: Hub, log_dir: Path) -> None:
    """Stream new lines of every *.log file in log_dir as `log` events."""
    offsets: dict[Path, int] = {}
    for path in log_dir.glob("*.log"):
        offsets[path] = path.stat().st_size  # history lives in the files
    while True:
        for path in sorted(log_dir.glob("*.log")):
            if path.name == "monitor.log":
                continue
            size = path.stat().st_size
            start = offsets.get(path, 0)
            if size < start:
                start = 0
            if size > start:
                with path.open("rb") as stream:
                    stream.seek(start)
                    chunk = stream.read(size - start)
                complete = chunk.rfind(b"\n") + 1
                offsets[path] = start + complete
                for line in chunk[:complete].decode("utf-8", "replace").splitlines():
                    if line.strip():
                        hub.publish({"kind": "log", "source": path.stem, "line": line[:4000]})
            else:
                offsets[path] = start
        await asyncio.sleep(0.3)


def make_dashboard(hub: Hub, services: dict[str, str], agent_file: Path | None) -> Starlette:
    probe = httpx.AsyncClient(timeout=1.5)

    async def index(_: Request) -> Response:
        return FileResponse(HERE / "monitor.html")

    async def stage(_: Request) -> Response:
        return FileResponse(HERE / "stage.html")

    async def code(_: Request) -> Response:
        return FileResponse(HERE / "code.html")

    async def code_source(_: Request) -> Response:
        """The agent file the SDK is playing, for the read-only code view."""
        if agent_file is None or not agent_file.is_file():
            return JSONResponse({"path": None, "text": "", "mtime": 0})
        return JSONResponse({"path": str(agent_file), "text": agent_file.read_text("utf-8-sig"),
                             "mtime": agent_file.stat().st_mtime})

    async def history(_: Request) -> Response:
        return JSONResponse(hub.events)

    async def health(_: Request) -> Response:
        async def check(name: str, url: str):
            try:
                resp = await probe.get(url)
                return name, {"ok": resp.status_code < 500, "status": resp.status_code}
            except httpx.HTTPError as exc:
                return name, {"ok": False, "error": type(exc).__name__}
        results = await asyncio.gather(*(check(n, u) for n, u in services.items()))
        return JSONResponse(dict(results))

    async def stream(request: Request) -> Response:
        queue: asyncio.Queue = asyncio.Queue()
        hub.subscribers.add(queue)

        async def body():
            try:
                yield "retry: 2000\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"data: {json.dumps(event)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                    if await request.is_disconnected():
                        break
            finally:
                hub.subscribers.discard(queue)

        return StreamingResponse(body(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    return Starlette(routes=[
        Route("/", index), Route("/history", history), Route("/health", health),
        Route("/events", stream), Route("/demo", stage), Route("/code", code),
        Route("/code/source", code_source),
    ])


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--dashboard-port", type=int, default=8020)
    parser.add_argument("--agent-file", type=Path, help="agent.py shown at /code")
    parser.add_argument("--tap", action="append", default=[],
                        help="hop=listen_host:listen_port=upstream_url")
    args = parser.parse_args()
    log_dir = args.runtime / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    hub = Hub(args.runtime / "exchanges.jsonl")

    servers = []
    for spec in args.tap:
        hop, listen, upstream = spec.split("=", 2)
        host, port = listen.rsplit(":", 1)
        app = make_tap(hub, hop, upstream.rstrip("/"))
        servers.append(uvicorn.Server(uvicorn.Config(
            app, host=host, port=int(port), log_level="warning", access_log=False)))
    services = {
        "game": "http://127.0.0.1:8000/index.html",
        "sdk": "http://127.0.0.1:5100/health",
        "backend": "http://127.0.0.1:8010/health",
        "gateway": "http://127.0.0.1:8004/health",
    }
    dashboard = make_dashboard(hub, services, args.agent_file.resolve() if args.agent_file else None)
    servers.append(uvicorn.Server(uvicorn.Config(
        dashboard, host="127.0.0.1", port=args.dashboard_port, log_level="warning",
        access_log=False)))
    hub.publish({"kind": "system", "message": f"monitor started (pid {os.getpid()})"})
    await asyncio.gather(follow_logs(hub, log_dir), *(s.serve() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
