# Bugfix Requirements Document

## Introduction

Defences placed mid-run (through a reinforcement batch) do detect enemies that are already on the grid. Enemy presence is tracked live by the grid cells, independent of when a defence was placed, and a mid-run defence has its lane, name and health set before it can see or be hit by anything. The check did find two mid-run-specific logic bugs. Neither can happen at start of run, because there every defence exists before the first enemy spawns.

Direction terms used below: enemies enter at the right-hand spawn column and walk left toward the losing line. Defences fire to the right. An enemy has "passed" a defence when it is in the defence's row at a lower column than the defence's cell. A defence is "between" an enemy and its target when it is the first defence the enemy's leftward attack ray hits, closer than the defence the enemy is already attacking.

1. Lane sight ignores direction. Lane-sight defences (OnSight, such as defenceSoilder, and Static, such as defenceShield) treat any enemy anywhere in their row as "in sight", including enemies that have already passed them. At start of run this can't happen while the defence is alive, because enemies stop at the first defence they reach. Mid-run, the backend can place a defence in a row where an enemy has already gone past that column. The defence then attacks (or acts scared) at an enemy it can never hit.
2. Enemies keep a stale target. A melee or laser enemy keeps damaging the defence it locked onto, even after a mid-run defence is placed between them. The new defence blocks the enemy but takes no damage from it, and the defence behind it keeps losing health. Thrower and suicide-bomber enemies are not affected, because their projectile or blast hits whatever defence it physically reaches.

Checked and ruled out (no change needed): enemies already on the grid being missed by a newly placed defence; a mid-run defence using the wrong lane on its first frames; a mid-run defence taking damage before its health is set; an off-by-one in the multi-lane row scan; currency deduction (no currency system is in use); per-unit logging, state-snapshot inclusion and animation-event wiring being set up only at start of run; and enemies failing to engage a mid-run defence. Two items are out of scope. Placement on occupied cells, out-of-grid cells or invalid defence ids stays the backend's responsibility under the mid-run-defence-placement spec. Repeated death logging while a defence is dying is a pre-existing issue that doesn't come from mid-run placement.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN an OnSight defence (for example defenceSoilder) is placed mid-run at column c, and every enemy in its row is at a column lower than c THEN the system reports an enemy in sight for that defence, so the defence plays its attack and keeps firing straight projectiles to the right, none of which can hit those enemies, until they leave the row

1.2 WHEN a Static defence (for example defenceShield) is placed mid-run at column c, and every enemy in its row is at a column lower than c THEN the system puts that defence into its scared state for enemies that have already passed it

1.3 WHEN a laser enemy (for example EnemyWizard) is attacking a living defence A, and a defence B is placed mid-run between the enemy and A THEN the system keeps applying the laser damage to A and draws the laser to A through B, and B takes no damage from that enemy while A is alive

1.4 WHEN a melee enemy (for example Enemy or EnemyMonk) is attacking a living defence A, and a defence B is placed mid-run between the enemy and A THEN the system keeps applying the melee hits to A, and B takes no damage from that enemy while A is alive

### Expected Behavior (Correct)

2.1 WHEN an OnSight defence is placed mid-run at column c, and every enemy in its row is at a column lower than c THEN the system SHALL report no enemy in sight for that defence, so the defence does not attack until an enemy is in its row at column c or higher

2.2 WHEN a Static defence is placed mid-run at column c, and every enemy in its row is at a column lower than c THEN the system SHALL keep that defence out of its scared state until an enemy is in its row at column c or higher

2.3 WHEN a laser enemy is attacking a living defence A, and a defence B is placed mid-run between the enemy and A THEN the system SHALL retarget the enemy to B from the first frame its attack ray hits B, applying the laser damage to B and drawing the laser to B

2.4 WHEN a melee enemy is attacking a living defence A, and a defence B is placed mid-run between the enemy and A THEN the system SHALL retarget the enemy to B from the first frame its attack ray hits B, applying the melee hits to B

### Unchanged Behavior (Regression Prevention)

3.1 WHEN an enemy is in an OnSight or Static defence's row at a column equal to or higher than the defence's column (including the defence's own cell), whether the defence was placed at start of run or mid-run, and including enemies that were already on the grid when it was placed THEN the system SHALL CONTINUE TO report the enemy in sight, so the OnSight defence attacks and the Static defence is scared

3.2 WHEN no enemy is in an OnSight or Static defence's row THEN the system SHALL CONTINUE TO report no enemy in sight, so the defence neither attacks nor is scared

3.3 WHEN an OnSightMultiLane defence (for example defenceArcher) is on the grid THEN the system SHALL CONTINUE TO pick and fire at an enemy in any row and any column, including enemies that have passed its column

3.4 WHEN an OnPoked defence (for example defencePenguin) is on the grid THEN the system SHALL CONTINUE TO start its attack only when it takes damage

3.5 WHEN the first defence on an enemy's attack ray is the defence the enemy is already attacking THEN the system SHALL CONTINUE TO apply that enemy's damage to the same defence, with no change at start of run

3.6 WHEN an enemy's attack ray hits no defence THEN the system SHALL CONTINUE TO move the enemy toward the losing line, and WHEN the defence an enemy is attacking is destroyed THEN the system SHALL CONTINUE TO switch the enemy to the next defence its attack ray hits

3.7 WHEN a thrower enemy (for example EnemClown) or a suicide-bomber enemy (for example EnemyPirate) attacks THEN the system SHALL CONTINUE TO apply damage through its projectile or blast, to whichever defence that projectile or blast physically reaches

3.8 WHEN a defence is placed mid-run THEN the system SHALL CONTINUE TO give it its placement cell as its lane, its run-wide unique name, its full health before it can take damage, and its normal handling in damage logging and state snapshots, as for a start-of-run defence

3.9 WHEN a defence placed mid-run is reached by an enemy's attack ray, projectile or blast THEN the system SHALL CONTINUE TO let that enemy engage and damage it as it would a start-of-run defence

3.10 WHEN a mid-run batch contains a card for an occupied cell, a cell outside the grid or an invalid defence id THEN the system SHALL CONTINUE TO place cards as sent and rely on the existing reinforcement exception handling, adding no per-card validation

3.11 WHEN a defence's health reaches 0 THEN the system SHALL CONTINUE TO play its death sequence and log its destruction exactly as it does today

3.12 WHEN a run has no mid-run placements (including Quick_Test_Mode) THEN the system SHALL CONTINUE TO show the same defence detection, enemy targeting and damage behaviour as before this fix
