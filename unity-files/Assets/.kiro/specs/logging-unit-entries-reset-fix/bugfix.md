# Bugfix Requirements Document

## Introduction

The battle report produced by `LoggingSystem` is inaccurate in two ways, and its documentation is stale.

1. Log entries are keyed by the unit's GameObject name. Every instantiated unit of a prefab is named `<Prefab>(Clone)`, so all units of one type collapse into a single entry. Their damage is summed, and the destroyed/killed flags, `*AtTime` values and positions overwrite each other. The report cannot tell individual units apart, and the top-level counts are counts of types, not units.
2. Logged data and `startTime` reset only once, at app launch. A second simulation in the same session (Main reloaded after going back to Menu) includes the previous run's entries. `durationSeconds` and every `*AtTime` value are then measured from app launch, not from the start of the current simulation.
3. The usage comment at the bottom of `LoggingSystem.cs` refers to a non-existent `BattleLogger`, uses the wrong signatures and argument types, and omits `teamId` from `Compile`.

The report is POSTed to an external `/api/results` backend, so the JSON schema may only change additively. The public `LoggingSystem` method signatures must stay the same: `LogDefenceDamage(string, Vector2Int, float)`, `LogEnemyDamage(string, float)`, `LogDefenceDestroyed(string)`, `LogEnemyKilled(string)`, `Compile(bool, string, bool saveToFile = true)` and `Reset()`.

Unit naming scheme: right after instantiation, each unit is renamed `<PrefabName>_<n>`. `<n>` is a 0-based index counted separately per prefab type within one initialization call. Examples: `EnemClown_0`, `EnemClown_1`, `defenceSoilder_0`. A unit's type is its name with any trailing `(Clone)` removed and then any trailing `_<digits>` suffix removed. A name with neither suffix is its own type.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN two or more enemies are instantiated from the same prefab and each logs damage THEN the system records one shared entry keyed `<Prefab>(Clone)` whose `damageDealt` is the sum over all those enemies

1.2 WHEN two or more defences are instantiated from the same prefab and each logs damage THEN the system records one shared entry keyed `<Prefab>(Clone)` whose `damageDealt` is the sum over all those defences, and whose `position` is overwritten by whichever defence logged last

1.3 WHEN several units of the same prefab are destroyed or killed at different times THEN the system keeps a single `destroyed`/`killed` flag and a single `destroyedAtTime`/`killedAtTime` per type, losing the individual unit outcomes

1.4 WHEN `Compile` is called after units of the same prefab have been logged THEN the system writes one array element per prefab type in `defences[]`/`enemies[]`, and `enemiesKilled`/`defencesDestroyed` count types instead of individual units

1.5 WHEN `Compile` is called THEN the system does not provide any per-type aggregate (unit count, total damage, units destroyed/killed per type)

1.6 WHEN the Main scene is loaded a second or later time in the same application session THEN the system still holds the defence and enemy entries from earlier simulations and includes them in the next `Compile` output

1.7 WHEN the Main scene is loaded a second or later time in the same application session THEN the system measures `durationSeconds`, `destroyedAtTime` and `killedAtTime` from application launch instead of from the start of the current simulation

1.8 WHEN a developer reads the usage comment at the bottom of `LoggingSystem.cs` THEN the system documents a non-existent `BattleLogger` class, passes `transform.position` instead of a `Vector2Int`, and calls `Compile(success: true)` without `teamId`

### Expected Behavior (Correct)

2.1 WHEN two or more enemies are instantiated from the same prefab and each logs damage THEN the system SHALL record a separate entry for each enemy, keyed by its unique name (`<PrefabName>_<n>`), with `damageDealt` equal to that enemy's own damage

2.2 WHEN two or more defences are instantiated from the same prefab and each logs damage THEN the system SHALL record a separate entry for each defence, keyed by its unique name (`<PrefabName>_<n>`), with that defence's own `damageDealt` and `position`

2.3 WHEN several units of the same prefab are destroyed or killed THEN the system SHALL record the `destroyed`/`killed` flag and `destroyedAtTime`/`killedAtTime` on each unit's own entry

2.4 WHEN `Compile` is called THEN the system SHALL write one element per logged unit in `defences[]` and `enemies[]`, and `enemiesKilled`, `defencesDestroyed`, `totalDefenceDamage` and `totalEnemyDamage` SHALL be computed over individual units. Each element MAY carry an additional `type` field holding the derived unit type

2.5 WHEN `Compile` is called THEN the system SHALL add a `defenceTypeStats[]` array and an `enemyTypeStats[]` array to the report. Each array SHALL hold one item per derived unit type with `type`, `unitCount`, `totalDamageDealt`, and `unitsDestroyed` (defences) or `unitsKilled` (enemies). For each type, `unitCount` and the totals SHALL equal the count and sums over that type's per-unit entries

2.6 WHEN a unit's logged id ends in `(Clone)`, has no `_<digits>` suffix, or has both (for example, objects that were not renamed) THEN the system SHALL still log that unit and derive its type by stripping `(Clone)` and then the `_<digits>` suffix, without throwing

2.7 WHEN units are instantiated by `PlacementSystem.InitializeDefences` or `EnemySpawningHandler.InitializeEnemies`, in both quickTestMode and normal mode, THEN the system SHALL rename each instance to `<PrefabName>_<n>` immediately after instantiation, so that no two units from the same initialization share a name

2.8 WHEN the Main scene starts a simulation (`GameManager.Start`) THEN the system SHALL call `LoggingSystem.Reset()` before enemies and defences are initialized. All previous defence and enemy entries SHALL be cleared, and `startTime` SHALL be set to the current time, so `durationSeconds` and `*AtTime` are measured from the start of the current simulation

2.9 WHEN a developer reads the usage comment at the bottom of `LoggingSystem.cs` THEN the system SHALL document `LoggingSystem` with the actual signatures (`Vector2Int` position, `Compile(success, teamId, saveToFile)`), the unit naming and type-derivation scheme, and the per-simulation `Reset()` call

### Unchanged Behavior (Regression Prevention)

3.1 WHEN existing scripts (Projectile, EnemyAttack, DefenceAttack, EnemyAI, DefenceBase) call `LogDefenceDamage`, `LogEnemyDamage`, `LogDefenceDestroyed`, `LogEnemyKilled`, `Compile` or `Reset` THEN the system SHALL CONTINUE TO accept the same signatures, and those call sites SHALL CONTINUE TO pass the root GameObject's name unmodified

3.2 WHEN `Compile` is called THEN the system SHALL CONTINUE TO emit all existing report fields (`teamID`, `success`, `durationSeconds`, `enemiesKilled`, `defencesDestroyed`, `totalDefenceDamage`, `totalEnemyDamage`, `defences[]`, `enemies[]`) with their existing names and types. Existing per-unit fields (`id`, `position`, `damageDealt`, `destroyed`, `destroyedAtTime`, `killed`, `killedAtTime`) SHALL CONTINUE TO be present

3.3 WHEN `Compile` is called with `saveToFile = true` THEN the system SHALL CONTINUE TO write pretty-printed JSON to `Application.persistentDataPath/battlelog_<yyyyMMdd_HHmmss>.json`, log the path, and return the JSON string. WHEN `saveToFile = false` THEN the system SHALL CONTINUE TO return the JSON without writing a file

3.4 WHEN a single unit logs damage several times THEN the system SHALL CONTINUE TO add up its damage into that unit's `damageDealt`. For defences, the system SHALL CONTINUE TO set `position` to the most recently logged position

3.5 WHEN the application launches (including editor play mode with domain reload disabled) THEN the system SHALL CONTINUE TO run `Reset()` via the existing `[RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]` attribute

3.6 WHEN units are instantiated and renamed THEN the system SHALL CONTINUE TO use the same prefabs, positions, parents, active state, `myCell` assignment, `maxDefenceCount`/`enemyCount` counting, spawn ordering and timing as before

3.7 WHEN a defence or enemy dies THEN the system SHALL CONTINUE TO behave as it does today for these out-of-scope issues, which this fix leaves unchanged: repeated death logging every frame from DefenceBase/EnemyAI (which overwrites `*AtTime`), DefenceBase using `health < 0`, `success` always passed as `false`, a projectile's owner possibly being destroyed before the hit, `position` defaulting to (0,0) for a defence that never dealt damage, and duplicate `Compile`/push from multiple `TriggerSimulationOverPanel` calls

3.8 WHEN the simulation runs in quickTestMode THEN the system SHALL CONTINUE TO skip `Compile`/network push in `SimulationOverCleanUp`, and SHALL CONTINUE TO initialize units from the serialized inspector data
