# Participant instructions

The participant guide is [README_PARTICIPANT.md](../README_PARTICIPANT.md): setup,
playing, writing `agent.py`, the LLM, scoring and the eval. It is also served at
`https://<site>/download/README_PARTICIPANT.md`.

## Legacy submission workflow

The repository also retains a separate evaluator submission flow. It uses an
`Agent` subclass implementing `place_defences(enemies)` and can be checked and
submitted with the CLI:

```bash
source .venv/bin/activate
export AUTOPILOT_BACKEND_URL=http://127.0.0.1:8000
autopilot verify --agent agent.py
autopilot register --name "Team name"
autopilot submit --agent agent.py --team-id TEAM_ID --wait-timeout 900
autopilot status SUBMISSION_ID --runs
autopilot leaderboard
```

That legacy CLI contract is documented in the SDK README. Provider credentials
belong in the organiser gateway environment, never in participant source.
