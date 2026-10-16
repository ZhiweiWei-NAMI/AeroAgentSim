"""Seeded authored rescue episodes; no flood or perception model is implied."""
from __future__ import annotations

import random


# The shipped Huangpu city manifest covers ENU x/y [-540, 540] m.
CITY_BOUNDS = (-540.0, -540.0, 540.0, 540.0)
ROLES = ("inspect", "relay", "deliver")


def vary_fleet(cfg: dict, seed: int) -> None:
    """Materialize once, before entities exist, using only the pinned run seed.

    Horizontal starts move at most 150 m per axis. The authored flight layer
    (130–180 m) stays above the shipped city's 120 m maximum roof; this is not collision
    prediction. The protected task itself and its ownership are never changed.
    """
    mode = cfg.get("fleet_variation", "authored")
    if mode == "authored":
        return
    if mode != "seeded":
        raise ValueError("rescue.fleet_variation: expected authored or seeded")
    rng = random.Random(seed)
    fleet = cfg["vehicles"]
    if not fleet:
        raise ValueError("rescue seeded fleet needs vehicles")
    for identity in sorted(fleet):
        profile = fleet[identity]
        x, y, z = profile["position"]
        profile["position"] = [round(rng.uniform(max(CITY_BOUNDS[0], x - 150.0), min(CITY_BOUNDS[2], x + 150.0)), 3),
                               round(rng.uniform(max(CITY_BOUNDS[1], y - 150.0), min(CITY_BOUNDS[3], y + 150.0)), 3),
                               round(rng.uniform(max(130.0, z - 150.0), min(180.0, z + 150.0)), 3)]
        capabilities = [role for role in ROLES if rng.random() < 0.5]
        profile["capabilities"] = capabilities or [rng.choice(ROLES)]
        profile["soc"] = round(rng.uniform(0.3, 1.0), 4)
        profile["payload_kg"] = round(rng.uniform(0.0, 3.0), 3)
    # Guarantee coverage among uncommitted aircraft, rather than hiding a missing
    # role behind a vehicle that cannot leave its protected job.
    free = [identity for identity in sorted(fleet) if not fleet[identity].get("protected_task")]
    if len(free) < len(ROLES):
        raise ValueError("rescue seeded fleet requires three uncommitted vehicles")
    rng.shuffle(free)
    for identity, role in zip(free, ROLES):
        profile = fleet[identity]
        if role not in profile["capabilities"]:
            profile["capabilities"].append(role)
            profile["capabilities"].sort()
        if role == "deliver":
            profile["payload_kg"] = round(rng.uniform(cfg.get("payload_kg", 1.0), 3.0), 3)
