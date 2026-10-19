# Implementation Plan

> Unity note: the workspace root is the Unity `Assets` folder. EditMode tests run from the Unity Test Runner, or with `Unity -batchmode -projectPath d:\projects\AutoPilot_GDG -runTests -testPlatform EditMode -testResults <file>`. If Unity can't be run from the agent environment, say so and leave the test run to the user. Don't report the tests as passing unless they were actually run.

- [ ] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Per-unit entries, per-type stats and per-simulation reset
  - **CRITICAL**: This test MUST FAIL on unfixed code. The failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior. It validates the fix once it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - Test setup (one-time):
    - Confirm `com.unity.test-framework` is in `d:\projects\AutoPilot_GDG\Packages\manifest.json`. If it's missing, add it with a pinned exact version that matches the project's Unity version, and mention the change
    - Create `Assets\Tests\Editor\LoggingSystemTests.cs` in an `Editor` folder with NO asmdef, so it compiles into Assembly-CSharp-Editor and can see `LoggingSystem` (Assembly-CSharp)
    - Add a test-side mirror of the report schema (`[Serializable]` classes) that includes the existing fields plus `type`, `defenceTypeStats[]` and `enemyTypeStats[]`. Parse `Compile(..., saveToFile: false)` output with `JsonUtility.FromJson`. Fields that are absent on unfixed code parse as null/empty, so the test still compiles against the unfixed code
    - `[SetUp]` calls `LoggingSystem.Reset()`
  - **Scoped PBT Approach**: Use a seeded `System.Random`, many iterations, and print the seed on failure. Generate scenarios where `isBugCondition(X)` holds: ≥2 units per prefab named `<PrefabName>_<n>` (as the fixed spawners name them), each with random damage, positions and destroyed/killed events. Also generate a second run after a `Reset()` (which simulates `GameManager.Start`)
  - Assert expected behavior from Property 1 in the design:
    - One `defences[]`/`enemies[]` element per unit, each with its own `damageDealt`, `position`, flags and `*AtTime`
    - `enemiesKilled`/`defencesDestroyed` count units
    - Each element's `type == <PrefabName>`
    - `defenceTypeStats`/`enemyTypeStats` have one item per prefab, with `unitCount` and totals equal to the sums over that prefab's units
    - The run-2 report contains only run-2 ids
  - Separately document the `<Prefab>(Clone)` collision: logging 3 and 5 under `EnemClown(Clone)` yields one entry of 8. This confirms root cause 1 (non-unique names). It is fixed in the spawners, not in `LoggingSystem`, so record it rather than asserting it
  - Run the test on UNFIXED code if Unity is available
  - **EXPECTED OUTCOME**: The test FAILS because `type`/`*TypeStats` are missing. This proves the bug exists
  - Document the counterexamples found (seed and the failing assertion)
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 2.1, 2.2, 2.3, 2.4, 2.5, 2.8_

- [ ] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Existing logging and report behavior
  - **IMPORTANT**: Follow observation-first methodology. Record the existing-field output of unfixed `Compile` for a few fixed sequences (repeated ids, defences that never deal damage, repeated death logs)
  - In `LoggingSystemTests.cs`, add a reference model that copies the original per-id accumulation:
    - `damageDealt` is summed per id
    - A defence's `position` is the last logged value, defaulting to (0,0)
    - Each repeated destroyed/killed call overwrites the flag and `*AtTime`
    - Counts and totals are computed over entries
  - Property test (seeded `System.Random`, many iterations, seed printed on failure): random `Log*` sequences over random ids from a small pool. Include repeats, `(Clone)`, no suffix, and `_<digits>` ids. Existing fields must match the reference model field by field (`teamID`, `success`, counts, totals, and per-entry `id`, `position`, `damageDealt`, `destroyed`, `destroyedAtTime`, `killed`, `killedAtTime`). For `*AtTime`/`durationSeconds` in EditMode, compare relative to the `Reset()` time with a small tolerance
  - Example tests:
    - Every public signature compiles: `LogDefenceDamage(string, Vector2Int, float)`, `LogEnemyDamage(string, float)`, `LogDefenceDestroyed(string)`, `LogEnemyKilled(string)`, `Compile(bool, string, bool)`, `Reset()`
    - `saveToFile:false` writes no new `battlelog_*.json` in `persistentDataPath`
    - `saveToFile:true` writes one file whose content equals the returned string. Delete that file in teardown
    - `Reset()` keeps the `[RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]` attribute (check via reflection)
  - Run the tests on UNFIXED code if Unity is available
  - **EXPECTED OUTCOME**: Tests PASS. This confirms the baseline behavior to preserve
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.7_

- [ ] 3. Fix per-unit logging entries and per-simulation reset

  - [x] 3.1 Extend `Scripts\LoggingSystem.cs` additively
    - Add `public static string DeriveType(string id)`:
      - `null`/empty returns `"Unknown"`
      - Strip one trailing `(Clone)`, then trim trailing whitespace
      - Strip one trailing `_<digits>` (at least one digit) only if a non-empty prefix remains
      - An empty result returns `"Unknown"`
      - No regex and no throwing
    - Add `public string type` to `DefenceLog`/`EnemyLog`, set from `DeriveType(id)` when `GetDefence`/`GetEnemy` creates an entry
    - Add `[Serializable] DefenceTypeStats { type, unitCount, totalDamageDealt, unitsDestroyed }` and `EnemyTypeStats { type, unitCount, totalDamageDealt, unitsKilled }`. Add `List<DefenceTypeStats> defenceTypeStats` and `List<EnemyTypeStats> enemyTypeStats` to `Report` after the existing fields
    - In `Compile`, group entries by `type` inside the existing loops and emit the stats sorted with `string.CompareOrdinal`. The per-unit order and the rest of `Compile` stay unchanged
    - Leave the `Reset()` body and attribute unchanged. Don't change any public signatures
    - Rewrite the usage comment for `LoggingSystem`. It should cover:
      - The real signatures (`Vector2Int` position, `Compile(success, teamId, saveToFile)`)
      - `<PrefabName>_<n>` naming
      - The type-derivation rule
      - The `Reset()` call in `GameManager.Start`
    - _Bug_Condition: isBugCondition(X) where X has ≥2 logged units of one prefab or X.runIndex ≥ 2_
    - _Expected_Behavior: Property 1 (per-unit entries, consistent per-type stats, current-run-only data) and Property 3 (DeriveType never throws, is idempotent, and stats are sorted and consistent)_
    - _Preservation: Existing fields, accumulation, file output and the Reset attribute are unchanged (Property 2)_
    - _Requirements: 2.4, 2.5, 2.6, 2.9, 3.2, 3.3, 3.4, 3.5_

  - [x] 3.2 Rename units at instantiation and reset per simulation
    - `Scripts\PlacementSystem.cs` `InitializeDefences`:
      - Add a local `Dictionary<string,int>` counter
      - Right after `Instantiate`, set `defence.name = $"{prefab.name}_{n}"`, where `prefab = defenceIndexMapping[defenceData.defenceId]`
      - Increment the counter per `prefab.name`
    - `Scripts\EnemySpawningHandler.cs` `InitializeEnemies`:
      - Use the same pattern right after `Instantiate(enemyAIIndexMapping[spawnData.enemyId], spawnQueue)` and before `SetActive(false)`
      - Key the counter by prefab name, so repeated `enemyId` entries keep counting up
    - `Scripts\GameManager.cs` `Start`: add `LoggingSystem.Reset();` as the first statement, before the `quickTestMode` branch
    - Don't touch the Projectile, EnemyAttack, DefenceAttack, EnemyAI or DefenceBase call sites. Keep prefabs, positions, parents, active state, `myCell`, counters, spawn order and timing the same
    - _Bug_Condition: isBugCondition(X) where units share `<Prefab>(Clone)` or X.runIndex ≥ 2_
    - _Expected_Behavior: Unique `<PrefabName>_<n>` ids per initialization call, plus a fresh log and `startTime` at each `GameManager.Start`_
    - _Preservation: Spawning behavior and call sites unchanged; quickTestMode still skips Compile/push_
    - _Requirements: 2.1, 2.2, 2.3, 2.7, 2.8, 3.1, 3.6, 3.8_

  - [ ] 3.3 Add type-derivation and aggregation tests
    - **Property 3: Type Derivation** - DeriveType and type-stats invariants
    - `DeriveType` examples:
      - `EnemClown_1`→`EnemClown`
      - `Foo(Clone)`→`Foo`
      - `Foo_3(Clone)`→`Foo`
      - `Foo`→`Foo`
      - `Foo_`→`Foo_`
      - `Foo_a1`→`Foo_a1`
      - `_7`, `(Clone)`, `""`, `null`→`Unknown`
    - Seeded random property tests:
      - `DeriveType` never throws and is idempotent
      - `DeriveType(p + "_" + n) == p` for any valid `p`
      - Per kind, `Σ unitCount == entries.Count`
      - Each type's totals equal the sums over its entries
      - Types are distinct and ordinal-sorted
      - Top-level counts and totals equal the sums over all entries
    - _Requirements: 2.4, 2.5, 2.6_

  - [ ] 3.4 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Per-unit entries, per-type stats and per-simulation reset
    - **IMPORTANT**: Re-run the SAME test from task 1. Do NOT write a new test
    - Run it if Unity is available. Otherwise, report that this verification is manual
    - **EXPECTED OUTCOME**: The test PASSES. This confirms the bug is fixed
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.8_

  - [ ] 3.5 Verify preservation tests still pass
    - **Property 2: Preservation** - Existing logging and report behavior
    - **IMPORTANT**: Re-run the SAME tests from task 2. Do NOT write new tests
    - Run them if Unity is available. Otherwise, report that this verification is manual
    - **EXPECTED OUTCOME**: Tests PASS. This confirms there are no regressions
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.7_

- [ ] 4. Checkpoint - Ensure all tests pass
  - Run the full EditMode suite (tasks 1, 2 and 3.3) and confirm the project compiles with no errors. If Unity can't be run, state that clearly
  - Ask the user if questions arise

- [ ] 5. Manual PlayMode checkpoint (user-run)
  - Renaming: in quickTestMode with ≥2 units per prefab, the Hierarchy shows `<Prefab>_0`, `<Prefab>_1`, … for defences and enemies, counted per prefab. Positions, parents and spawn behavior are unchanged
  - Reset across scenes (normal mode): go Menu → Main → Menu → Main. The second `battlelog_*.json` has only run-2 ids, `durationSeconds` is close to the run-2 time, and every `*AtTime` is ≤ `durationSeconds`
  - Backend: the second report still has every existing field, and the POST to `/api/results` succeeds
  - _Requirements: 2.7, 2.8, 3.6, 3.8_
