# Mid-Run Defence Detection Bugfix Design

## Overview

Two decisions in the game make an assumption that only holds at start of run: "every enemy in a defence's row is at or to the right of that defence, and an enemy's first defence on its ray is the one it locked onto." Mid-run placement breaks both.

- **Bug A (lane sight):** `CustomGridSystem.isSomeoneInSight(rowIndex)` scans the whole row. `DefenceBase` calls it for OnSight (`defenceSoilder`) and Static (`defenceShield`) defences with only `myCell.y`. A defence placed behind an enemy that has already passed its column attacks or acts scared at an enemy it can never reach.
- **Bug B (stale target):** `EnemyAI.Movement` only refills its cached `target` when it is `null`. A defence placed between an enemy and its current target blocks the ray, but the laser damage (`DoDamageLazer(target)`) and melee hits (`EnemyAttackEvent` → `DoDamage(target)`) keep going to the old target.

The fix is three small edits:

1. `CustomGridSystem`: a new overload `isSomeoneInSight(int rowIndex, int minColumn)` that skips cells left of `minColumn`. A static pure helper does the scan so EditMode tests can call it.
2. `DefenceBase`: the OnSight and Static checks pass `myCell.x` as `minColumn`. The two-line call-site change is the whole behaviour change for Bug A.
3. `EnemyAI`: the `if (target == null)` refill becomes "refill when nothing is cached, or the ray hit a different defence". A static pure helper decides this.

Nothing else changes: `EnemyAttack`, `GridCell`, `DefenceAttack`, `PlacementSystem`, `GameManager`, prefabs and scenes stay as they are.

## Glossary

- **Bug_Condition (C)**: The inputs that trigger either bug. C_A is a lane-sight query from an OnSight or Static defence whose row has enemies, but only at columns lower than the defence's column. C_B is a melee or laser enemy frame where the ray's first defence differs from the cached target, and the cached target still exists.
- **Property (P)**: The intended result on C. For C_A, "in sight" is false. For C_B, the cached target becomes the defence the ray hit in that frame, so damage and the laser endpoint go to it.
- **Preservation**: For every input outside C, the fixed code gives the same lane-sight result and the same cached target as the original code. Archer, penguin, thrower and bomber behaviour, placement, naming, logging and death handling stay the same.
- **Lane-sight defence**: A `DefenceBase` with `attackTriggerType` OnSight (attacks via `SetTriggerAttack`) or Static (scared via `SetTriggerScared`).
- **`myCell`**: A defence's placement cell, `cell.nodeNumbering`. `PlacementSystem.PlaceDefence` sets it right after `Instantiate`, before the defence's first `Update`.
- **Occupied column**: Column `x` in row `y` where `GridCell.isSomeoneOnThisCell` is true. An enemy whose collider spans two cells occupies both.
- **Passed**: An enemy occupies only columns lower than the defence's `myCell.x` in that row. Enemies walk left (-x) and lane-sight defences fire right (+x).
- **Attack ray**: `Physics2D.Raycast(transform.position, directionOfAttack, attackRange, targetLayers)` in `EnemyAI.Movement`. It reports the first collider along the ray. Project settings have `queriesStartInColliders` and `queriesHitTriggers` on (checked in `ProjectSettings/Physics2DSettings.asset`), so a ray that starts inside a defence's collider reports that defence.
- **Cached target**: The private `EnemyAI.target` (`DefenceBase`). `DoDamageLazer(target)` reads it every frame, and `EnemyAttackEvent` reads it on each melee animation event.
- **Alive (for C_B)**: The cached target hasn't been destroyed yet, so Unity's `target == null` is false. A defence in its death animation counts as alive until `EventParentConnectorDefence.DestroyParentGameObject` runs, because that is also when the original code first lets go of it.

## Bug Details

### Bug Condition

Bug A shows up when an OnSight or Static defence's row has at least one occupied column, and every occupied column is lower than the defence's column. `isSomeoneInSight` has no column input, so it returns true for those enemies.

Bug B shows up when a melee or laser enemy's ray hits a defence-tagged collider that isn't its cached target, and the cached target still exists. The refill condition only looks at `target == null`, so the cache keeps the old defence.

**Formal Specification:**
```
FUNCTION isBugCondition(X)
  INPUT: X is a LaneSightQuery or an EnemyFrame
  OUTPUT: boolean

  IF X is LaneSightQuery THEN
    RETURN isBugCondition_LaneSight(X)
  END IF
  RETURN isBugCondition_StaleTarget(X)
END FUNCTION

FUNCTION isBugCondition_LaneSight(Q)
  INPUT: Q.trigger  : AttackTriggerType
         Q.myCell   : (x, y)                       // defence placement cell
         Q.occupied : set of column x in row Q.myCell.y with isSomeoneOnThisCell = true
  OUTPUT: boolean

  RETURN Q.trigger IN {OnSight, Static}
         AND Q.occupied IS NOT EMPTY
         AND FOR ALL x IN Q.occupied : x < Q.myCell.x
END FUNCTION

FUNCTION isBugCondition_StaleTarget(F)
  INPUT: F.enemyType : EnemyType
         F.hit       : first collider on the attack ray this frame, or null
         F.target    : cached DefenceBase, or null / destroyed
  OUTPUT: boolean

  RETURN F.enemyType IN {Melee, Lazer}
         AND F.hit IS NOT null
         AND F.hit.tag = "defence"
         AND F.target IS NOT null AND F.target IS NOT destroyed
         AND F.hit.gameObject != F.target.gameObject
END FUNCTION
```

**Expected behavior on C (P):**
```
FUNCTION expectedBehavior_LaneSight(Q, inSight)
  // fixed lane sight counts only enemies at or right of the defence's column
  RETURN inSight = (EXISTS x IN Q.occupied : x >= Q.myCell.x)
  // on C_A this is false, so SetTriggerAttack(false) / SetTriggerScared(false)
END FUNCTION

FUNCTION expectedBehavior_StaleTarget(F, targetAfter)
  // the cached target becomes the hit defence in the same frame
  RETURN targetAfter = F.hit.gameObject.GetComponent<DefenceBase>()
  // so DoDamageLazer(targetAfter) and the next EnemyAttackEvent damage it,
  // and the laser line ends at targetAfter.transform.position
END FUNCTION
```

C is defined by grid and ray state, not by when a defence was placed. In practice mid-run placement is how a run gets into C. There is one start-of-run path into C_B, covered under Preservation scope.

### Examples

- **Soldier behind an enemy (C_A, 1.1):** `defenceSoilder` is placed mid-run at (4,2). The only enemy in row 2 is at column 2, walking left. Expected: no enemy in sight, no attack animation, no projectiles. Actual: the soldier attacks and keeps firing projectiles to the right until the enemy leaves the row.
- **Shield behind enemies (C_A, 1.2):** `defenceShield` is placed mid-run at (6,1) with enemies at columns 1 and 3. Expected: not scared. Actual: scared.
- **Mixed row (not C):** a soldier at (4,2) with enemies at columns 2 and 7. Both old and fixed code report in sight, because the enemy at 7 is ahead.
- **Own cell (not C):** an enemy collider that overlaps the soldier's own cell (column 4) still counts as in sight.
- **Laser through a new defence (C_B, 1.3):** an `EnemyWizard` at column 8 is lasering defence A at column 3 (range 8). Defence B is placed mid-run at column 6. Expected: the laser is drawn to B and B loses 30/s. Actual: the laser is drawn through B to A, A loses 30/s, and B takes nothing.
- **Melee on a new defence (C_B, 1.4):** an `EnemyMonk` is hitting A. Defence B is placed on the monk's own cell, so the ray starts inside B's collider and reports B. Expected: the next hits (40 each) go to B. Actual: they go to A.
- **New defence behind the target (not C):** B is placed at a column lower than A. The ray still hits A first, so nothing changes.
- **Target destroyed (not C):** A finishes its death animation and is destroyed. Both old and fixed code switch to the next defence the ray hits.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- An OnSight or Static defence still reports in sight when any enemy in its row is at its column or higher, its own cell included. This holds for start-of-run and mid-run defences, and for enemies already on the grid when the defence was placed (3.1).
- An empty row still means no attack and no scared state (3.2).
- `defenceArcher` (OnSightMultiLane) still uses `GetEnemyIfSomeoneInSight` over every row and every column (3.3). `defencePenguin` (OnPoked) still starts only from `TakeDamage` (3.4).
- When the ray's first defence is the cached target, the enemy keeps damaging it, frame for frame as today (3.5).
- No hit still means the enemy moves and keeps its cache as today. A destroyed target is still replaced by the next hit defence (3.6).
- Thrower (`EnemClown`) and suicide-bomber (`EnemyPirate`) damage still comes from `Projectile` and the `OnDisable` blast (3.7). The new retarget rule can update their cache, but nothing reads it for damage. `DoDamageLazer` does nothing for non-Lazer types, and their clips never fire `EnemyMeeleAnimationEvent`.
- Placement, `myCell`, run-wide names, health set in `Start`, damage logging, snapshots, mid-run batch handling and the death sequence are in code this fix doesn't touch (3.8, 3.9, 3.10, 3.11).
- The old one-argument `isSomeoneInSight(int)` stays in place with the same behaviour. After the fix nothing calls it. It is kept to keep the diff minimal and because the architecture overview documents it.

**Scope:**
All inputs outside C should be completely unaffected by this fix. This includes:
- Lane-sight queries where the row is empty, or where any occupied column is at or above `myCell.x`. A defence at column 0 sees exactly what it saw before.
- Lane-sight queries from OnSightMultiLane and OnPoked defences, since those never call `isSomeoneInSight`.
- Enemy frames with no hit, with a non-defence hit, with a hit equal to the cached target, or with a null or destroyed cached target.
- Thrower and suicide-bomber frames, whose damage doesn't read the cache.

**Runs without mid-run placements (3.12):** For C_A, a living defence can't be passed at start of run, because enemies stop at the first defence on their ray. For C_B there is one start-of-run path into C. A penguin poked by a wizard slides right through it and kills it. While the wizard plays its death animation, `Movement` keeps raycasting and the laser can stay on. Its ray can then hit the next defence D on its left while the penguin, its cached target, is still alive and sliding away. The original code keeps damaging the penguin. The fixed code damages D. This is the same stale-target defect inside C, not a regression. Dying enemies attacking at all is pre-existing and out of scope. Every start-of-run input outside C behaves exactly as before.

## Hypothesized Root Cause

These come from reading the code, and the exploration tests confirm them:

1. **The row scan has no column input**: `isSomeoneInSight(int rowIndex)` loops over every `GridCell` in `grid[rowIndex]` and returns true on the first occupied one.
   - `EnemyDetectionAndSetAttack` and `EnemyDetectionAndSetScared` pass only `myCell.y`. `myCell.x` is known but unused.
   - Only these two calls use `isSomeoneInSight` (checked with a workspace search), so a new overload has no other callers to worry about.

2. **An implicit start-of-run invariant**: the row-wide scan was correct as long as enemies stop at the first living defence in their row. Mid-run placement can put a defence behind an enemy that already passed that column, which breaks the invariant.

3. **The target cache only refills when null**: in `Movement`, `target` is assigned only inside `if (target == null)`. It is cleared only when the ray hits a non-defence collider. A different defence on the ray doesn't count, so the only way to switch is for the old target to be destroyed.

4. **Both damage paths read the cache, not the hit**: `DoDamageLazer(target)` applies `attackDamage * Time.deltaTime` and ends the laser at `target.transform.position`. `EnemyAttackEvent` calls `DoDamage(target)`. Neither looks at `hit`.

5. **Comparing GameObjects is safe**: every defence prefab (`defenceSoilder`, `defenceShield`, `defencePenguin`, `defenceArcher`) has exactly one `Collider2D`. It sits on the root, with `DefenceBase`, tagged `defence` on layer 6 (checked in the prefab YAML). Children are untagged with no colliders. So `hit.collider.gameObject` is the defence's own GameObject, and no child collider can trigger a false retarget.

## Correctness Properties

Property 1: Bug Condition - Lane sight ignores enemies that have passed the defence

_For any_ row occupancy and defence column where isBugCondition_LaneSight holds (some occupied column, all lower than `myCell.x`), the fixed lane-sight check (`CustomGridSystem.IsSomeoneInSightFromColumn(row, myCell.x)`, reached through `isSomeoneInSight(myCell.y, myCell.x)`) SHALL return false. The OnSight defence therefore sets its attack trigger to false and the Static defence sets its scared trigger to false. More generally, for any occupancy and column, the result SHALL equal "some occupied column ≥ `myCell.x`".

**Validates: Requirements 2.1, 2.2**

Property 2: Bug Condition - Enemies retarget to the first defence on their ray

_For any_ enemy frame where isBugCondition_StaleTarget holds (the cached target exists and the ray's first defence is a different GameObject), the fixed retarget rule (`EnemyAI.ShouldRetarget(target, hit.collider.gameObject)`) SHALL return true. `Movement` then sets `target` to the hit defence in the same frame, so the laser damage, the laser endpoint and later melee hits all go to that defence.

**Validates: Requirements 2.3, 2.4**

Property 3: Preservation - Lane sight unchanged outside C_A

_For any_ row occupancy and defence column where isBugCondition_LaneSight does NOT hold (empty row, or some occupied column ≥ `myCell.x`), the fixed lane-sight check SHALL return the same value as the original row-wide `isSomeoneInSight(rowIndex)` scan. For `minColumn` ≤ 0 it SHALL equal the original scan for every occupancy. Multi-lane and OnPoked detection paths SHALL be unchanged.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.8, 3.12**

Property 4: Preservation - Targeting unchanged outside C_B

_For any_ cached target and hit where isBugCondition_StaleTarget does NOT hold (cached target null or destroyed, or hit GameObject equal to the cached target's GameObject), `EnemyAI.ShouldRetarget` SHALL return the same value as the original rule `target == null`. The cached target, and so every damage call, SHALL then match the original code frame for frame. Thrower and suicide-bomber damage SHALL be unchanged for every input.

**Validates: Requirements 3.5, 3.6, 3.7, 3.9, 3.12**

Requirements 3.10 and 3.11 live in code this fix doesn't touch: `PlacementSystem.PlaceDefencesMidRun`, `GameManager`'s reinforcement handling and `DefenceBase.CheckCurrentHealth`. They are covered by leaving those files unchanged and by the existing `MidRunDefencePlacementTests` suite still passing.

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `Scripts/CustomGridSystem.cs`

**Function**: new overload `isSomeoneInSight(int rowIndex, int minColumn)` and new static `IsSomeoneInSightFromColumn(List<GridCell> row, int minColumn)`, added right after the existing `isSomeoneInSight(int)`

**Specific Changes**:
1. **Column-aware overload**: same name as the existing method (follows the file's naming), with an extra `minColumn`. It delegates to the static helper with `grid[rowIndex]`, the same way `GetNearestCellNumbering` delegates to the static `NearestCellNumbering`.
2. **Pure row scan**: a `foreach` over the row. It skips cells whose `nodeNumbering.x` is below `minColumn`, and returns true on the first remaining `isSomeoneOnThisCell`. Using `nodeNumbering.x` rather than the list index matches `myCell`, which is set from `cell.nodeNumbering`. A negative `minColumn` scans the whole row, and one above the last column returns false.
3. **Existing method untouched**: `isSomeoneInSight(int)` and `GetEnemyIfSomeoneInSight` don't change.

```csharp
    // NEW: lane sight that skips enemies left of minColumn (already passed)
    public bool isSomeoneInSight(int rowIndex, int minColumn)
    {
        return IsSomeoneInSightFromColumn(grid[rowIndex], minColumn);
    }

    // NEW: pure row scan from minColumn rightwards, testable without a scene
    public static bool IsSomeoneInSightFromColumn(List<GridCell> row, int minColumn)
    {
        foreach (GridCell cell in row)
        {
            if (cell.nodeNumbering.x < minColumn)
            {
                continue;
            }
            if (cell.isSomeoneOnThisCell)
            {
                return true;
            }
        }
        return false;
    }
```

**File**: `Scripts/DefenceBase.cs`

**Function**: `EnemyDetectionAndSetAttack`, `EnemyDetectionAndSetScared`

**Specific Changes**:
4. **Pass the defence's column**: in both methods, change `grid.isSomeoneInSight(myCell.y)` to `grid.isSomeoneInSight(myCell.y, myCell.x)` with a trailing `// UPDATED: skip enemies that already passed me`. The if/else bodies, `EnemyDetectionAndSetAttackMultiLane` and `TakeDamage` don't change.

**File**: `Scripts/EnemyAI.cs`

**Function**: `Movement` (one condition) and new static `ShouldRetarget(DefenceBase cachedTarget, GameObject hitObject)`

**Specific Changes**:
5. **Retarget condition**: replace `if (target == null)` with `if (ShouldRetarget(target, hit.collider.gameObject))`, marked `// UPDATED: also retarget when a different defence blocks the ray`. The body (`target = hit.collider.gameObject.GetComponent<DefenceBase>();`), the `DoDamageLazer(target)` call, the `else { target = null; }` branch and the movement code don't change. `GetComponent` still runs only when the target changes, never every frame.
6. **Pure retarget rule**: true when the cached target is null or destroyed (Unity `== null`), or when its GameObject isn't the hit GameObject. The null check comes first, so `cachedTarget.gameObject` is never read on a destroyed object.

```csharp
    // NEW: retarget when nothing is cached (or it was destroyed) or the ray hit another defence
    public static bool ShouldRetarget(DefenceBase cachedTarget, GameObject hitObject)
    {
        if (cachedTarget == null)
        {
            return true;
        }
        if (cachedTarget.gameObject != hitObject)
        {
            return true;
        }
        return false;
    }
```

**File**: `Tests/Editor/MidRunDefenceDetectionTests.cs` (new)

7. **EditMode tests**: an NUnit class in the existing `Tests/Editor` folder, with no asmdef, so it compiles into `Assembly-CSharp-Editor` like `MidRunDefencePlacementTests`. It follows that file's style: an `Iterations` const, a seeded `System.Random` from `NewSeed()`, the seed in every assert message, explicit types, plain loops, braces on their own lines.

**Code rules applied**: no full-file rewrites, only the blocks above. New blocks are marked `// NEW:` and changed lines `// UPDATED:`. No new fields, so no `[SerializeField]`/`public` field decisions. The static helpers are `public` only so the tests can call them, the same as `NearestCellNumbering`. No ternaries, `var`, `out var`, lambdas, LINQ, chained assignments or single-line `if` bodies.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior. Both decisions become pure static helpers, so EditMode tests can cover them over many generated inputs without a scene. The physics and animation wiring around them is checked manually in Play Mode, because the project has no PlayMode test assembly.

Run the tests from the Unity Test Runner (EditMode), or with `Unity -batchmode -projectPath d:\projects\AutoPilot_GDG -runTests -testPlatform EditMode -testResults <file>`. `com.unity.test-framework` 1.5.1 is in `Packages/manifest.json` (checked). If Unity can't be run from the agent environment, say so and leave the run to the user.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: The helpers don't exist yet. So the exploration step first extracts the *current* decisions into them, with no behaviour change, and routes the existing call sites through them:
- `IsSomeoneInSightFromColumn` returns true on any occupied cell and ignores `minColumn`.
- `ShouldRetarget` returns `cachedTarget == null`.

The tests then run against the code the game actually executes. Fixtures are built in `[SetUp]` and removed with `UnityEngine.Object.DestroyImmediate` in `[TearDown]`:
- A row of 11 `GridCell` components (`new GameObject().AddComponent<GridCell>()`), with `nodeNumbering = (x, row)` and `isSomeoneOnThisCell` set directly.
- Defences built as GameObjects with `BoxCollider2D` added before `DefenceBase`. `DefenceBase` requires the abstract `Collider2D`, and `DefenceAttack` is auto-added. `Awake` doesn't run in EditMode, so no scene lookups happen.

**Test Cases**:
1. **Soldier behind an enemy**: occupied {2}, `minColumn` 4. Assert false (will fail on unfixed code: returns true)
2. **Shield behind enemies**: occupied {0, 1, 3}, `minColumn` 5. Assert false (will fail on unfixed code)
3. **Laser/melee new defence in between**: cached A alive, hit = B's GameObject. Assert `ShouldRetarget` is true (will fail on unfixed code: returns false)
4. **Passed-only rows, generated**: random non-empty occupancy all below a random `minColumn`. Assert false (will fail on unfixed code for every generated case)

**Expected Counterexamples**:
- `IsSomeoneInSightFromColumn(row, c)` returns true when every occupied column is below `c`
- `ShouldRetarget(A, B)` returns false for a live A and a different B
- Possible causes: a row scan with no column filter (root cause 1), and a null-only refill condition (root cause 3)

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL Q WHERE isBugCondition_LaneSight(Q) DO
  ASSERT IsSomeoneInSightFromColumn_fixed(rowOf(Q), Q.myCell.x) = false
END FOR

FOR ALL F WHERE isBugCondition_StaleTarget(F) DO
  ASSERT ShouldRetarget_fixed(F.target, F.hit.gameObject) = true
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL Q WHERE NOT isBugCondition_LaneSight(Q) DO
  ASSERT IsSomeoneInSightFromColumn_fixed(rowOf(Q), Q.myCell.x) = originalRowScan(rowOf(Q))
END FOR

FOR ALL F WHERE NOT isBugCondition_StaleTarget(F) DO
  ASSERT ShouldRetarget_fixed(F.target, F.hit.gameObject) = (F.target == null)
END FOR
```

`originalRowScan` is a test-side copy of the old `isSomeoneInSight(int)` loop (any occupied cell). This mirrors how `Property15_StartOfRunNamingPreserved` keeps a copy of the old naming algorithm.

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe on UNFIXED (extracted) code that the helpers match the original logic for non-bug inputs, then write property tests capturing that behavior.

**Test Cases**:
1. **Ahead or own-cell enemies**: on unfixed code, a row with any occupied column ≥ `c` reports true. After the fix it must still report true.
2. **Empty row**: on unfixed code an empty row reports false. After the fix it must still report false for any `minColumn`.
3. **Column 0 / negative `minColumn`**: after the fix the result equals the original row scan for every occupancy.
4. **Same target**: `ShouldRetarget(A, A's GameObject)` is false on unfixed code and must stay false.
5. **Null or destroyed target**: `ShouldRetarget(null, X)` is true, and so is `ShouldRetarget(destroyedA, X)` after `DestroyImmediate`. Both are true on unfixed code and must stay true.

### Unit Tests

- Lane sight examples: own cell counts; mixed row (behind + ahead) is true; `minColumn` above the last column is false; negative `minColumn` equals the full scan; empty row is false
- Retarget examples: different live defence → true; same defence → false; null → true; destroyed → true
- The existing `MidRunDefencePlacementTests` suite still passes unchanged (3.8, 3.10, 3.12)

### Property-Based Tests

- Property 1: generate a random 11-cell occupancy and `minColumn` in [-2, 12]. Assert `IsSomeoneInSightFromColumn` equals "exists occupied x ≥ minColumn", and is false whenever C_A holds
- Property 2: generate a pool of 2–6 defence GameObjects and pick a cached target and a hit that differ. Assert `ShouldRetarget` is true
- Property 3: for generated occupancies outside C_A, assert the result equals `originalRowScan`
- Property 4: generate cached-target states (null, destroyed, alive) and hits. Outside C_B, assert `ShouldRetarget` equals `cachedTarget == null`

### Integration Tests

Manual Play Mode checks. Use a backend run with reinforcements on, or drag a defence prefab under `PlacementSystem` in Play Mode and set `myCell` in the Inspector.
- **Soldier behind an enemy**: place `defenceSoilder` in a row behind a walking enemy. It stays idle, and it starts firing once a new enemy enters that row at its column or higher
- **Shield behind enemies**: same setup with `defenceShield`. It isn't scared until an enemy is at its column or higher
- **Wizard retarget**: while an `EnemyWizard` lasers A, place B between them. The laser line moves to B on the next frame, B's health bar drops, and A's stops dropping
- **Melee retarget**: while an `Enemy` or `EnemyMonk` hits A, place B on the enemy's cell. The next hits land on B, and the damage log shows the enemy damaging while B's health falls
- **Unchanged paths**: the archer still fires at enemies in any row, including ones past its column. The penguin still slides only when hit. Clown projectiles and the pirate blast still damage whatever they physically reach. When A is destroyed, the enemy switches to the next defence. A Quick_Test_Mode run with no mid-run placements looks the same as before
