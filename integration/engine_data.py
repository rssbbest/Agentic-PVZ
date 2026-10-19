"""Read IDs and balance values from the referenced Unity scene/prefabs."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'autopilot-game-engine_old'


def catalog():
    scene = (ENGINE / 'Assets/Scenes/Main.unity').read_text()
    output = {'source': str((ENGINE / 'Assets/Scenes/Main.unity').relative_to(ROOT)),
              'cellSize': 1.5, 'gridSize': 5, 'enemies': [], 'defences': []}
    names = {}
    for meta in (ENGINE / 'Assets/Prefabs').rglob('*.prefab.meta'):
        match = re.search(r'^guid: (\w+)', meta.read_text(), re.M)
        if match:
            names[match[1]] = meta.with_suffix('')
    for kind, field in [('enemies', 'enemyAIIndexMapping'), ('defences', 'defenceIndexMapping')]:
        block = re.search(rf'  {field}:\n((?:  - .*\n)+)', scene).group(1)
        for index, guid in enumerate(re.findall(r'guid: (\w+)', block)):
            path = names[guid]
            body = path.read_text()
            item = {'id': index, 'name': path.stem, 'prefab': str(path.relative_to(ROOT))}
            for prop in ('maxHealth', 'movementSpeed', 'attackRange', 'attackDamage',
                         'enemyType', 'attackType', 'attackTriggerType', 'speed',
                         'blastRadius', 'canRegeneratehealth', 'regenerateRatePerSec'):
                m = re.search(rf'^  {prop}: ([\d.-]+)', body, re.M)
                if m:
                    item[prop] = float(m.group(1))
            output[kind].append(item)
    return output
