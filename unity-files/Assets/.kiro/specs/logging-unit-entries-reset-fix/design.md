# Logging Unit Entries & Reset Bugfix Design

## Overview

`LoggingSystem` keys entries by GameObject name. Every instantiated unit is named `<Prefab>(Clone)`, so units of one prefab collapse into a single entry. Logged data and `startTime` are also only reset at app launch, so a second simulation in the same session reports stale entries and times measured from launch. The usage comment is stale.

The fix has three parts:

1. Give every unit a unique name (`<PrefabName>_<n>`) right after `Instantiate` in `PlacementSystem.InitializeDefences` and `EnemySpawningHandler.InitializeEnemies`. Call sites keep passing `name`, so entries become per-unit with no call-site changes.
2. Call `LoggingSystem.Reset()` at the top of `GameManager.Start`, so each simulation starts with empty logs and a fresh `startTime`.
3. Extend `LoggingSystem` additively: derive a `type` per entry, add `defenceTypeStats[]` / `enemyTypeStats[]` to the report, and rewrite the usage comment. Public signatures and existing JSON fields stay the same.

## Glossary

- **Bug_Condition (C)**: A simulation scenario where two or more units would log under the same id, or where the Main scene is started a second or later time in the session.
- **Property (P)**: The report has one entry per unit, holding that unit's own values. Per-type stats are present and consistent. Data and times cover only the current simulation.
- **Preservation**: For a given sequence of log calls, everything `LoggingSystem` produced before (existing fields, accumulation, file output) stays the same. Spawning keeps the same prefabs, positions, parents, counts and timing.
- **Unit id**: The string passed to `Log*`. It is the root GameObject's `name`.
- **Unit type**: `DeriveType(id)`. Strip one trailing `(Clone)`, then one trailing `_<digits>`. `null`/empty input, or an empty result, gives `"Unknown"`.
- **`LoggingSystem`**: Static class in `Scripts/LoggingSystem.cs` that accumulates per-unit logs and compiles the JSON report POSTed to `/api/results`.
- **`InitializeDefences` / `InitializeEnemies`**: Instantiation methods in `Scripts/PlacementSystem.cs` and `Scripts/EnemySpawningHandler.cs`.
- **`startTime`**: `Time.time` at the last `Reset()`. All `durationSeconds` / `*AtTime` values are measured from it.

## Bug Details

### Bug Condition

The bug shows up in two cases. In the first, more than one unit of the same prefab is instantiated in one initialization call, so the units share the name `<Prefab>(Clone)` and therefore one dictionary entry. In the second, a simulation runs after an earlier one in the same session. `Reset()` runs only at `SubsystemRegistration`, so the old entries and the launch-time `startTime` are still there.

**Formal Specification:**
```
FUNCTION isBugCondition(X)
  INPUT: X of type SimulationScenario
         X.runIndex    : int        // 1 = first Main load in this app session
         X.spawned     : list of (kind ∈ {Defence, Enemy}, prefabName)  // per init call
         X.logEvents   : list of Log* calls made during the run
  OUTPUT: boolean

  hasIdCollision := EXISTS kind, prefabName :
                      COUNT(u IN X.spawned WHERE u.kind = kind AND u.prefabName = prefabName) >= 2
                      AND some such unit appears in X.logEvents
  isRepeatRun    := X.runIndex >= 2

  RETURN hasIdCollision OR isRepeatRun
END FUNCTION
```

Two defects apply to every `Compile`, whatever the scenario: there are no per-type stats (1.5), and the usage comment is stale (1.8). Example tests cover these. They are not part of C.

### Examples

- Two `EnemClown` deal 3 and 5 damage. Expected: two entries, `EnemClown_0` with 3 and `EnemClown_1` with 5. Actual: one `EnemClown(Clone)` entry with 8.
- Two `defenceSoilder` at (1,0) and (1,2). Expected: two entries with their own positions. Actual: one entry whose `position` is whichever defence logged last.
- One clown killed at t=4 and another at t=9. Expected: two `killed=true` entries and `enemiesKilled=2`. Actual: one entry with `killedAtTime=9` and `enemiesKilled=1`.
- Main is run, then Menu, then Main again, and the second run lasts 30 s. Expected: only run-2 entries and `durationSeconds≈30`. Actual: run-1 entries are still included, and `durationSeconds` is measured from app launch.
- Edge case: an id such as `Foo(Clone)`, `Foo`, `Foo_3(Clone)` or `_7` is still logged without throwing. Its types are `Foo`, `Foo`, `Foo` and `Unknown`.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Public signatures: `LogDefenceDamage(string, Vector2Int, float)`, `LogEnemyDamage(string, float)`, `LogDefenceDestroyed(string)`, `LogEnemyKilled(string)`, `Compile(bool, string, bool saveToFile = true)`, `Reset()`.
- Call sites in Projectile, EnemyAttack, DefenceAttack, EnemyAI and DefenceBase are untouched and keep passing `name` / `transform.name`.
- Damage keeps accumulating per id. A defence's `position` is the last logged value. The destroyed/killed flags and `*AtTime` are set as today, including the existing every-frame re-logging that overwrites them (3.7).
- All existing report fields keep their names and types (3.2). File output path and format, the `Debug.Log` of the path, and the `saveToFile=false` behavior are unchanged.
- `[RuntimeInitializeOnLoadMethod(SubsystemRegistration)]` stays on `Reset()`.
- Spawning keeps the same prefabs, positions, parents, active state, `myCell`, `maxDefenceCount`, `enemyCount`, spawn-queue ordering and timing. Only `name` changes.
- quickTestMode still skips `Compile`/push and still uses inspector data.

**Scope:**
Inputs outside C are first runs where every logged id is unique. The `LoggingSystem` changes are additive, so these runs must give the same existing fields as before, given the same ids. This covers:
- Arbitrary sequences of `Log*` calls with distinct or repeated ids, aggregated per id as today
- `Compile` with `saveToFile` true or false
- Out-of-scope behaviors listed in 3.7

## Hypothesized Root Cause

1. **Name-based keying with non-unique names**: `Instantiate` names every copy `<Prefab>(Clone)`. Call sites pass `name`, and `GetDefence`/`GetEnemy` look up the dictionary by that string, so instances of one prefab share an entry. Neither `InitializeDefences` nor `InitializeEnemies` renames instances.
2. **Reset only at subsystem registration**: `Reset()` runs once per play session through `RuntimeInitializeOnLoadMethod`. The class is static, so its dictionaries survive scene loads, and nothing calls `Reset()` when Main loads.
3. **`startTime` tied to that single reset**: `startTime = Time.time` is captured at launch (about 0), so later runs measure from launch.
4. **Report has no type dimension**: `Report` contains only flat per-entry lists, and no type is derived anywhere.
5. **Stale documentation**: The usage comment was written for an earlier `BattleLogger` API.

## Correctness Properties

Property 1: Bug Condition - Per-unit entries and per-simulation reset

_For any_ simulation scenario where isBugCondition holds, the fixed system SHALL produce a report with exactly one `defences[]`/`enemies[]` element per logged unit, keyed `<PrefabName>_<n>`. Each element SHALL hold only that unit's `damageDealt`, `position`, flags and `*AtTime`. `enemiesKilled`/`defencesDestroyed` SHALL count units. The report SHALL contain no entries from earlier runs, and `durationSeconds`/`*AtTime` SHALL be measured from `Time.time` at the current `GameManager.Start`.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.7, 2.8**

Property 2: Preservation - Existing logging and report behavior

_For any_ sequence of `Log*` calls after `Reset()` (any ids, unique or repeated), the fixed `LoggingSystem` SHALL produce the same existing report fields as the original: `teamID`, `success`, `durationSeconds`, counts, totals, and per-entry `id`, `position`, `damageDealt`, `destroyed`, `destroyedAtTime`, `killed`, `killedAtTime`. File output behavior SHALL also be the same. The only differences allowed are the additive `type`, `defenceTypeStats` and `enemyTypeStats` fields.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8**

Property 3: Type derivation and aggregation invariants

_For any_ id string, `DeriveType` SHALL return without throwing and be idempotent on its output. For any sequence of `Log*` calls, each `*TypeStats` array SHALL contain exactly one item per distinct derived type, sorted by `type` (ordinal). The `unitCount` values SHALL sum to the number of entries. Each type's `totalDamageDealt` and `unitsDestroyed`/`unitsKilled` SHALL equal the sums over that type's entries. Top-level totals and counts SHALL equal the sums over all entries.

**Validates: Requirements 2.4, 2.5, 2.6**

## Fix Implementation

### Changes Required

**File**: `Scripts/LoggingSystem.cs`
1. **Type derivation**: Add `public static string DeriveType(string id)`. For `null`/empty, return `"Unknown"`. Strip one trailing `"(Clone)"`, then trim trailing whitespace. Strip one trailing `_<digits>` (at least one digit) only if a non-empty prefix remains. If the result is empty, return `"Unknown"`. No regex, no exceptions.
2. **Entry type**: Add `public string type` to `DefenceLog` and `EnemyLog`, set to `DeriveType(id)` in `GetDefence`/`GetEnemy` when the entry is created.
3. **Type stats**: Add `[Serializable] DefenceTypeStats { string type; int unitCount; float totalDamageDealt; int unitsDestroyed; }` and `EnemyTypeStats { string type; int unitCount; float totalDamageDealt; int unitsKilled; }`. Add `List<DefenceTypeStats> defenceTypeStats` and `List<EnemyTypeStats> enemyTypeStats` to `Report`, after the existing fields.
4. **Compile**: Keep the existing loops. Group entries by `type` into a `Dictionary<string, *TypeStats>` while iterating, then emit the list sorted with `string.CompareOrdinal` on `type`. The order of per-unit `defences[]`/`enemies[]` is unchanged (dictionary enumeration, as today). The rest of `Compile` is unchanged.
5. **Reset**: Unchanged body, attribute kept. It is now also called per simulation.
6. **Usage comment**: Rewrite it for `LoggingSystem` with the real signatures (`Vector2Int` position, `Compile(success, teamId, saveToFile)`), the `<PrefabName>_<n>` naming, the type derivation rule, and the `Reset()` call in `GameManager.Start`.

**File**: `Scripts/PlacementSystem.cs`, method `InitializeDefences`
- Add a local `Dictionary<string,int> typeCounters`. Right after `Instantiate`, set `defence.name = $"{prefab.name}_{n}"`, where `prefab = defenceIndexMapping[defenceData.defenceId]` and `n` is the counter for `prefab.name`, then increment the counter. Nothing else changes.

**File**: `Scripts/EnemySpawningHandler.cs`, method `InitializeEnemies`
- Same pattern, applied after `Instantiate(enemyAIIndexMapping[spawnData.enemyId], spawnQueue)` and before `SetActive(false)`. The counter is keyed by prefab name, so two `EnemySpawnData` items with the same `enemyId` keep counting up. Nothing else changes.

**File**: `Scripts/GameManager.cs`, method `Start`
- Add `LoggingSystem.Reset();` as the first statement, before the `quickTestMode` branch.

**Timing notes**:
- In Main, `GameManager.Start` runs after every `Awake` in the scene and before any `Update`. Damage, death and spawning all happen in `Update`, coroutines or later. So `Reset()` happens before any logging in the run.
- `Time.time` keeps advancing across scene loads, so `startTime` becomes `Time.time` at Main start. `durationSeconds` and `*AtTime` are then relative to the current simulation.
- Log ids are captured when the call is made. A destroyed unit's entry keeps its renamed id.

## Testing Strategy

### Validation Approach

There is no test framework yet. Add the Unity Test Framework (`com.unity.test-framework`, pinned version) with an EditMode test assembly (`Tests/EditMode`, referencing the game scripts assembly or `Assembly-CSharp`). `LoggingSystem` is pure static logic, so EditMode tests are enough for it. Each test calls `LoggingSystem.Reset()` in `[SetUp]`, compiles with `saveToFile: false`, and parses the result with `JsonUtility.FromJson` into a test-side mirror of the report schema. Renaming and the cross-scene reset are checked manually in PlayMode.

### Exploratory Bug Condition Checking

**Goal**: Show the bug on unfixed code and confirm the root causes.

**Test Plan**: Feed `LoggingSystem` the ids the unfixed spawners produce, and check run isolation. Then inspect a real report from PlayMode.

**Test Cases**:
1. **Clone collision (EditMode)**: Log damage 3 and 5 under `EnemClown(Clone)` twice and assert two entries. This fails on unfixed code with one entry of 8, which confirms root cause 1 together with the PlayMode check below.
2. **Stale run (EditMode)**: Log entries, call `Compile`, then start a "second run" without `Reset()` and assert only the new entries exist. This fails on unfixed code and confirms root cause 2.
3. **Type stats absent (EditMode)**: Assert `enemyTypeStats` is non-empty after logging. This fails on unfixed code because the field doesn't exist.
4. **PlayMode observation**: With two units of one prefab in quickTestMode, check the Hierarchy names. Expected on unfixed code: both are `<Prefab>(Clone)`.

**Expected Counterexamples**: Summed `damageDealt` under a `(Clone)` id. Leftover entries from the previous run. `durationSeconds` larger than the run length.

### Fix Checking

```
FOR ALL X WHERE isBugCondition(X) DO
  report := runSimulation_fixed(X)
  ASSERT entriesPerUnit(report) AND idsMatch("<PrefabName>_<n>")
         AND noEntriesFromPriorRuns(report) AND timesRelativeToCurrentStart(report)
END FOR
```

### Preservation Checking

```
FOR ALL logSequence DO
  ASSERT existingFields(Compile_original(logSequence)) = existingFields(Compile_fixed(logSequence))
END FOR
```

**Testing Approach**: Use property-style randomized EditMode tests with a seeded `System.Random`, many iterations, and the seed printed on failure. They cover far more id and event combinations than hand-written cases. Expected values come from a simple reference model in the test that copies the original per-id accumulation, so existing fields can be compared directly.

**Test Cases**:
1. **Accumulation preserved**: Random sequences of damage, destroyed and killed calls over random ids, including repeats, must match the reference model field by field.
2. **Signatures/file output**: Compile-time use of every public signature. `saveToFile:false` writes no new file in `persistentDataPath`. `saveToFile:true` writes one `battlelog_*.json` whose content equals the returned string, and the test deletes it afterwards.
3. **Spawn preservation (manual PlayMode)**: Same unit counts, positions, parents, active state and spawn behavior as before, with only the names changed.

### Unit Tests

- `DeriveType` examples: `EnemClown_1`→`EnemClown`, `Foo(Clone)`→`Foo`, `Foo_3(Clone)`→`Foo`, `Foo`→`Foo`, `Foo_`→`Foo_`, `Foo_a1`→`Foo_a1`, `_7`/`(Clone)`/`""`/`null`→`Unknown`.
- `type` field is set on new entries, and `*TypeStats` items for a fixed scenario are in ordinal-sorted order.
- `Reset()` clears both dictionaries and resets `startTime`. The report after `Reset()` has empty arrays and zero totals.

### Property-Based Tests

- **Type derivation**: For random names (letters, digits, `_`, optional `(Clone)`), `DeriveType` never throws. `DeriveType(DeriveType(x)) == DeriveType(x)`. `DeriveType(p + "_" + n) == p` for any non-empty `p` that has no trailing `_<digits>`.
- **Aggregation invariants**: For random log sequences, `Σ unitCount == entries.Count` per kind. Each type's totals equal the sums over its entries. Types are distinct and sorted. Top-level `totalDefenceDamage`, `totalEnemyDamage`, `defencesDestroyed` and `enemiesKilled` equal the sums over the entries.
- **Preservation**: Existing fields from random sequences match the reference model (Property 2).

### Integration Tests

Manual PlayMode checks:
- **Renaming**: In quickTestMode with two or more units per prefab, the Hierarchy shows `<Prefab>_0`, `<Prefab>_1`, … for both defences and enemies, counted per prefab.
- **Reset across scenes (normal mode)**: Menu, Main, Menu, Main. The second report (in `persistentDataPath`) has only run-2 ids, `durationSeconds` close to the run-2 wall time, and `*AtTime` values no larger than `durationSeconds`.
- **Backend compatibility**: The second report still has every existing field, and the POST to `/api/results` succeeds.
