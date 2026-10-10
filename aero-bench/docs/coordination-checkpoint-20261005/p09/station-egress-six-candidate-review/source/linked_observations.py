"""Prefix-only, original-tick observations for adopted linked scenarios.

This is not a migration of legacy sustain_ticks. The five-sample guard is an
explicit L5-1 profile. Missing weather/geometry is UNKNOWN, never a numeric zero.
"""
from __future__ import annotations

import math
from .linked_contract import STEP_NS


def available_weather(rows, observation_ns):
    """One original tick acquisition latency; evidence must precede decision."""
    candidates = [r for r in rows if (r['tick'] + 1) * STEP_NS < observation_ns]
    if not candidates:
        return None
    row = candidates[-1]
    return {'sample_ns': row['tick'] * STEP_NS, 'available_ns': (row['tick'] + 1) * STEP_NS,
            'weather': {k: v for k, v in row.items() if k != 'tick'}}


def consecutive_rain(rows, observation_ns, owner, *, threshold, count):
    known = [r for r in rows if (r['tick'] + 1) * STEP_NS < observation_ns]
    streak = 0
    previous = None
    for row in reversed(known):
        if previous is not None and row['tick'] != previous - 1:
            break
        rain = row.get('rain')
        if rain is None or not math.isfinite(rain) or rain < threshold:
            break
        streak += 1
        previous = row['tick']
    last = available_weather(rows, observation_ns)
    value = None if last is None else last['weather'].get('rain')
    return {'schema_version': 'p09.l5-1.rain-consecutive-original-ticks/v1',
            'owner': owner, 'observation_ns': observation_ns,
            'sample_ns': None if last is None else last['sample_ns'],
            'available_ns': None if last is None else last['available_ns'],
            'rain': value, 'threshold': threshold, 'sample_period_ns': STEP_NS,
            'required_consecutive_samples': count, 'consecutive_samples': streak,
            'predicate': None if value is None else streak >= count,
            'availability': 'UNKNOWN' if value is None else 'KNOWN',
            'source': 'authored weather state sampled locally; not a calibrated rain sensor'}


def proximity_evidence(current_rows, owner_a, owner_b, *, metric, threshold_m,
                       vertical_threshold_m=None):
    indexed = {r['entity_id']: r for r in current_rows}
    if owner_a not in indexed or owner_b not in indexed:
        return {'predicate': None, 'availability': 'UNKNOWN', 'reason': 'missing exact owner pose'}
    a, b = indexed[owner_a]['pos_enu'], indexed[owner_b]['pos_enu']
    delta = [x - y for x, y in zip(a, b)]
    if metric == '3d':
        distance = math.sqrt(sum(x * x for x in delta))
        predicate = distance <= threshold_m
    elif metric == 'xy_plus_z':
        if vertical_threshold_m is None:
            raise ValueError('xy_plus_z requires its authored vertical threshold')
        distance = math.hypot(*delta[:2])
        predicate = distance <= threshold_m and abs(delta[2]) <= vertical_threshold_m
    else:
        raise ValueError('undeclared proximity metric')
    return {'owner_a': owner_a, 'owner_b': owner_b, 'metric': metric,
            'distance_m': distance, 'vertical_distance_m': abs(delta[2]),
            'threshold_m': threshold_m, 'vertical_threshold_m': vertical_threshold_m,
            'predicate': predicate, 'availability': 'KNOWN'}
