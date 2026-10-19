"""SDK HTTP service called by the backend (`autopilot serve`).

    POST /reinforcements {"teamID": "...", "snapshot": {...}}
                  -> 200 {"defencePlacementData": [...]}
    POST /run     {"agentFileContent": "<base64>", "enemies": [...]}
                  -> 200 {"sdkStatus": {...}, "defencePlacementData": [...]}
    GET  /health  -> 200 {"status": "ok"}

Reinforcement agent failures return an empty placement list, allowing the
backend to resume the game safely. The legacy `/run` endpoint retains its
structured validation response. Each agent call runs in a guarded subprocess.
"""
import base64
import binascii
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pydantic import ValidationError

from pathlib import Path

from autopilot_sdk.contracts.schemas import ReinforcementCall, RunRequest
from autopilot_sdk.runtime.placement import run_placement
from autopilot_sdk.runtime.reinforcements import find_agent_file, run_reinforcements

logger = logging.getLogger("autopilot.server")

MAX_BODY_BYTES = 2 * 1024 * 1024


class _Handler(BaseHTTPRequestHandler):
    timeout_s: float = 45.0
    reinforcement_timeout_s: float = 3.5
    agent_dir: Path = Path("./agents")

    def _send_json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"detail": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/run", "/reinforcements"}:
            self._send_json(404, {"detail": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(400, {"detail": "Missing or oversized request body"})
            return
        raw_body = self.rfile.read(length)
        if self.path == "/reinforcements":
            self._handle_reinforcements(raw_body)
            return
        try:
            req = RunRequest.model_validate_json(raw_body)
            source = base64.b64decode(req.agentFileContent, validate=True)
        except (ValidationError, binascii.Error) as exc:
            self._send_json(400, {"detail": f"Bad request: {exc}"[:2000]})
            return
        try:
            resp = run_placement(source, req.enemies, self.timeout_s)
        except Exception as exc:
            logger.exception("placement run crashed")
            self._send_json(500, {"detail": f"SDK error: {exc}"[:2000]})
            return
        self._send_json(200, resp.model_dump(exclude_none=True))

    def _handle_reinforcements(self, raw_body: bytes) -> None:
        try:
            req = ReinforcementCall.model_validate_json(raw_body)
        except ValidationError as exc:
            self._send_json(400, {"detail": f"Bad request: {exc}"[:2000]})
            return
        if req.teamID != req.snapshot.teamID:
            self._send_json(400, {"detail": "teamID must match snapshot.teamID"})
            return

        agent_path = find_agent_file(self.agent_dir, req.teamID)
        if agent_path is None:
            logger.warning("No agent file found for team %s in %s", req.teamID, self.agent_dir)
            self._send_json(200, {"defencePlacementData": []})
            return
        try:
            source = agent_path.read_bytes()
            placements = run_reinforcements(
                source,
                req.snapshot.model_dump(mode="json"),
                self.reinforcement_timeout_s,
            )
        except Exception:
            logger.exception("reinforcement agent failed for team %s", req.teamID)
            placements = []
        self._send_json(200, {"defencePlacementData": placements})

    def log_message(self, fmt: str, *args: object) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)


def make_server(
    host: str,
    port: int,
    timeout_s: float = 45.0,
    reinforcement_timeout_s: float = 3.5,
    agent_dir: Path | str = "./agents",
) -> ThreadingHTTPServer:
    handler = type(
        "Handler",
        (_Handler,),
        {
            "timeout_s": timeout_s,
            "reinforcement_timeout_s": min(max(float(reinforcement_timeout_s), 0.05), 4.0),
            "agent_dir": Path(agent_dir),
        },
    )
    return ThreadingHTTPServer((host, port), handler)


def serve(
    host: str,
    port: int,
    timeout_s: float,
    reinforcement_timeout_s: float = 3.5,
    agent_dir: Path | str = "./agents",
) -> None:
    server = make_server(host, port, timeout_s, reinforcement_timeout_s, agent_dir)
    logger.info("autopilot SDK service on http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
