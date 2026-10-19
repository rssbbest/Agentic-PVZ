# Implementation Plan

## Overview

First extract the current lane-sight and retarget decisions into static helpers with no behaviour change, and write exploration and preservation tests against them. Then fix the two helper bodies, re-run the tests, and check the physics and animation paths by hand in Play Mode.

## Tasks

- [x] 1. Extract current logic into helpers, then write bug condition exploration tests
  - **Property 1: Bug Condition** - Lane sight ignores passed enemies; enemies retarget to the first defence on their ray
  - **CRITICAL**: The tests in this task MUST FAIL on unfixed code. The failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: These tests encode the expected behavior. They validate the fix once they pass after task 3.1
  - **GOAL**: Surface counterexamples that demonstrate both bugs exist
  - Step A, extract the current decisions with NO behaviour change (so the tests run the code the game actually executes):
    - `Scripts\CustomGridSystem.cs`, right after the existing `isSomeoneInSight(int)`:
      - Add `// NEW:` overload `public bool isSomeoneInSight(int rowIndex, int minColumn)` that returns `IsSomeoneInSightFromColumn(grid[rowIndex], minColumn)`
      - Add `// NEW:` `public static bool IsSomeoneInSightFromColumn(List<GridCell> row, int minColumn)`. Unfixed body: `foreach` over `row`, return true on the first `cell.isSomeoneOnThisCell`, else return false. It ignores `minColumn` for now
      - Leave `isSomeoneInSight(int)` and `GetEnemyIfSomeoneInSight` unchanged
    - `Scripts\DefenceBase.cs`, in `EnemyDetectionAndSetAttack` and `EnemyDetectionAndSetScared` only: change `grid.isSomeoneInSight(myCell.y)` to `grid.isSomeoneInSight(myCell.y, myCell.x)` with a trailing `// UPDATED: skip enemies that already passed me`. The if/else bodies stay as they are
    - `Scripts\EnemyAI.cs`:
      - Add `// NEW:` `public static bool ShouldRetarget(DefenceBase cachedTarget, GameObject hitObject)`. Unfixed body returns `cachedTarget == null`, written as an `if (cachedTarget == null)` block that returns true, followed by `return false;`, so the fix only inserts one block
      - In `Movement`, replace `if (target == null)` with `if (ShouldRetarget(target, hit.collider.gameObject))` and a trailing `// UPDATED: also retarget when a different defence blocks the ray`. The body, `DoDamageLazer(target)`, the `else { target = null; }` branch and the movement code stay as they are
    - Confirm the project compiles and the existing `MidRunDefencePlacementTests` still pass (if Unity is available)
  - Step B, create `Tests\Editor\MidRunDefenceDetectionTests.cs` (no asmdef, compiles into Assembly-CSharp-Editor), following the `MidRunDefencePlacementTests` style:
    - Header `// NEW:` comment, an `Iterations` const (500), `NewSeed()` from `Environment.TickCount`, a seeded `System.Random`, and the seed in every assert message
    - `[SetUp]`: build a row of 11 `GridCell`s with `new GameObject().AddComponent<GridCell>()`, setting `nodeNumbering = new Vector2Int(x, 0)` and `isSomeoneOnThisCell` directly. Track every created GameObject in a `List<GameObject>`
    - Defence fixtures: a GameObject with `BoxCollider2D` added BEFORE `DefenceBase` (it requires the abstract `Collider2D`; `DefenceAttack` is auto-added). `Awake`/`Start` don't run in EditMode
    - `[TearDown]`: `UnityEngine.Object.DestroyImmediate` each tracked GameObject that isn't already destroyed
  - Bug condition from design: `isBugCondition_LaneSight(Q)` = occupied set non-empty AND every occupied x < `myCell.x`; `isBugCondition_StaleTarget(F)` = cached target alive AND `hit.gameObject != target.gameObject`
  - Example tests (assert expected behavior from design):
    - Soldier behind an enemy: occupied {2}, `minColumn` 4 → `IsSomeoneInSightFromColumn` is false
    - Shield behind enemies: occupied {0, 1, 3}, `minColumn` 5 → false
    - `minColumn` above the last column (11) with a non-empty row → false
    - New defence in between: cached A alive, hit = B's GameObject → `ShouldRetarget(A, B.gameObject)` is true
  - **Scoped PBT Approach** (seeded, `Iterations` cases each):
    - Lane sight (design Property 1): random non-empty occupancy with every occupied x below a random `minColumn` in [1, 12] → false. Also, for any random occupancy and `minColumn` in [-2, 12], the result equals "some occupied x ≥ `minColumn`"
    - Retarget (design Property 2): a pool of 2–6 live defence GameObjects; pick a cached target and a different hit → true
  - Run the tests on UNFIXED (extracted) code if Unity is available
  - **EXPECTED OUTCOME**: Tests FAIL. This proves both bugs exist
  - Document the counterexamples found (seed and failing case), e.g. `IsSomeoneInSightFromColumn(occupied {2}, 4)` returns true; `ShouldRetarget(A, B)` returns false for a live A and a different B. These confirm root causes 1 and 3 in the design
  - Mark complete when the helpers are extracted, the tests are written and run, and the failures are documented
  - **Result (unfixed run)**: all 7 bug condition tests failed as expected. Counterexamples: occupied {2} / minColumn 4 → true; {0,1,3} / 5 → true; {0,5,10} / 11 → true; seed 134791703 iteration 0, {0,3,4,6} / 7 → true; `ShouldRetarget(A, B)` → false (seed 134791718, cached 1, hit 2). Root causes 1 and 3 confirmed
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.4_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Lane sight and targeting unchanged outside the bug condition
  - **IMPORTANT**: Follow observation-first methodology. On UNFIXED (extracted) code, observe:
    - Occupied {2, 7}, `minColumn` 4 → true (enemy ahead)
    - Occupied {4}, `minColumn` 4 → true (own cell counts)
    - Empty row → false for any `minColumn`
    - `ShouldRetarget(A, A.gameObject)` → false (same target)
    - `ShouldRetarget(null, X)` → true, and `ShouldRetarget(destroyedA, X)` → true after `DestroyImmediate`
  - In `MidRunDefenceDetectionTests.cs`, add a test-side `OriginalRowScan(List<GridCell> row)` that copies the old `isSomeoneInSight(int)` loop (any occupied cell). This mirrors how `Property15_StartOfRunNamingPreserved` keeps a copy of the old naming algorithm
  - Example tests: mixed row (behind + ahead) is true; own cell is true; empty row is false; negative `minColumn` equals the full scan; same defence → false; null → true; destroyed → true
  - Property tests (seeded, `Iterations` cases each):
    - Lane sight (design Property 3): random occupancy and `minColumn` in [-2, 12], keep only cases where `isBugCondition_LaneSight` is false (empty row, or some occupied x ≥ `minColumn`), and assert the result equals `OriginalRowScan`. For `minColumn` ≤ 0, assert it equals `OriginalRowScan` for every occupancy
    - Targeting (design Property 4): random cached-target state (null, destroyed via `DestroyImmediate`, or alive with hit = its own GameObject) and a hit from a live pool. Assert `ShouldRetarget` equals `cachedTarget == null`
  - Multi-lane (`GetEnemyIfSomeoneInSight`), OnPoked (`TakeDamage`), thrower and suicide-bomber paths aren't touched by this fix. They are covered by leaving that code unchanged and by the manual check in task 5
  - Run the tests on UNFIXED (extracted) code if Unity is available
  - **EXPECTED OUTCOME**: Tests PASS. This confirms the baseline behavior to preserve
  - Mark complete when the tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.9, 3.12_

- [x] 3. Fix lane sight direction and stale enemy target

  - [x] 3.1 Apply the fix in the helper bodies
    - `Scripts\CustomGridSystem.cs` `IsSomeoneInSightFromColumn`: inside the `foreach`, before the occupied check, insert a `// NEW: skip cells left of minColumn (already passed)` block: `if (cell.nodeNumbering.x < minColumn)` with `continue;` in braces. Use `nodeNumbering.x`, not the list index, to match `myCell`. Update the helper's header comment if it still says `minColumn` is ignored
    - `Scripts\EnemyAI.cs` `ShouldRetarget`: after the null-check block and before `return false;`, insert a `// NEW: a different defence blocks the ray` block: `if (cachedTarget.gameObject != hitObject)` returning true. The null check stays first, so a destroyed target's `gameObject` is never read
    - No other edits. The `DefenceBase` and `Movement` call sites were already routed in task 1. `isSomeoneInSight(int)`, `GetEnemyIfSomeoneInSight`, `EnemyAttack`, `GridCell`, `DefenceAttack`, `PlacementSystem`, `GameManager`, prefabs and scenes stay as they are
    - _Bug_Condition: isBugCondition_LaneSight(Q) where Q.occupied is non-empty and all x < Q.myCell.x; isBugCondition_StaleTarget(F) where F.target is alive and F.hit.gameObject != F.target.gameObject_
    - _Expected_Behavior: expectedBehavior_LaneSight: inSight = (exists x in Q.occupied with x ≥ Q.myCell.x); expectedBehavior_StaleTarget: targetAfter = F.hit.gameObject.GetComponent<DefenceBase>() in the same frame_
    - _Preservation: Outside C, lane sight equals the original row scan and ShouldRetarget equals (cachedTarget == null); archer, penguin, thrower and bomber paths unchanged (Preservation Requirements in design)_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.5, 3.6, 3.12_

  - [x] 3.2 Verify bug condition exploration tests now pass
    - **Property 1: Expected Behavior** - Lane sight ignores passed enemies; enemies retarget to the first defence on their ray
    - **IMPORTANT**: Re-run the SAME tests from task 1. Do NOT write new tests
    - Run them if Unity is available. Otherwise, state that and leave the run to the user
    - **EXPECTED OUTCOME**: Tests PASS. This confirms both bugs are fixed
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 3.3 Verify preservation tests still pass
    - **Property 2: Preservation** - Lane sight and targeting unchanged outside the bug condition
    - **IMPORTANT**: Re-run the SAME tests from task 2. Do NOT write new tests
    - Run them if Unity is available. Otherwise, state that and leave the run to the user
    - **EXPECTED OUTCOME**: Tests PASS. This confirms there are no regressions
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.9, 3.12_

  - [x] 3.4 Confirm the existing MidRunDefencePlacementTests still pass
    - Re-run `Tests\Editor\MidRunDefencePlacementTests.cs` unchanged. Don't edit that file
    - Run it if Unity is available. Otherwise, state that and leave the run to the user
    - _Requirements: 3.8, 3.10, 3.11, 3.12_

- [x] 4. Checkpoint - Ensure all tests pass
  - Run the full EditMode suite (`MidRunDefenceDetectionTests` and `MidRunDefencePlacementTests`) and confirm the project compiles with no errors or new warnings
  - If Unity can't be run from the agent environment, state that clearly and leave the run to the user
  - Ask the user if questions arise

- [ ] 5. Manual Play Mode verification (user-run)
  - No PlayMode test assembly exists, so the physics and animation wiring is checked by hand. Use a backend run with reinforcements on, or drag a defence prefab under `PlacementSystem` in Play Mode and set `myCell` in the Inspector
  - Soldier behind an enemy: place `defenceSoilder` in a row behind a walking enemy. It stays idle, and starts firing once an enemy is in that row at its column or higher
  - Shield behind enemies: same setup with `defenceShield`. It isn't scared until an enemy is at its column or higher
  - Wizard retarget: while an `EnemyWizard` lasers A, place B between them. The laser line moves to B on the next frame, B's health drops, and A's stops dropping
  - Melee retarget: while an `Enemy` or `EnemyMonk` hits A, place B on the enemy's cell. The next hits land on B, and the damage log shows B's health falling
  - Unchanged paths: the archer still fires at enemies in any row, including ones past its column. The penguin still slides only when hit. Clown projectiles and the pirate blast still damage whatever they reach. When A is destroyed, the enemy switches to the next defence. A Quick_Test_Mode run with no mid-run placements looks the same as before
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.3, 3.4, 3.6, 3.7, 3.9, 3.12_

## Notes

- Unity: the workspace root is the Unity `Assets` folder. EditMode tests run from the Unity Test Runner, or with `Unity -batchmode -projectPath d:\projects\AutoPilot_GDG -runTests -testPlatform EditMode -testResults <file>` (batchmode can't open the project while the Editor has it open). If Unity can't be run from the agent environment, say so and leave the test run to the user. Don't report tests as passing unless they were actually run.
- Code rules for every task: minimal edits, no full-file rewrites. Mark new blocks `// NEW: ...` and changed lines `// UPDATED: ...`, with short comments. No new fields are needed; if one is, prefer `[SerializeField]` over `public`. No ternaries, `var`, `out var`, lambdas, LINQ, chained assignments or single-line `if` bodies. Use explicit types, plain loops and braces on their own lines.
- Property numbering: the hover-status properties are the workflow's Property 1 (bug condition) and Property 2 (preservation). Property 1 covers design Properties 1 and 2. Property 2 covers design Properties 3 and 4.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2"] },
    { "id": 2, "tasks": ["3.1"] },
    { "id": 3, "tasks": ["3.2", "3.3", "3.4"] },
    { "id": 4, "tasks": ["4"] },
    { "id": 5, "tasks": ["5"] }
  ]
}
```
