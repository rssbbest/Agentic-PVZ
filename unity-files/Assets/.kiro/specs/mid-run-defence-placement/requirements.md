# Requirements Document

## Introduction

Today the backend picks every defence once, when the run starts, and the board stays fixed after that. Mid-run defence placement lets the backend add defences while the run is in progress. At a fixed real-time interval the game slows to a freeze in step with the CRT zoom-out, sends the current state snapshot to a new `/api/reinforcements` endpoint, and either places the returned batch of defences (same card format as the start of the run) or skips. It then speeds back up to normal in step with the CRT zoom-in and starts the next interval.

These requirements are derived from the approved design in `design.md`, which stays the source of truth for the technical approach. They cover the Unity client and the interface the client expects from the backend.

Out of scope, by decision: a background listener or polling in the Network_Manager, per-card validation of the backend response, any change to the State_Snapshot schema, any reinforcement behaviour in Quick_Test_Mode, and debug keys or canned test batches. Like the existing endpoints, `/api/reinforcements` is unauthenticated plain HTTP. This feature accepts that exposure and does not address it.

## Glossary

- **Game_Manager**: the `GameManager` component in the Main scene. Writes `Time.timeScale`, drives the CRT and VHS effects, tracks the Game_Phase and runs each Reinforcement_Cycle.
- **Reinforcement_Scheduler**: the `ReinforcementScheduler` class. On each tick, decides from timestamps and the tick inputs whether a Reinforcement_Cycle may start, which is called reporting the tick as ready.
- **Reinforcement_Rules**: the static `ReinforcementRules` class. Resolves Game_Phase changes, makes the place-or-skip decision, and computes the ease-down and ease-up scales for `Time.timeScale`.
- **Placement_System**: the `PlacementSystem` component, the only component that instantiates defences.
- **Placement_Ledger**: the `PlacementLedger` class owned by the Placement_System. Issues Unit_Names and keeps the Run_Placed_Count.
- **Network_Manager**: the `NetworkManager` singleton that persists across scene loads and sends every Backend request.
- **CRT_Controller**: the `CRTPauseController` component. Animates the CRT zoom-out (`ActivateEffect`) and zoom-in (`DeactivateEffect`) on Unscaled_Time.
- **VHS_Effect**: the `VHSEffect` overlay shown while fast-forward is held.
- **Logging_System**: the static `LoggingSystem` class. Keeps per-unit logs keyed by Unit_Name and builds the State_Snapshot.
- **Backend**: the external server behind the Network_Manager URLs.
- **Run**: one load of the Main scene, from `GameManager.Start` until the scene unloads.
- **Quick_Test_Mode**: a Run with `GameManager.quickTestMode` on, which uses no Backend.
- **Reinforcements_Off**: the Run state in which no Reinforcement_Cycle can start, the Game_Phase stays out of ReinforcementPause, and every check added by this feature has no effect.
- **Game_Phase**: the `GamePhase` value naming the flow that owns `Time.timeScale` and the CRT: Running (fast-forward buttons), InteractionEngine (a Taunt), ReinforcementPause (a Reinforcement_Cycle) or Over (end-of-game cleanup).
- **Taunt**: the existing Interaction Engine flow (`StartInteractionEngine` / `DeactivateInteractionEngine`). It slows the game, shows a message over the CRT, and ramps `Time.timeScale` back up when dismissed (the dismiss ramp).
- **Reinforcement_Interval**: the `reinforcementIntervalSeconds` period of Game_Time that must pass before a Reinforcement_Cycle can start. Counted from the end of initial placement, then from the end of the previous Reinforcement_Cycle.
- **Due**: the state of an enabled Reinforcement_Scheduler in which the current Reinforcement_Interval has elapsed, no tick has been reported ready since the interval started, and the scheduler has not stopped.
- **Reinforcement_Cycle**: one reinforcement pause from start to finish: optional Clean_Screenshot, CRT zoom-out with ease-down to a freeze, State_Snapshot, Reinforcement_Request, place or skip, optional Hold, and CRT zoom-in with ease-up to normal speed.
- **Abort**: the early end of a Reinforcement_Cycle after the game ends, in which the cycle stops without changing any further state.
- **Freeze_Instant**: the point in a Reinforcement_Cycle at which the ease-down has ended and `Time.timeScale` is exactly 0.
- **Start_Scale**: the `Time.timeScale` value when the CRT zoom-out of a Reinforcement_Cycle starts.
- **CRT_Duration**: the CRT_Controller `duration` setting (1.2 s by default), with the same 0.01 s minimum the CRT_Controller applies.
- **Ease_Curve**: the CRT_Controller `ease` animation curve (`EaseInOut(0, 0, 1, 1)` by default).
- **Zoom_Amount**: the CRT_Controller animation progress: 0 when fully zoomed in, 1 when fully zoomed out.
- **Fully_Idle**: the CRT_Controller state in which the CRT effect is inactive, no zoom-out or zoom-in is running, and the game camera and overlay are restored.
- **Unscaled_Time**: Unity time that ignores `Time.timeScale` (`Time.unscaledTime`, `Time.unscaledDeltaTime`).
- **Game_Time**: Unity time scaled by `Time.timeScale` (`Time.time`). It runs faster during fast-forward, slower during Taunt slow-downs, and stops while frozen.
- **Realtime**: wall-clock waiting that ignores `Time.timeScale`, such as `WaitForSecondsRealtime` or summed `Time.unscaledDeltaTime`.
- **State_Snapshot**: the JSON text returned by `LoggingSystem.CaptureStateSnapshot(teamID, SessionID)` with the Network_Manager `teamID` and `SessionID`, in the schema defined by the game-state-capture spec.
- **Reinforcement_Request**: the HTTP POST of a State_Snapshot to `pushReinforcementsUrl`, including all retry attempts.
- **Reinforcement_Response**: the body of a 2xx answer to a Reinforcement_Request.
- **Placement_Card**: one `DefencePlacementCard` entry, `{"defenceId": <int>, "defenceGridPosition": {"x": <int>, "y": <int>}}`, as used for start-of-run placement.
- **Start_Of_Run_Batch**: the defences that `PlacementSystem.InitializeDefences` places from the team data when the Run starts.
- **Mid_Run_Batch**: the Placement_Cards of one Reinforcement_Response with `placeDefences` set to true.
- **Batch_Placed_Count**: the number of defences the Placement_System reports placed in one Reinforcement_Cycle. 0 when the cycle skips, the batch is empty, the request fails, or placement throws an exception (even after some cards were placed).
- **Run_Placed_Count**: the total number of defences placed in the Run so far, start-of-run and mid-run together, as counted by the Placement_Ledger.
- **Unit_Name**: the GameObject name of a placed defence, `<PrefabName>_<n>`, which the Logging_System uses as the unit id.
- **Hold**: the Realtime wait of `reinforcementHoldSeconds` at `Time.timeScale` 0 between placement and the CRT zoom-in.
- **Resume_State**: the end state of a completed Reinforcement_Cycle: `Time.timeScale` exactly 1, VHS_Effect off, CRT zoom-in finishing, Game_Phase Running, and the Reinforcement_Interval restarted.
- **Clean_Screenshot**: a JPG screenshot captured in a frame in which the CRT_Controller is Fully_Idle, before the CRT zoom-out of the Reinforcement_Cycle starts, so the image shows no CRT overlay.

## Requirements

### Requirement 1: Reinforcement settings and feature switch

**User Story:** As a developer, I want the reinforcement ping configurable in the Inspector and fully off when no backend is in use, so that I can tune it per build and keep Quick_Test_Mode runs identical to today.

#### Acceptance Criteria

1. THE Game_Manager SHALL provide serialized Inspector settings under a "Reinforcements" header: `reinforcementsEnabled` (default true), `reinforcementIntervalSeconds` (default 30) and `reinforcementHoldSeconds` (default 1).
2. WHILE Quick_Test_Mode is on, THE Game_Manager SHALL keep the Run in Reinforcements_Off.
3. IF the Network_Manager instance is missing when `GameManager.Start` runs, THEN THE Game_Manager SHALL keep the Run in Reinforcements_Off.
4. WHERE `reinforcementsEnabled` is off, THE Game_Manager SHALL keep the Run in Reinforcements_Off.
5. IF the Reinforcement_Scheduler is created disabled or with a Reinforcement_Interval of 0 seconds or less, THEN THE Reinforcement_Scheduler SHALL report every tick as not ready for the whole Run.
6. WHILE the Run is in Reinforcements_Off, THE Game_Manager SHALL run Taunts, fast-forward, the end screen, result compilation and result upload exactly as before this feature.
7. WHILE Quick_Test_Mode is off, WHEN `GameManager.Start` runs, THE Game_Manager SHALL overlay the team data's optional `gameSettings` object (`reinforcementsEnabled`, `reinforcementIntervalSeconds`, `reinforcementHoldSeconds`, `sendScreenshotToo`) onto the four Inspector settings before starting the first Reinforcement_Interval. A missing, null or invalid `gameSettings` object, or any missing key, keeps the Inspector value.

### Requirement 2: Reinforcement interval

**User Story:** As a backend developer, I want a reinforcement decision point at a fixed real-time interval, so that the evaluator gets regular chances to add defences whatever the game speed.

#### Acceptance Criteria

1. WHILE Quick_Test_Mode is off, WHEN `GameManager.Start` finishes placing the Start_Of_Run_Batch, THE Game_Manager SHALL start the first Reinforcement_Interval at the current Game_Time.
2. THE Reinforcement_Scheduler SHALL measure the Reinforcement_Interval as the difference between the current Game_Time and the start timestamp of the interval.
3. THE Reinforcement_Scheduler SHALL count Game_Time toward the Reinforcement_Interval, so fast-forward shortens the real wait in proportion to `fastForwardTimeScale` and Taunt slow-downs lengthen it.
4. WHILE less than `reinforcementIntervalSeconds` of Game_Time has passed since the current Reinforcement_Interval started, THE Reinforcement_Scheduler SHALL report every tick as not ready.
5. WHILE the Reinforcement_Scheduler is Due, WHEN a tick reports the Game_Phase as Running, the CRT_Controller as Fully_Idle, and neither wave completion nor game over, THE Reinforcement_Scheduler SHALL report the tick as ready.
6. WHILE the Reinforcement_Scheduler is Due, WHEN a tick reports neither wave completion nor game over, and reports the Game_Phase as InteractionEngine or ReinforcementPause or the CRT_Controller as not Fully_Idle, THE Reinforcement_Scheduler SHALL report the tick as not ready and stay Due.
7. WHEN the Reinforcement_Scheduler reports a tick as ready, THE Reinforcement_Scheduler SHALL report every later tick as not ready until the Reinforcement_Interval restarts.
8. IF a tick reports wave completion, game over or the Game_Phase Over, THEN THE Reinforcement_Scheduler SHALL stop and report that tick and every later tick as not ready for the rest of the Run, including after any interval restart.
9. WHEN a Reinforcement_Cycle reaches the Resume_State, THE Game_Manager SHALL restart the Reinforcement_Interval at the current Game_Time.
10. WHILE Quick_Test_Mode is off, THE Game_Manager SHALL tick the Reinforcement_Scheduler once per frame, after the end-of-game check of that frame, with the current Game_Time, Game_Phase, wave completion, game over and Fully_Idle state.

### Requirement 3: Reinforcement cycle

**User Story:** As a backend developer, I want each reinforcement ping to run as one fixed sequence, so that every decision is made on a frozen board and play always continues afterwards.

#### Acceptance Criteria

1. WHEN the Reinforcement_Scheduler reports a tick as ready, THE Game_Manager SHALL request the Game_Phase ReinforcementPause and start a Reinforcement_Cycle in the same frame.
2. THE Game_Manager SHALL run the stages of a Reinforcement_Cycle in this order: optional Clean_Screenshot, VHS_Effect off with CRT zoom-out and ease-down, State_Snapshot at the Freeze_Instant, Reinforcement_Request, place or skip, optional Hold, CRT zoom-in with ease-up, Resume_State.
3. THE Game_Manager SHALL run at most one Reinforcement_Cycle at a time.
4. THE Game_Manager SHALL start a Reinforcement_Cycle only when the Reinforcement_Scheduler reports a tick as ready.
5. THE Game_Manager SHALL prefix each console message logged by a Reinforcement_Cycle with "[Reinforcements] ".

### Requirement 4: Game phase arbitration

**User Story:** As a player, I want Taunts, reinforcement pauses and the end screen to take turns controlling game speed and the CRT, so that the game never gets stuck frozen or half zoomed.

#### Acceptance Criteria

1. THE Game_Manager SHALL track one current Game_Phase out of Running, InteractionEngine, ReinforcementPause and Over, starting each Run at Running.
2. WHEN a Taunt starts, THE Game_Manager SHALL request the Game_Phase InteractionEngine.
3. WHILE the Game_Phase is InteractionEngine, WHEN the Taunt dismiss ramp finishes, THE Game_Manager SHALL request the Game_Phase Running.
4. WHEN `TriggerSimulationOverPanel` runs for the first time in a Run, THE Game_Manager SHALL request the Game_Phase Over.
5. WHEN the Game_Manager requests a Game_Phase, THE Game_Manager SHALL set the Game_Phase that the Reinforcement_Rules resolve from the current and the requested Game_Phase.
6. WHILE the current Game_Phase is Over, THE Reinforcement_Rules SHALL resolve every requested Game_Phase to Over.
7. WHILE the current Game_Phase is Running, InteractionEngine or ReinforcementPause, THE Reinforcement_Rules SHALL resolve a requested Game_Phase to the requested value.
8. WHILE the Game_Phase is ReinforcementPause, THE Game_Manager SHALL skip Taunt evaluation.
9. WHEN a Reinforcement_Cycle reaches the Resume_State, THE Game_Manager SHALL evaluate Taunt conditions again from the next frame and start a Taunt only for a condition that holds at that time.
10. WHILE the Game_Phase is ReinforcementPause, THE Game_Manager SHALL let only the Reinforcement_Cycle write `Time.timeScale` or start and stop the CRT_Controller effect.
11. WHILE the Game_Phase is Running, InteractionEngine or Over, THE Game_Manager SHALL evaluate Taunt conditions as before this feature, including overlapping Taunts.

### Requirement 5: Smooth freeze synced to the CRT

**User Story:** As a player, I want the game to slow smoothly to a stop in step with the CRT zoom-out, so that the reinforcement pause looks like part of the CRT effect instead of a hard freeze.

#### Acceptance Criteria

1. THE CRT_Controller SHALL provide a read-only check that reports whether the CRT_Controller is Fully_Idle.
2. WHILE the CRT effect is active or a CRT zoom-out or zoom-in is running, THE CRT_Controller SHALL report not Fully_Idle.
3. WHEN a Reinforcement_Cycle reaches the zoom-out stage, THE Game_Manager SHALL start the CRT_Controller zoom-out from a Zoom_Amount of 0.
4. WHILE the CRT zoom-out of a Reinforcement_Cycle runs, THE Game_Manager SHALL set `Time.timeScale` every frame to Start_Scale × (1 − Zoom_Amount), advancing on the same Unscaled_Time steps, CRT_Duration and Ease_Curve as the CRT_Controller.
5. WHEN the CRT zoom-out of a Reinforcement_Cycle finishes, THE Game_Manager SHALL set `Time.timeScale` to exactly 0 in the same frame.
6. THE Game_Manager SHALL use the `Time.timeScale` in effect when the zoom-out starts as the Start_Scale, including fast-forward speed and Taunt ramp overshoot.
7. THE Reinforcement_Rules SHALL return an ease-down scale between 0 and the Start_Scale inclusive for any elapsed time, any duration, any Start_Scale of 0 or more, and any Ease_Curve, including a missing Ease_Curve and an Ease_Curve with values outside 0 to 1.
8. WHEN the elapsed time is at least the duration, or the duration is 0 or less, THE Reinforcement_Rules SHALL return an ease-down scale of exactly 0.
9. WHEN the elapsed time is 0 or less and the duration is positive, THE Reinforcement_Rules SHALL return the Start_Scale as the ease-down scale.
10. IF the Start_Scale is negative, THEN THE Reinforcement_Rules SHALL use 0 as the Start_Scale.
11. IF the Ease_Curve is missing, THEN THE Reinforcement_Rules SHALL use linear progress for the ease-down and ease-up scales.
12. THE Reinforcement_Rules SHALL return ease-down scales that are non-increasing in elapsed time for any non-decreasing Ease_Curve.

### Requirement 6: State snapshot at the freeze

**User Story:** As a backend developer, I want the board state from the moment the game freezes, so that the evaluator decides on the same board the placement is applied to.

#### Acceptance Criteria

1. WHEN a Reinforcement_Cycle reaches the Freeze_Instant, THE Game_Manager SHALL capture the State_Snapshot of the frozen board before submitting the Reinforcement_Request.
2. THE Game_Manager SHALL send the captured State_Snapshot text unchanged as the body of the Reinforcement_Request.
3. THE Logging_System SHALL keep the State_Snapshot schema unchanged by this feature.
4. WHERE `logStateSnapshotLocally` is on, THE Game_Manager SHALL log the State_Snapshot of each Reinforcement_Request to the console.

### Requirement 7: Reinforcement request

**User Story:** As a backend developer, I want one JSON request per ping with the existing retry behaviour, so that a slow or missing backend can delay a ping but cannot break the game.

#### Acceptance Criteria

1. THE Network_Manager SHALL provide a serialized `pushReinforcementsUrl` setting under a "Reinforcement settings" header, with the default `http://127.0.0.1:5000/api/reinforcements`.
2. THE Game_Manager SHALL submit exactly one Reinforcement_Request in each Reinforcement_Cycle that reaches the Freeze_Instant.
3. WHEN the Game_Manager submits a Reinforcement_Request, THE Network_Manager SHALL POST the body to `pushReinforcementsUrl` with the content type `application/json`.
4. THE Network_Manager SHALL contact `pushReinforcementsUrl` only for Reinforcement_Requests submitted by the Game_Manager.
5. THE Network_Manager SHALL send each Reinforcement_Request with the existing `maxAttempts`, `retryDelaySeconds` and `timeoutSeconds` settings, using a new web request for every attempt.
6. IF an attempt fails with a connection error and attempts remain, THEN THE Network_Manager SHALL wait `retryDelaySeconds` of Realtime and send the next attempt.
7. IF an attempt fails with any result other than a connection error, such as an HTTP error status, THEN THE Network_Manager SHALL report failure without further attempts.
8. WHEN a Reinforcement_Request finishes, THE Network_Manager SHALL invoke the completion callback exactly once, with success and the Reinforcement_Response for a 2xx answer, or with failure and an error message otherwise.
9. IF the Reinforcement_Request body is null, empty or whitespace, THEN THE Network_Manager SHALL log one warning, send no request, and invoke the completion callback with failure before returning.
10. THE Network_Manager SHALL report Reinforcement_Request outcomes through the completion callback and the console only, leaving the UI controller untouched.
11. WHERE `DEBUG_MODE` is on, THE Network_Manager SHALL send Reinforcement_Requests to the configured `pushReinforcementsUrl`.
12. WHILE a Reinforcement_Request is in flight, THE Game_Manager SHALL keep `Time.timeScale` at exactly 0.
13. IF every attempt of a Reinforcement_Request times out, THEN THE Network_Manager SHALL report failure after `maxAttempts` attempts of up to `timeoutSeconds` each, separated by `retryDelaySeconds` Realtime waits (about 34 seconds with the default settings).
14. IF a Reinforcement_Request fails, THEN THE Game_Manager SHALL log one warning, place no defences, and continue the Reinforcement_Cycle to the Resume_State.

### Requirement 8: Place-or-skip decision

**User Story:** As a backend developer, I want to answer each ping with either a batch of defences or a skip, in the start-of-run card format, so that the evaluator can reuse its existing placement output.

#### Acceptance Criteria

1. WHEN a Reinforcement_Request succeeds and the Reinforcement_Rules decide to place, THE Game_Manager SHALL pass the full Reinforcement_Response text to the Placement_System as one Mid_Run_Batch.
2. WHEN a Reinforcement_Request succeeds and the Reinforcement_Rules decide to skip, THE Game_Manager SHALL log the skip, place no defences, and continue the Reinforcement_Cycle to the Resume_State.
3. THE Reinforcement_Rules SHALL decide to place if and only if the Reinforcement_Response parses as JSON with `placeDefences` set to true.
4. WHEN the Reinforcement_Response sets `placeDefences` to false or has no `placeDefences` key, THE Reinforcement_Rules SHALL decide to skip.
5. IF the Reinforcement_Response is null, empty or whitespace, THEN THE Reinforcement_Rules SHALL decide to skip.
6. IF the Reinforcement_Response is not readable as JSON, THEN THE Reinforcement_Rules SHALL log one warning with the prefix "[Reinforcements] " and decide to skip.
7. THE Reinforcement_Rules SHALL return a place-or-skip decision for any input string, including null, without throwing an exception.

### Requirement 9: Mid-run defence placement

**User Story:** As a backend developer, I want a Mid_Run_Batch to add defences on top of the current board, so that start-of-run defences and earlier reinforcements stay in place.

#### Acceptance Criteria

1. THE Placement_System SHALL read the Placement_Cards of a Mid_Run_Batch from the Reinforcement_Response text with the same Placement_Card schema as the Start_Of_Run_Batch, ignoring the `placeDefences` key.
2. WHEN the Placement_System receives a Mid_Run_Batch, THE Placement_System SHALL place exactly one defence per Placement_Card, in card order, and report the number of defences placed.
3. THE Placement_System SHALL set up each mid-run defence like a start-of-run defence: positioned on the grid cell named by the Placement_Card, with `myCell` set to that cell, parented to the Placement_System, and named by the Placement_Ledger.
4. WHEN the Placement_System places a Mid_Run_Batch, THE Placement_System SHALL keep every existing defence and the stored start-of-run placement data unchanged.
5. IF the Mid_Run_Batch text is blank, or the parsed batch or card list is missing, THEN THE Placement_System SHALL place no defences and report 0 placed.
6. THE Placement_System SHALL place each Placement_Card as the Backend sent the card, relying on the Backend for valid defence ids and free cells inside the grid.
7. IF placing a Placement_Card throws an exception, THEN THE Game_Manager SHALL catch the exception, log one error, keep the defences already placed, and continue the Reinforcement_Cycle to the Resume_State.

### Requirement 10: Run-wide unique unit names

**User Story:** As an analyst reading the final report, I want every defence in a run to have its own name, so that start-of-run and mid-run units never merge into one log entry.

#### Acceptance Criteria

1. THE Placement_Ledger SHALL name each placement `<PrefabName>_<n>`, where n is the number of earlier placements of the same prefab in the Run.
2. THE Placement_Ledger SHALL return a distinct Unit_Name for every placement in a Run, across the Start_Of_Run_Batch and all Mid_Run_Batches.
3. THE Placement_System SHALL use one Placement_Ledger for the whole Run, shared by start-of-run and mid-run placement.
4. THE Placement_Ledger SHALL build Unit_Names from which `LoggingSystem.DeriveType` returns the original prefab name, for every non-empty prefab name without a trailing `_<digits>` or `(Clone)` suffix.
5. WHEN the Placement_System places the Start_Of_Run_Batch, THE Placement_System SHALL produce the same prefabs, positions, parents, `myCell` values and Unit_Names as before this feature.

### Requirement 11: Defence count includes mid-run placements

**User Story:** As a game designer, I want the max defence count to include reinforcements, so that the "half the defences lost" Taunt compares losses against every defence placed in the run.

#### Acceptance Criteria

1. THE Placement_Ledger SHALL report a Run_Placed_Count equal to the number of defences placed so far in the Run, start-of-run and mid-run together.
2. THE Placement_Ledger SHALL keep the Run_Placed_Count non-decreasing for the whole Run, including after defences are destroyed.
3. THE Placement_System SHALL return the Run_Placed_Count from `MaxDefenceCount()`.
4. WHILE only the Start_Of_Run_Batch has been placed, THE Placement_System SHALL return the same `MaxDefenceCount()` value as before this feature.

### Requirement 12: Hold and resume

**User Story:** As a player, I want to see new defences appear in the CRT feed and then the game speed back up smoothly, so that reinforcements are visible and play always continues at normal speed.

#### Acceptance Criteria

1. WHEN the place-or-skip stage of a Reinforcement_Cycle ends with a Batch_Placed_Count of at least 1, THE Game_Manager SHALL run the Hold before starting the CRT zoom-in.
2. WHEN the place-or-skip stage of a Reinforcement_Cycle ends with a Batch_Placed_Count of 0, THE Game_Manager SHALL start the CRT zoom-in without a Hold.
3. WHILE the Hold runs, THE Game_Manager SHALL keep `Time.timeScale` at exactly 0 with the CRT zoomed out.
4. WHEN a Reinforcement_Cycle reaches the zoom-in stage, THE Game_Manager SHALL start the CRT_Controller zoom-in from a Zoom_Amount of 1.
5. WHILE the CRT zoom-in of a Reinforcement_Cycle runs, THE Game_Manager SHALL set `Time.timeScale` every frame to 1 − Zoom_Amount, advancing on the same Unscaled_Time steps, CRT_Duration and Ease_Curve as the CRT_Controller.
6. WHEN the CRT zoom-in of a Reinforcement_Cycle finishes, THE Game_Manager SHALL set `Time.timeScale` to exactly 1 and request the Game_Phase Running in the same frame.
7. THE Game_Manager SHALL bring every Reinforcement_Cycle that does not Abort to the Resume_State, on each of the place, skip, empty batch, request failure, unreadable response and placement exception paths.
8. THE Reinforcement_Rules SHALL return an ease-up scale between 0 and 1 inclusive for any elapsed time, any duration and any Ease_Curve, including a missing Ease_Curve and an Ease_Curve with values outside 0 to 1.
9. WHEN the elapsed time is at least the duration, or the duration is 0 or less, THE Reinforcement_Rules SHALL return an ease-up scale of exactly 1.
10. WHEN the elapsed time is 0 or less and the duration is positive, THE Reinforcement_Rules SHALL return an ease-up scale of exactly 0.
11. THE Reinforcement_Rules SHALL return ease-up scales that are non-decreasing in elapsed time for any non-decreasing Ease_Curve.
12. THE Reinforcement_Rules SHALL return an ease-up scale and an ease-down scale with a Start_Scale of 1 that add up to 1 within float tolerance, for any elapsed time strictly between 0 and a positive duration and any Ease_Curve with values between 0 and 1.

### Requirement 13: Game over during a reinforcement pause

**User Story:** As a player, I want the end of the game to take over at once, even during a reinforcement pause, so that the end screen and results work exactly as before.

#### Acceptance Criteria

1. WHEN the game ends during any stage of a Reinforcement_Cycle, THE Game_Manager SHALL Abort the Reinforcement_Cycle at the next frame step of the cycle.
2. WHEN a Reinforcement_Cycle Aborts, THE Game_Manager SHALL leave `Time.timeScale`, the CRT_Controller, the VHS_Effect, defence placement, the Reinforcement_Scheduler and the Game_Phase to the end-of-game cleanup.
3. IF a Reinforcement_Response arrives after the Reinforcement_Cycle Aborted or after the Main scene unloaded, THEN THE Game_Manager SHALL discard the Reinforcement_Response without placing defences or changing game state.
4. WHEN the game ends during a Reinforcement_Cycle, THE Game_Manager SHALL show the end panel, compile the results and push the results as it does without this feature.
5. WHEN the game ends during the CRT zoom-in of a Reinforcement_Cycle, THE CRT_Controller SHALL reverse the zoom-in into the end-of-game zoom-out.

### Requirement 14: Fast-forward during a reinforcement pause

**User Story:** As a player, I want fast-forward to have no effect during a reinforcement pause, so that I can't unfreeze the game while the backend decides.

#### Acceptance Criteria

1. WHEN a Reinforcement_Cycle reaches the zoom-out stage, THE Game_Manager SHALL turn the VHS_Effect off before starting the CRT zoom-out.
2. WHILE the Game_Phase is ReinforcementPause, THE Game_Manager SHALL ignore fast-forward press and release, leaving `Time.timeScale` and the VHS_Effect unchanged.
3. WHEN a Reinforcement_Cycle reaches the Resume_State after fast-forward was held at the start of the cycle, THE Game_Manager SHALL leave the game at `Time.timeScale` exactly 1 with the VHS_Effect off, without restoring fast-forward.
4. WHILE the Game_Phase is Running, InteractionEngine or Over, THE Game_Manager SHALL handle fast-forward press and release as before this feature.

### Requirement 15: Optional clean screenshot

**User Story:** As a backend developer, I want an optional screenshot of the board with each ping, so that I can see what the evaluator saw without the CRT overlay.

#### Acceptance Criteria

1. THE Game_Manager SHALL provide a serialized `sendScreenshotToo` setting under the "Reinforcements" header, labelled "Send Screenshot Too" in the Inspector, with the default false.
2. WHERE `sendScreenshotToo` is on, THE Game_Manager SHALL capture a Clean_Screenshot as the first stage of each Reinforcement_Cycle, before turning the VHS_Effect off and starting the CRT zoom-out.
3. WHERE `sendScreenshotToo` is on, THE Game_Manager SHALL send the Clean_Screenshot to the existing screenshot endpoint through `NetworkManager.PushScreenshot`.
4. WHERE `sendScreenshotToo` is on, THE Game_Manager SHALL wait for the screenshot capture and encoding only, and continue the Reinforcement_Cycle while the upload runs.
5. WHERE `sendScreenshotToo` is on, THE Game_Manager SHALL apply the existing capture guards to the reinforcement screenshot: `captureEnabled` on, no other screenshot in progress, and a screen size above 0.
6. IF a capture guard skips the reinforcement screenshot or the capture fails, THEN THE Game_Manager SHALL continue the Reinforcement_Cycle without the screenshot.
7. IF the screenshot upload fails, THEN THE Game_Manager SHALL send the Reinforcement_Request and apply the Reinforcement_Response as without the screenshot.
8. WHERE `sendScreenshotToo` is off, THE Game_Manager SHALL start the CRT zoom-out without capturing a screenshot.
9. THE Game_Manager SHALL keep `CaptureAndSendScreenshot` behaving as before this feature.

### Requirement 16: Backend interface

**User Story:** As a backend developer, I want a clear contract for `/api/reinforcements`, so that I can build the endpoint without reading the game code.

#### Acceptance Criteria

1. THE Backend SHALL accept `POST /api/reinforcements` with a State_Snapshot body and the content type `application/json`.
2. WHEN the Backend decides to place defences, THE Backend SHALL answer with a 2xx status and a body of the form `{"placeDefences": true, "defencePlacementData": [ <Placement_Card>, ... ]}`.
3. WHEN the Backend decides to skip, THE Backend SHALL answer with a 2xx status and either `{"placeDefences": false}` or an empty body.
4. THE Backend SHALL fill every returned Placement_Card with a valid defence id and a free cell inside the grid.
5. WHERE the game runs as a WebGL build, THE Backend SHALL answer the CORS preflight request for `/api/reinforcements` with the origin of the game page.

### Requirement 17: Testable decision logic

**User Story:** As a developer, I want the scheduling, phase, easing, decision and naming logic to run without a scene, so that EditMode tests can check the logic over many generated inputs.

#### Acceptance Criteria

1. THE Reinforcement_Scheduler SHALL work without scene objects or MonoBehaviour callbacks, taking the current Game_Time as an argument on every call.
2. THE Reinforcement_Scheduler SHALL run each tick in constant time without allocating memory.
3. THE Reinforcement_Rules SHALL work without scene objects or MonoBehaviour callbacks and compute every result from the arguments of the call.
4. THE Placement_Ledger SHALL work without scene objects or MonoBehaviour callbacks.
