# Known bugs and limitations

**Current list for the hosted event: [issues/event-readiness.md](issues/event-readiness.md).**
The table below describes the older managed-worker setup.

These are open issues, not claims that the local integration is competition-ready.

| Issue | Current behaviour / consequence | Next step |
| --- | --- | --- |
| Python runner is not a hostile-code sandbox | Static scanning and socket monkeypatches block known provider calls, but arbitrary code still has filesystem/process access. A separate participant script can call an LLM and exchange answers via a file. Unknown proxy domains and native code can bypass the denylist. | Run untrusted agents on organiser-controlled isolated workers with restricted files, processes and network egress. |
| Local secret file is readable by the machine owner | Provider keys are excluded from Git and SDK environment, but an agent sharing the organiser's filesystem may read `.env`; environment filtering alone does not prevent this. | Use a separate sandbox identity/container without the gateway's secrets or filesystem. |
| Queue is in memory | Restarting the adapter while jobs are pending can lose queued/running work. Backend watchdog marks missing callbacks as failed, and organiser retry is available. Completed replays are persisted. | Persist queue/leases, reconcile unfinished runs and recover on restart. |
| Lost Unity worker has no automatic lease recovery | A browser closure or native crash can leave a claimed job waiting until the backend timeout (30 minutes in managed Unity mode). | Add worker lease expiration and reassignment. |
| Callback retries are limited | Unity retries result delivery four times. Adapter-to-backend delivery can end in `callback_failed`; pending submissions may need admin retry. | Durable callback outbox and retry policy. |
| Fixed service ports | Launcher uses 8000, 8001, 8002, 8004. It detects conflicts; there is no complete configurable-port setup. | Centralise ports/URLs if multiple stacks must coexist. |
| Linux-only launcher | PID ownership checks use `/proc`; native target is Linux. Windows EXE and macOS execution have not been built/tested. | Add platform-specific launch/process management and validate those targets. |
| WebGL needs an active browser worker | Background tabs can throttle the game; closing it stops processing. Unsupported GPU shader warnings can appear with software rendering. | Prefer native workers for unattended execution; validate deployment browsers/GPUs. |
| Original build4 is not bundled or patched | The ZIP was compiled WebGL output. Its logs merge prefab names and it can end rounds before all enemies resolve. Optional `/original/` serving requires providing the ZIP separately. | Use corrected builds; merge fixes into the exact newer build4 source when available. |
| Managed builds bypass the original Menu | Build helpers include `Main.unity` directly; browser control page handles uploads and formation selection. Camera is framed for the managed 5×5 grid. Original standalone menu behaviour is not the managed product. | Preserve/test a separate menu build if that flow is required. |
| Scoring is provisional | Local evaluator weights kills 80% and surviving defences 20%. It is not an official competition evaluator. No agreed weapon budget/cost rule is enforced. | Agree competition scoring, budgets and validation rules before ranking an event. |
| Seeded spawning is not full deterministic playback | Unity collision/target ordering, frame rate and time scaling can still affect results across runs or worker platforms. Native and WebGL are not certified equivalent for competitive scoring. | Standardise workers and timing, then audit repeatability before ranking an event. |
| Portable simulation differs from Unity | Python approximation uses prefab balance but not identical physics, animations, collision timing or abilities. Scores should not be compared across modes. | Use real Unity mode for intended game evaluation. |
| LLM strategy is cached | Identical model/messages reuse the saved response across 50 waves. This is a general-purpose formation example, not 50 independent provider calls or adaptive gameplay. Keys/models may fail due to quota or availability. | Change prompt/model for a new cache key; inspect gateway status and errors. |
| Local APIs are not hardened for hosting | No participant authentication or LLM quota enforcement is provided by this demo; loopback binding is intentional. | Add authentication, rate limiting and worker isolation before exposing services. |
| Floating Python dependency ranges | Backend versions are pinned, while SDK/development packages use ranges. A full lock file is not supplied. | Lock supported dependency versions after deciding deployment platform. |

## Troubleshooting

- **Services fail to start:** inspect `.runtime/<service>.log`, check Docker and
  ensure ports are free with `make status`. Stop the older stack before starting
  this one. A partial start is cleaned up by `make stop`.
- **Submission stays simulating:** check `/health` at port 8002, connect the native
  player or open `/engine/?autopilot=1`, and inspect `unity-player.log`. Do not
  restart services during active work. A failed submission can be retried through
  `POST /submissions/{id}/retry` with `X-Admin-Secret` from local config.
- **LLM gateway returns 503:** set `AUTOPILOT_LLM_BASE_URL`/`AUTOPILOT_LLM_API_KEY` in `.env`, then stop/start services.
  HTTP 502 may indicate provider quota/model errors; the UI and smoke test report
  the failure instead of returning a fabricated formation.
- **Unity cannot build:** verify editor version, activated licence and target
  module. Inspect `.runtime/unity-linux-build.log` or `unity-webgl-build.log`.
  `UNITY_COMPAT_LIBS` can supply required libraries on a Linux distribution that
  differs from Unity's supported environment; those libraries are not bundled.
- **Database password mismatch:** `.runtime/config.json` holds the generated
  password. Preserve it with the existing database volume; changing `.env` does
  not update an already-created PostgreSQL user's password.
