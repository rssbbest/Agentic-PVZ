"""Portable lane combat. Uses Unity prefab stats, not Unity physics/animation.

Fixed 50 ms steps; ranged attacks every second, instant hit; deterministic
seed; enemies spawn in shuffled order on five lanes. These explicit rules
make this a playable development simulator, not a competition parity claim.
"""
import random
from .engine_data import catalog

CATALOG = catalog()


class Battle:
    def __init__(self, enemies, placement, seed=42):
        self.rng = random.Random(seed)
        self.queue = [e['enemyId'] for e in enemies for _ in range(e['count'])]
        self.rng.shuffle(self.queue)
        self.total = len(self.queue)
        self.enemies = []
        self.defences = []
        for i, p in enumerate(placement):
            stats = CATALOG['defences'][p['defenceId']]
            self.defences.append(dict(uid=i, kind=p['defenceId'],
                x=p['defenceGridPosition']['x'] * 1.5,
                y=p['defenceGridPosition']['y'], hp=stats['maxHealth'],
                maxHp=stats['maxHealth'], cooldown=0., moving=False, hit=[]))
        self.time = 0.
        self.next_spawn = 0.
        self.spawned = 0
        self.killed = 0
        self.escaped = 0
        self.done = False

    def step(self, dt=.05):
        if self.done:
            return
        self.time += dt
        if self.queue and self.time >= self.next_spawn:
            kind = self.queue.pop(0)
            stats = CATALOG['enemies'][kind]
            self.enemies.append(dict(uid=self.spawned, kind=kind, x=7.5,
                y=self.rng.randrange(5), hp=stats['maxHealth'], maxHp=stats['maxHealth'], cooldown=0.))
            self.spawned += 1
            self.next_spawn = self.time + max(.3, 3 - .25 * self.spawned)
        for d in self.defences:
            if d['hp'] <= 0:
                continue
            stats = CATALOG['defences'][d['kind']]
            attack = int(stats['attackType'])
            d['cooldown'] -= dt
            targets = [e for e in self.enemies if e['hp'] > 0 and e['x'] >= d['x'] - .2
                       and (attack == 3 or e['y'] == d['y'])]
            if attack == 1:
                if any(abs(e['x'] - d['x']) < .65 for e in targets):
                    d['moving'] = True
                if d['moving']:
                    old = d['x']
                    d['x'] += stats['speed'] * dt
                    for e in targets:
                        if old - .65 <= e['x'] <= d['x'] + .65 and e['uid'] not in d['hit']:
                            e['hp'] -= stats['attackDamage']
                            d['hit'].append(e['uid'])
                    if d['x'] > 9:
                        d['hp'] = 0
            elif attack in (2, 3) and targets and d['cooldown'] <= 0:
                target = min(targets, key=lambda e: e['x'])
                target['hp'] -= stats['attackDamage']
                d['cooldown'] = 1.
        for e in self.enemies:
            if e['hp'] <= 0:
                self.killed += 1
                stats = CATALOG['enemies'][e['kind']]
                if int(stats['enemyType']) == 3:
                    for d in self.defences:
                        if ((d['x'] - e['x'])**2 + ((d['y'] - e['y'])*1.5)**2)**.5 <= stats['blastRadius']:
                            d['hp'] -= stats['attackDamage']
                continue
            stats = CATALOG['enemies'][e['kind']]
            e['cooldown'] -= dt
            targets = [d for d in self.defences if d['hp'] > 0 and d['y'] == e['y']
                       and 0 <= e['x'] - d['x'] <= stats['attackRange'] + .4]
            if targets:
                target = max(targets, key=lambda d: d['x'])
                if int(stats['enemyType']) == 2:
                    target['hp'] -= stats['attackDamage'] * dt
                elif e['cooldown'] <= 0:
                    target['hp'] -= stats['attackDamage']
                    e['cooldown'] = 1.
            else:
                e['x'] -= stats['movementSpeed'] * dt
            if stats.get('canRegeneratehealth'):
                e['hp'] = min(e['maxHp'], e['hp'] + stats['regenerateRatePerSec'] * dt)
            if e['x'] < -.75:
                self.escaped += 1
                e['hp'] = -999999  # removed below, not counted as killed next step
        self.enemies = [e for e in self.enemies if e['hp'] > 0]
        if not self.queue and not self.enemies:
            self.done = True
        if self.time >= 180:
            self.escaped += len(self.enemies) + len(self.queue)
            self.enemies.clear()
            self.queue.clear()
            self.done = True

    def snapshot(self):
        return dict(time=round(self.time, 2), total=self.total, spawned=self.spawned,
                    killed=self.killed, escaped=self.escaped, done=self.done,
                    enemies=self.enemies, defences=self.defences)

    def result(self):
        return dict(engine='portable', totalEnemies=self.total, killed=self.killed,
                    escaped=self.escaped, defencesPlaced=len(self.defences),
                    defencesAlive=sum(d['hp'] > 0 for d in self.defences),
                    durationSeconds=round(self.time, 2), won=self.escaped == 0)
