"""Shared IO, time, and identity helpers for the L6 v2 ARM workflow."""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[3]
for _p in (ROOT, ROOT / 'Dataset/tools', ROOT / 'Plugins/SumoImporter/Scripts'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

TICK_HZ = 10
FORMAL_STEP_TICKS = 5
RUNTIME_FAMILIES = ('control_state', 'communication_state', 'navigation_state',
                    'security_state', 'mission_state')
SEMANTIC_FIELDS = ('pos_enu', 'vel_mps', *RUNTIME_FAMILIES)
LABEL_FIELDS = ('state', 'activity_type', 'posture', 'yaw_deg')
COMPARE_FIELDS = (*SEMANTIC_FIELDS, *LABEL_FIELDS)

SCENARIOS = ROOT / 'Dataset/scenarios/L6_digital_layer/failure'
CAPTURE = ROOT / 'aw_data/render_ready_episodes_capture_filtered'
SEMANTIC = ROOT / 'aw_data/objective_semantic_truth'


def load(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def rows(path: Path | str) -> list[dict]:
    with Path(path).open(encoding='utf-8-sig') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write(path: Path | str, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                      encoding='utf-8')


def write_rows(path: Path | str, values: Iterable[Mapping[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('w', encoding='utf-8') as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(',', ':'),
                                    allow_nan=False) + '\n')


def digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(',', ':'))
    return 'sha256:' + hashlib.sha256(payload.encode()).hexdigest()


def file_digest(path: Path | str) -> str:
    handle = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            handle.update(chunk)
    return 'sha256:' + handle.hexdigest()


def grid_up(tick: float) -> int:
    """Round up to the formal 5-tick grid."""
    return int(math.ceil(max(0.0, float(tick)) / FORMAL_STEP_TICKS) * FORMAL_STEP_TICKS)


def project_path(relative: str) -> Path:
    path = (ROOT / relative).resolve()
    path.relative_to(ROOT)
    return path
