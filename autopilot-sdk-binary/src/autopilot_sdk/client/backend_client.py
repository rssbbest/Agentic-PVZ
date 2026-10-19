"""Backend API client (participant side) — autopilot-backend REST API.

  POST /teams                 register a team
  POST /submissions           upload agent.py (multipart: agent_file, team_id)
  GET  /submissions/{id}      status, total_score, per-config runs
  GET  /runs/{id}             SDK status, placement, evaluator output, score
  GET  /enemy_configs         the test suite
  GET  /leaderboard           best submission per team

Transient failures (connection errors, 5xx) are retried with backoff (§14);
4xx responses surface immediately as BackendError.
"""
import time

import requests

TERMINAL_SUBMISSION_STATUSES = {"completed", "failed"}


class BackendError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class _Transient(Exception):
    pass


def retry_with_backoff(func: object, *, attempts: int = 5, max_delay_s: float = 30) -> object:
    """Run zero-arg `func` with exponential backoff (1s,2s,4s,...)."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return func()  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001 - retry on any transient
            last = exc
            if i < attempts - 1:
                time.sleep(min(2**i, max_delay_s))
    assert last is not None
    raise last


class BackendClient:
    def __init__(self, base_url: str, *, timeout_s: float = 30.0, max_retries: int = 5):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._session = requests.Session()

    def _request(self, method: str, path: str, **kwargs: object) -> object:
        url = f"{self.base_url}{path}"

        def once() -> requests.Response:
            try:
                resp = self._session.request(method, url, timeout=self.timeout_s, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                raise _Transient(str(exc)) from exc
            if resp.status_code >= 500:
                raise _Transient(f"{resp.status_code} from {url}: {resp.text[:500]}")
            return resp

        try:
            resp = retry_with_backoff(once, attempts=self.max_retries)
        except _Transient as exc:
            raise BackendError(f"Backend unreachable at {url}: {exc}") from exc
        assert isinstance(resp, requests.Response)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise BackendError(f"{resp.status_code}: {detail}", resp.status_code)
        return resp.json()

    def health(self) -> dict:
        return self._request("GET", "/health")  # type: ignore[return-value]

    def create_team(self, name: str) -> dict:
        return self._request("POST", "/teams", json={"name": name})  # type: ignore[return-value]

    def submit(self, team_id: int, agent_path: str) -> dict:
        # Read up front: retries must resend the full file.
        with open(agent_path, "rb") as f:
            content = f.read()
        return self._request(  # type: ignore[return-value]
            "POST",
            "/submissions",
            data={"team_id": str(team_id)},
            files={"agent_file": ("agent.py", content, "text/x-python")},
        )

    def get_submission(self, submission_id: int) -> dict:
        return self._request("GET", f"/submissions/{submission_id}")  # type: ignore[return-value]

    def get_run(self, run_id: int) -> dict:
        return self._request("GET", f"/runs/{run_id}")  # type: ignore[return-value]

    def enemy_configs(self) -> list:
        return self._request("GET", "/enemy_configs")  # type: ignore[return-value]

    def leaderboard(self, limit: int = 50) -> list:
        return self._request("GET", "/leaderboard", params={"limit": limit})  # type: ignore[return-value]

    def wait_for_submission(
        self, submission_id: int, *, poll_s: float = 2.0, timeout_s: float = 1800.0
    ) -> dict:
        """Poll until the submission is completed/failed or `timeout_s` passes."""
        deadline = time.monotonic() + timeout_s
        while True:
            sub = self.get_submission(submission_id)
            if sub["status"] in TERMINAL_SUBMISSION_STATUSES or time.monotonic() >= deadline:
                return sub
            time.sleep(poll_s)
