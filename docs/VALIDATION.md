# Validation record

## Unified checkout — 2026-10-11

These checks were completed before the user's request to stop live testing:

- Fresh Python 3.11 environment installed from root `requirements.txt`.
- `make setup` was also verified on the installed checkout.
- **74 automated tests passed**: 47 SDK, 18 integration, 9 backend. One upstream
  Starlette/AnyIO deprecation warning was reported.
- A fresh separate Postgres database and all four local services started from
  root `.env` settings.
- Baseline and Gemini submissions each completed **50/50 portable runs**. A real
  Gemini call succeeded; its strategy was reused for the other 49 calls.
- Duplicate-placement submission: all 50 runs invalid, score zero.
- SDK outage: all 50 runs failed; restart and organiser retry completed all 50.
- Participant CLI static verification and leaderboard calls succeeded.
- Headless mobile browser check showed 50 run entries, no horizontal overflow,
  no JavaScript errors and a completed portable practice game.
- Gateway key was absent from SDK, backend and simulator process environments.
- A **fresh Linux Unity build** passed with Unity 6000.1.17f1 after resolving
  available LFS assets and removing the unavailable icon demo pack.
- SDK-produced baseline and Gemini formations each completed a real native Unity
  practice game, with accepted results, evaluator output and recorded frames.
  The native log contained no null/missing-reference/index exceptions for these games.

The raw current reports are retained locally in ignored `artifacts/`. A compact
credential-free record is included in [validation-results.json](validation-results.json).

The fresh WebGL build was **cancelled at the user's request during compilation**;
it is not recorded as a passed unified-checkout build. All test services, the
native player, compiler processes and the database were stopped. No further
live testing is scheduled. The optional GitHub test workflow is manual only.

## Earlier engine evidence — 2026-10-08

Before this repository was assembled, the corrected engine source passed native
and WebGL builds and real browser gameplay/replay checks. Baseline and Gemini
agents completed 50 native runs each, with development scores 83.7182 and 68.6448.
That evidence applies to the earlier integrated engine. The unified checkout
additionally resolves source assets, replaces the missing demo-arrow reference,
and changes Python configuration/packaging. The older results are not presented
as a new 100-run Unity test of this checkout.

Scores vary by simulation mode and may vary by runtime timing. Portable scores
are not comparable to Unity scores; see [known limitations](KNOWN_BUGS.md).
