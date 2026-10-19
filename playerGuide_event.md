# AutoPilot Player Guide

## How a run works

1. Start the SDK and log in with your team ID and password. Pick a mode from the menu (PLAY, PUBLISH PUBLIC or PUBLISH PRIVATE). The SDK chooses which test case is played.
2. Open the game page, type your team ID (at least 4 characters) and press ▶. The game loads the level: your starting defences (usually none), the enemy wave and your starting coins.
3. As soon as the first enemy appears, the game freezes and your agent makes its **opening** decision.
4. Defences fight on their own. You can't move or command units during a run.
5. At the interval configured for the level (`reinforcementIntervalSeconds`), the game freezes again and your agent can add more defences. On a level with reinforcements turned off, the opening is the only decision.
6. The run ends in a win or a loss. The end screen shows for 5 seconds, then the result is sent and the game returns to the menu. The SDK window shows your score.

## The grid

```
 y
 5 ║ .  .  .  .  .  .  .  .  .  .  .  ◄
 4 ║ .  .  .  .  .  .  .  .  .  .  .  ◄
 3 ║ .  .  .  .  .  .  .  .  .  .  .  ◄  enemies enter at x = 10
 2 ║ .  .  .  .  .  .  .  .  .  .  .  ◄  and walk left
 1 ║ .  .  .  .  .  .  .  .  .  .  .  ◄
 0 ║ .  .  .  .  .  .  .  .  .  .  .  ◄
   ╚═══════════════════════════════════
 x   0  1  2  3  4  5  6  7  8  9  10
   ▲
   lose line: an enemy crossing here ends the run
```

- 11 columns (x = 0 to 10) and 6 rows (y = 0 to 5). Each tile shows its (x,y) label in game.
- x counts columns from your base (left) toward the enemy side (right). y counts rows from the bottom.
- Enemies enter at column 10. Column 0 is your last line.
- Each row is a separate lane. Enemies never change rows or turn around.
- "In front of" a unit means a higher column (toward the enemies). "Behind" means a lower column (toward your base).
- One tile is 1.5 units: tile (x,y) is 1.5x units right of and 1.5y units above tile (0,0). Ranges and speeds below are in units unless they say tiles.

## Placing defences

Your agent's `get_reinforcements(state)` returns a list of cards. Each card names a defence and a tile:

```python
return [
    {"defenceId": 3, "defenceGridPosition": {"x": 1, "y": 0}},
    {"defenceId": 1, "defenceGridPosition": {"x": 0, "y": 0}},
]
```

Return `[]` to place nothing.

The referee checks every card in the order you return them, and drops a card when:

- the tile is off the board (`x` outside 0 to 10, or `y` outside 0 to 5)
- the `defenceId` isn't 0 to 3
- the tile already has a living defence
- the same tile appears twice in one answer (the second card is dropped)
- you can't afford it with the coins left (a cheaper card later in the list can still be placed)

A dropped card never stops the cards after it. Accepted cards appear before the game resumes, and the SDK window shows what was refused and why.

- Defences never move (except a triggered Penguin) and stay until destroyed. A destroyed defence frees its tile.
- Placed defences are never removed or replaced.

## Coins

- Each test case sets your starting coins (1,300 to 3,900 on the public test cases).
- Every enemy you defeat adds 50 coins.
- Each defence costs coins (see the Defences table, or `state["unitCosts"]`).
- Your balance is shown on screen and updates after each decision. Your agent sees the same number as `state["budget"]["remaining"]`.

## Enemy waves

- Enemies spawn one at a time at column 10. A spawn flash appears on the tile 0.3 s before each one.
- Each test case sets the spawn timing: the gap before the next spawn, how much the gap shrinks after each spawn, and a minimum gap. Later enemies arrive faster.
- A level can limit an enemy type to certain rows (`allowedRows`) and can fix the order enemies spawn in. Your agent gets both at the opening (see below). Public games randomize rows and spawn order where the level does not specify them; hidden cases use their fixed seed.

## Combat rules

- Units only fight in their own row. Exceptions: Archers shoot into any row, and the Pirate's blast hits nearby tiles, including the rows above and below.
- An enemy stops at the first defence it meets in its row (once it's in range) and attacks it until it's destroyed, then walks on.
- Enemies don't block each other, so several can attack one defence at once.
- Enemy projectiles and beams hit the first defence in their path. Your shots and arrows pass through your own defences and only hit enemies.
- Only Archers can hit an enemy that has got behind a defence.
- Attack rates come from attack animations, so DPS values are approximate.

## Winning and losing

- **Win:** every enemy in the wave has spawned and been defeated.
- **Lose:** any single enemy crosses the left edge of column 0, even if you still have defences.
- Losing all your defences doesn't end the run by itself, but nothing will stop the next enemy. There's no time limit.

## When your agent is called

- **Opening (`state["phase"] == "start"`):** the moment the first enemy appears, before it moves. This call has the whole wave and the spawn settings. It happens on every level, even one with reinforcements turned off.
- **Pauses (`state["phase"] == "pause"`):** at the interval configured for the level (`reinforcementIntervalSeconds`), counted from the end of the previous decision. A level with reinforcements turned off makes no pauses after the opening.
- The battle stays frozen while your agent decides, so thinking time costs nothing in game.
- **You have 15 seconds per call** (`state["timeBudgetSeconds"]`). If your agent is slower, crashes or returns `[]`, nothing is placed and the game goes on.
- The screen shows the outcome: "Agent chose to place defences", "Agent chose to skip", or "No response from agent, resuming". It holds for a moment (set by the level) before the game resumes.
- The interval counts game time. The game runs at a fixed speed during the competition; there is no fast-forward control. If a pause becomes due during a taunt, the game cuts the taunt short and asks the agent.
- Edits to `agent.py` apply from the next call. A PUBLISH run is the exception: it keeps the copy of `agent.py` it started with.

## What your agent receives

`state` is a dictionary. Every call has these keys:

| Key | Contents |
|---|---|
| `phase` | `"start"` for the opening, `"pause"` after that |
| `timeBudgetSeconds` | seconds your agent has to answer: 15 |
| `teamID`, `sessionID` | the team ID typed in the game, and this game's ID |
| `elapsedSeconds` | game seconds since the level started |
| `counts` | `activeDefenceCount`, `activeEnemyCount`, `enemiesQueued` (not spawned yet), `totalEnemiesInWave` |
| `defences` | every defence on the field: `id`, `type`, `health`, `maxHealth`, `position` {`x`, `y`} |
| `enemies` | every enemy on the field: `id`, `type`, `health`, `maxHealth`, `position` {`x`, `y`}, `isAttacking`, `isDead` |
| `budget` | `remaining` (coins you can spend now), `spent`, `unlocked` (starting coins + 50 per kill so far), `total` (the most this level can pay out) |
| `unitCosts` | cost of each `defenceId`: `{"0": 250, "1": 200, "2": 300, "3": 150}` |
| `candidatePlacements` | always `[]`; ignore it |

The opening call also has:

| Key | Contents |
|---|---|
| `upcomingEnemies` | the whole wave, including the enemy already on the field: `enemyId`, `count`, `allowedRows` |
| `spawnSettings` | `interArrivalTime` (first gap, s), `timeDecreaseRate` (s less after each spawn), `minInterArrivalTime` (smallest gap, s), and `spawnSequence` (the enemyIds in spawn order) when the level fixes the order |
| `gameSettings` | `reinforcementsEnabled`, `reinforcementIntervalSeconds` (set by the level), `reinforcementHoldSeconds`, `sendScreenshotToo` (always false) |

A pause looks like this:

```json
{
  "phase": "pause",
  "timeBudgetSeconds": 15,
  "teamID": "0042",
  "sessionID": "c3f9a1b27d4e8c05",
  "elapsedSeconds": 12.4,
  "counts": {"activeDefenceCount": 2, "activeEnemyCount": 2, "enemiesQueued": 7, "totalEnemiesInWave": 12},
  "defences": [
    {"id": "defenceShield_0", "type": "defenceShield", "health": 640, "maxHealth": 700, "position": {"x": 2, "y": 3}},
    {"id": "defenceSolider_1", "type": "defenceSolider", "health": 150, "maxHealth": 150, "position": {"x": 1, "y": 3}}
  ],
  "enemies": [
    {"id": "EnemyMonk_0", "type": "EnemyMonk", "health": 212, "maxHealth": 280, "position": {"x": 3, "y": 3}, "isAttacking": true, "isDead": false},
    {"id": "EnemClown_1", "type": "EnemClown", "health": 200, "maxHealth": 200, "position": {"x": 9, "y": 0}, "isAttacking": false, "isDead": false}
  ],
  "budget": {"remaining": 1300.0, "spent": 350.0, "unlocked": 1650.0, "total": 2100.0},
  "unitCosts": {"0": 250, "1": 200, "2": 300, "3": 150},
  "candidatePlacements": []
}
```

- An enemy's `position` is the nearest tile. A defence's `position` is the tile it was placed on.
- `isAttacking`: the enemy has stopped at a defence and is fighting it.
- `isDead`: the enemy is already defeated and is playing its death animation.
- A defence at 0 health or less is already destroyed and about to disappear.
- `type` names come from the game's unit files, spelling included (`defenceSolider`, `EnemClown`). They're listed in the unit tables below.
- Unit stats (damage, speed, range) aren't in `state`: use the tables below.
- The LLM needs no key or URL in your code: `LLMClient()` gets your team's access from your SDK login.

## Screen and controls

- **Game speed:** the game runs at a fixed speed during the competition; there is no fast-forward control.
- **Coins:** your balance is shown on screen and updates after each decision.
- **Taunts:** the game slows to quarter speed while a character insults you. It's a warning, not a pause, and each kind plays at most once per run:
  - one when an enemy is about to break through (it reaches column 1, or it has a clear path and is about 4 s from your base)
  - one when you're losing defences faster than you're defeating the wave

## Results and score

When the run ends, the game reports win or loss, run time, enemies defeated, defences lost, and total damage dealt by each side. The server scores it and the SDK window shows the score.

- **A won game scores out of 100:** win 25 · kills up to 25 · defences still standing up to 25 · speed up to 10 · coins left up to 15.
- **A lost game scores only its kills** (up to 25).

## Defences

| Defence | `defenceId` | Snapshot `type` | Cost | Health | Damage | Attack rate | DPS | Reach |
|---|---|---|---|---|---|---|---|---|
| Penguin | 0 | `defencePenguin` | 250 | 200 | 500 per enemy | Once | Burst | Its row, in front |
| Soldier | 1 | `defenceSolider` | 200 | 150 | 10 per shot | Every 0.25 s | ~40 | Its row, in front |
| Archer | 2 | `defenceArcher` | 300 | 100 | 10 per arrow | Every 0.38 s | ~26 | Any row |
| Shield | 3 | `defenceShield` | 150 | 700 | 0 | — | 0 | Blocks its row |

- **Penguin:** does nothing until it takes any damage, even a hit that would destroy it. Then it slides forward along its row at 10 units/s (~6.7 tiles/s), deals 500 to every enemy it touches, and leaves the field. One use only.
- **Soldier:** fires while an enemy is in its row, on its own tile or in front of it. It ignores enemies behind it. Shots travel forward at 15 units/s and hit the first enemy in their path.
- **Archer:** fires arcing arrows that track their target. It picks the lowest row with an enemy (row 0 first), then the enemy closest to your base in that row. All Archers share this rule, so they focus one target. Enemies only attack their own row, so an Archer in a quiet row is hard to reach.
- **Shield:** deals no damage. Enemies stop to attack it, and enemy projectiles and beams hit it first, so a Shield in front protects the units behind it.

## Enemies

| Enemy | `enemyId` | Snapshot `type` | Health | Speed (units/s) | Cross time* | Range | Damage | Attack rate | DPS |
|---|---|---|---|---|---|---|---|---|---|
| Clown | 0 | `EnemClown` | 200 (+15/s regen) | 2 | ~7.5 s | 5 units (~3.3 tiles) | 30 per throw | Every 1.77 s | ~17 |
| Monk | 1 | `EnemyMonk` | 280 | 3.5 | ~4.3 s | Melee (0.75 units) | 40 per strike | Every 1.1 s | ~36 |
| Wizard | 2 | `EnemyWizard` | 500 (+15/s regen) | 3 | ~5 s | 8 units (~5.3 tiles) | 50 per second | Continuous, after ~1.75 s wind-up | 50 |
| Pirate | 3 | `EnemyPirate` | 30 | 8 | ~1.9 s | Melee (0.5 units) | 200 blast, 2-unit radius | Once, on death | — |

\*Column 10 to column 0 without stopping.

- **Clown:** stops ~3 tiles from the first defence in its row and throws projectiles (7 units/s) straight along the row. It regenerates when hurt, so slow damage may never finish it.
- **Monk:** runs down its row and strikes the first defence it reaches.
- **Wizard:** stops ~5 tiles from the first defence in its row, winds up, then beams it until it's destroyed. If the next defence is already in range, the beam switches to it with no new wind-up. It regenerates like the Clown.
- **Pirate:** explodes as soon as it reaches the first defence in its row, dealing 200 to every defence within 2 units (~1.3 tiles). Killing it early still sets off the blast where it falls, which hits nothing if it's away from your defences.
