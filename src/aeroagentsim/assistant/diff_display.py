"""Presentation metadata for operation diffs; raw identities and values survive."""

from __future__ import annotations

import re
from typing import Any

from .catalogue import OperationCatalogue, metadata

DISPLAY_FIELDS = {"label", "label_zh", "before_text", "after_text"}


def _humanize(name: str) -> str:
    return re.sub(r"[_.:/]+", " ", name).strip().capitalize()


def _metadata(name: str, kind: str, identity: str, vocabulary: dict) -> tuple[str, str, str | None]:
    key = name.rsplit(".", 1)[-1]
    if kind == "rule" and key == "value":
        key = identity
    if identity == "algorithms" and key == "plugin":
        key = name.split(".", 1)[0]
    if kind == "event" and key == "kind":
        # Incident rows label their kind explicitly; enum values carry Fire/Person fell copy.
        name = key = "incident_kind"
    en, zh = vocabulary["field_labels"].get(name, vocabulary["field_labels"].get(key, (_humanize(name), _humanize(name))))
    unit = {"lat": "°", "lon": "°", "bounds": "°", "direction_deg": "°", "from_deg": "°", "alt": "m", "wind_speed": "m/s", "wind_limit": "m/s", "order_rate": "/s", "rate_per_s": "/s", "precipitation_mm_h": "mm/h"}.get(key)
    if key in vocabulary["position_display_fields"]:
        unit = "m"
    if unit is None:
        unit = next((symbol for suffix, symbol in (("_mps", "m/s"), ("_pct", "%"), ("_kg", "kg"), ("_ns", "ns"), ("_s", "s"), ("_m", "m"), ("_w", "W"), ("_j", "J")) if key.endswith(suffix)), None)
    return en, zh, unit


def _number_text(value: int | float) -> str:
    # Geographic coordinates need more precision than general format's six
    # significant digits, or different bounds can look identical in a review.
    return str(value) if isinstance(value, int) else format(value, ".12g")


def _text(value: Any, name: str, kind: str, identity: str, algorithms: dict, vocabulary: dict, *, zh: bool = False) -> str:
    if value is None or isinstance(value, str) and value.strip().lower() in {"", "none", "null"}:
        return "—"
    key = name.rsplit(".", 1)[-1]
    _, _, unit = _metadata(name, kind, identity, vocabulary)
    symbol = vocabulary["units_zh"].get(unit, unit) if zh and unit else unit
    if isinstance(value, bool):
        return ("开启" if value else "关闭") if zh else ("On" if value else "Off")
    if isinstance(value, (int, float)):
        return _number_text(value) + (f" {symbol}" if symbol else "")
    slot = name.split(".", 1)[0] if identity == "algorithms" else None
    if isinstance(value, str):
        if slot in algorithms and (key == slot or key == "plugin"):
            row = next((r for r in algorithms[slot] if r["id"] == value), None)
            if row is not None:
                return row.get("label_zh", row["label"]) if zh else row["label"]
        if key in {"mode", "run_mode", "role", "order", "selection", "accept_when", "kind", "light"} and value in vocabulary["enum_labels"]:
            return vocabulary["enum_labels"][value][int(zh)]
        return value
    if isinstance(value, list):
        if key in vocabulary["position_display_fields"] and value and all(type(v) in (int, float) for v in value):
            return "(" + ", ".join(_number_text(v) for v in value) + f") {symbol}"
        return ", ".join(_text(v, name, kind, identity, algorithms, vocabulary, zh=zh) for v in value) if value else "—"
    if isinstance(value, dict):
        entries = []
        for child, content in value.items():
            child_name = f"{name}.{child}" if name else child
            en, translated, _ = _metadata(child_name, kind, identity, vocabulary)
            entries.append(f"{translated if zh else en}: {_text(content, child_name, kind, identity, algorithms, vocabulary, zh=zh)}")
        return "; ".join(entries) if entries else "—"
    return str(value)


def enrich_diff(difference: dict) -> dict:
    """Add readable copy once, including rows appended by the plan compiler."""
    catalogue = OperationCatalogue()
    vocabulary = {key: metadata(key, {}, catalogue=catalogue) for key in
                  ("field_labels", "units_zh", "position_display_fields", "enum_labels", "kind_labels")}
    vocabulary["kind_labels"] = {"resource": ("Resource", "资源"), "configuration": ("Configuration", "配置"),
                                 **vocabulary["kind_labels"]}
    algorithms = None
    for group in ("added", "changed", "removed"):
        for row in difference[group]:
            row["label"] = row.get("label") or row["id"]
            row["label_zh"] = row.get("label_zh") or row["id"]
            en, zh = vocabulary["kind_labels"].get(row["kind"], (_humanize(row["kind"]), _humanize(row["kind"])))
            row.update(kind_label=en, kind_label_zh=zh)
            fields = row.get("fields", [])
            # A parent summary repeats information when more specific fields exist.
            if fields:
                row["fields"] = [f for f in fields if not any(other["name"].startswith(f["name"] + ".") for other in fields)]
            for field in row.get("fields", []):
                if DISPLAY_FIELDS <= field.keys():
                    continue
                if row["id"] == "algorithms" and algorithms is None:
                    from aeroagentsim.services.algorithm_catalog import discover_algorithm_catalog

                    algorithms = discover_algorithm_catalog()
                name = field["name"]
                en, zh, unit = _metadata(name, row["kind"], row["id"], vocabulary)
                field.update(label=en + (f" ({unit})" if unit else ""),
                             label_zh=zh + (f"（{vocabulary['units_zh'].get(unit, unit)}）" if unit else ""))
                for side in ("before", "after"):
                    field[side + "_text"] = _text(field.get(side), name, row["kind"], row["id"], algorithms or {}, vocabulary)
                    field[side + "_text_zh"] = _text(field.get(side), name, row["kind"], row["id"], algorithms or {}, vocabulary, zh=True)
    return difference
