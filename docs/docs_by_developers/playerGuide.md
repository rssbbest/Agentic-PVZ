# Unit Reference: Defences and Enemies

## Battlefield basics

- The grid has 11 columns (0 to 10) and 6 rows (0 to 5). One tile is 1.5 units.
- Enemies enter at column 10 and walk left along their row. If any enemy gets past the left edge, you lose. Defeat every enemy to win.
- Units only fight inside their own row. There are two exceptions: Archers can shoot into any row, and the Pirate's blast reaches nearby tiles.
- Each enemy stops at the first defence in its row once that defence is within its range.
- Enemies don't block each other, so several can attack the same defence at once.
- Your soldiers' shots and archers' arrows pass through your own defences.
- The total cost of your placed defences must stay within the level budget.

Attack rates and damage per second (DPS) below are approximate. They come from each unit's attack animation.

### Placement IDs

| Defence | `defenceId` |  | Enemy | `enemyId` |
|---|---|---|---|---|
| Penguin | 0 |  | Clown | 0 |
| Soldier | 1 |  | Monk | 1 |
| Archer | 2 |  | Wizard | 2 |
| Shield | 3 |  | Pirate | 3 |

```json
{"defencePlacementData":[
  {"defenceId":3,"defenceGridPosition":{"x":1,"y":0}},
  {"defenceId":1,"defenceGridPosition":{"x":0,"y":0}}
]}
```

---

## Defences at a glance

| Defence | Cost | Health | Damage | Attack rate | DPS | Reach |
|---|---|---|---|---|---|---|
| Penguin | 250 | 200 | 500 per enemy | Once | Burst | Its own row |
| Soldier | 200 | 150 | 15 per shot | Every 0.42 s | ~36 | Its whole row |
| Archer | 300 | 100 | 10 per arrow | Every 0.38 s | ~26 | Any row |
| Shield | 150 | 700 | 0 | — | 0 | Blocks its row |

### Penguin

A one-shot emergency weapon that waits quietly until something hits it.

Power: Belly Slide
- The Penguin does nothing until it takes damage from any source.
- When it's hit, it slides right along its entire row at 10 units/s (about 6.7 tiles/s).
- It deals 500 damage to every enemy it touches, then leaves the field.
- Any hit triggers the slide, even one that would destroy it.
- It can only be used once.

| Stat | Value |
|---|---|
| Cost | 250 |
| Health | 200 |
| Damage | 500 to each enemy touched |
| Slide speed | 10 units/s |
| Trigger | Taking any damage |

### Soldier

A cheap, reliable gunner that holds a single row.

Power: Row Fire
- The Soldier starts firing as soon as an enemy appears anywhere in its row, so its range covers the whole row.
- Shots fly straight down the row at 15 units/s and hit the first enemy in their path.

| Stat | Value |
|---|---|
| Cost | 200 |
| Health | 150 |
| Damage | 15 per shot |
| Attack rate | About 2.4 shots/s (every 0.42 s) |
| DPS | About 36 |
| Range | Entire row |

### Archer

A fragile long-range support unit that can hit any row on the field.

Power: Volley
- The Archer fires arcing, homing arrows at enemies in any row.
- Targeting: it always picks an enemy in the lowest-numbered row first (row 0 before row 1, and so on). Within that row, it picks the enemy that has advanced furthest.
- Every Archer uses the same rule, so multiple Archers focus on the same target.
- Enemies only attack their own row, so an Archer placed in a quiet row is hard to reach.

| Stat | Value |
|---|---|
| Cost | 300 |
| Health | 100 |
| Damage | 10 per arrow |
| Attack rate | About 2.6 arrows/s (every 0.38 s) |
| DPS | About 26 |
| Range | Any row |

### Shield

A cheap, sturdy wall. It deals no damage.

Power: Hold the Line
- Enemies in its row stop at the Shield and attack it.
- Enemy projectiles and lasers hit the first defence in their row. A Shield placed in front (a higher column) protects the units behind it.

| Stat | Value |
|---|---|
| Cost | 150 |
| Health | 700 |
| Damage | 0 |

---

## Enemies at a glance

| Enemy | Health | Speed | Time to cross* | Range | Damage | Attack rate | DPS |
|---|---|---|---|---|---|---|---|
| Clown | 200 (+15/s regen) | 2 units/s | ~7.5 s | 5 units (~3.3 tiles) | 30 per throw | Every 1.77 s | ~17 |
| Monk | 280 | 7 units/s | ~2.1 s | Melee | 40 per strike | Every 1.1 s | ~36 |
| Wizard | 500 | 3 units/s | ~5 s | 8 units (~5.3 tiles) | 30 per second | Continuous | 30 |
| Pirate | 30 | 8 units/s | ~1.9 s | Melee | 200 blast | When it dies | — |

\*Time to walk from column 10 to column 0 without stopping.

### Clown

A slow, mid-range thrower that heals itself.

Powers
- Juggler's Throw: stops about 3 tiles away from the first defence in its row and throws projectiles straight along the row. Each projectile hits the first defence in its way.
- Regeneration: heals 15 health per second whenever it's below full health. Slow damage may never finish it off.

| Stat | Value |
|---|---|
| Health | 200 |
| Regeneration | 15 per second |
| Speed | 2 units/s (about 1.3 tiles/s) |
| Range | 5 units (about 3.3 tiles) |
| Damage | 30 per throw |
| Attack rate | Every 1.77 s (about 17 DPS) |
| Projectile speed | 7 units/s |

### Monk

A fast, tough melee bruiser.

Power: Flurry
- The Monk rushes down its row, stops at the first defence it reaches, and strikes it repeatedly.

| Stat | Value |
|---|---|
| Health | 280 |
| Speed | 7 units/s (about 4.7 tiles/s) |
| Range | Melee (0.75 units) |
| Damage | 40 per strike |
| Attack rate | Every 1.1 s (about 36 DPS) |

### Wizard

A long-range caster with a lot of health.

Power: Arcane Beam
- The Wizard stops about 5 tiles away from the first defence in its row.
- It charges for about 1.75 s, then fires a continuous laser at that defence.
- The beam keeps going until the target is destroyed. The Wizard then moves on to the next defence.

| Stat | Value |
|---|---|
| Health | 500 |
| Speed | 3 units/s (2 tiles/s) |
| Range | 8 units (about 5.3 tiles) |
| Damage | 30 per second |
| Wind-up | About 1.75 s before the beam starts |

### Pirate

A very fast, fragile bomber.

Power: Powder Keg
- The Pirate charges at the first defence in its row.
- When it dies, it explodes and deals 200 damage to every defence within 2 units (about 1.3 tiles) of where it fell.
- If it dies far from your defences, the blast hits nothing.

| Stat | Value |
|---|---|
| Health | 30 |
| Speed | 8 units/s (about 5.3 tiles/s) |
| Range | Melee (0.5 units) |
| Blast damage | 200 to each defence in range |
| Blast radius | 2 units (about 1.3 tiles) |
