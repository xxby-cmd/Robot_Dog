"""Finite, user-selected walking speeds without application range limits."""
import json
import math
from pathlib import Path

DEFAULTS = {'forward': 0.15, 'reverse': 0.12, 'turn': 0.25}
CONFIG = Path.home() / '.config' / 'robotdog' / 'teleop.json'


def validate(values):
    result = {}
    for key in DEFAULTS:
        value = values[key]
        if isinstance(value, bool):
            raise ValueError('速度必须是数字')
        value = float(value)
        if not math.isfinite(value):
            raise ValueError('速度必须是有限数字')
        result[key] = value
    return result


def load(path=CONFIG):
    path = Path(path)
    if not path.exists():
        return dict(DEFAULTS)
    return validate(json.loads(path.read_text(encoding='utf-8')))


def save(values, path=CONFIG):
    values = validate(values)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name(path.name + '.new')
    stage.write_text(json.dumps(values, indent=2), encoding='utf-8')
    stage.replace(path)
    return values


def command(action, values):
    if action == 'forward':
        return values['forward'], 0.0
    if action == 'reverse':
        return -values['reverse'], 0.0
    if action == 'left':
        return 0.0, values['turn']
    if action == 'right':
        return 0.0, -values['turn']
    raise ValueError('Unknown walking action')
