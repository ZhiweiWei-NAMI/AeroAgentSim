"""Readable parameter summaries, regenerated from the actual plan operations."""

from __future__ import annotations

from typing import Any

from .catalogue import OperationCatalogue, metadata


def unchanged_plan(plan: dict) -> bool:
    """Bound proposals and alternatives need actual effects, not just a recipe."""
    difference = plan.get("diff_base")
    return (plan["compile"]["ok"] and ("workspace" in plan["base"] or bool(plan.get("variant"))) and difference is not None
            and not any(difference[group] for group in ("added", "changed", "removed"))
            and not any(difference.get("entities", {}).values()))


def _value(value: Any, key: str, zh: bool, copy: dict) -> str:
    language = int(zh)
    if key == "release" and isinstance(value, dict):
        times = value.get("times_s")
        if isinstance(times, list) and times and all(type(v) in (int, float) for v in times):
            return f"{len(times)} 次，{times[0]:g}–{times[-1]:g} s" if zh else f"{len(times)} releases, {times[0]:g}–{times[-1]:g} s"
        if value.get("mode") == "poisson" and type(value.get("rate_per_s")) in (int, float):
            parts = [f"{value['rate_per_s']:g}/s"]
            parts.extend(f"{copy['parameters'][k][language]} {_value(value[k], k, zh, copy)}" for k in ("start_s", "end_s") if k in value)
            if "burst_size" in value:
                parts.append(("每批 " if zh else "burst ") + str(value["burst_size"]))
            return ", ".join(parts)
    if isinstance(value, dict):
        return ", ".join(f"{copy['parameters'].get(k, (k.replace('_', ' '), k))[language]} {_value(v, k, zh, copy)}" for k, v in value.items())
    if isinstance(value, list):
        preview = ", ".join(_value(v, key, zh, copy) for v in value[:4])
        return preview + (f" … ({len(value)})" if len(value) > 4 else "")
    if type(value) is bool:
        return ("是" if value else "否") if zh else ("yes" if value else "no")
    if isinstance(value, (int, float)):
        number = format(value, ".12g") if key in {"bounds", "lat", "lon"} else f"{value:g}"
        return number + (" " + copy["units"][key] if key in copy["units"] else "")
    if isinstance(value, str):
        # Object ids are preserved verbatim; role and option values get labels.
        return copy["values"].get(value, (value, value))[language] if key not in {"id", "fleet", "vehicle", "task", "extract", "from", "to"} else value
    return str(value)


def operation_summaries(operations: list[dict]) -> list[dict]:
    catalogue = OperationCatalogue()
    copy = {key: metadata(key, {}, catalogue=catalogue) for key in ("parameters", "values", "units")}
    labels_by_name = metadata("operation_labels", {}, catalogue=catalogue)
    rows = []
    for op in operations:
        try:
            spec = catalogue.resolve(op["op"])
            labels = labels_by_name.get(spec.display.get("legacy_operation", spec.id), (spec.title, spec.title_zh))
        except ValueError:
            labels = ("Change", "修改")
        texts = []
        for zh in (False, True):
            params = op["params"]
            parts = []
            name = params.get("label_zh" if zh else "label")
            if name:
                parts.append(name)
            for key, value in params.items():
                if key in {"label", "label_zh"}:
                    continue
                caption = copy['parameters'].get(key, (key.replace("_", " "), key))[int(zh)]
                parts.append((caption + " " if caption else "") + _value(value, key, zh, copy))
            text = labels[int(zh)] + (": " + ", ".join(parts) if parts else "")
            texts.append(text[:320] + ("…" if len(text) > 320 else ""))
        rows.append({"id": op.get("id"), "label": labels[0], "label_zh": labels[1], "text": texts[0], "text_zh": texts[1]})
    return rows


def change_summaries(difference: dict) -> list[dict[str, str]]:
    """Describe actual compiled effects, including prerequisites the model did not request."""
    rows = []
    for group, verbs in (("added", ("Add", "新增")), ("changed", ("Change", "修改")), ("removed", ("Remove", "移除"))):
        for item in difference[group]:
            row = {"kind": item["kind"], "id": item["id"]}
            for zh in (False, True):
                suffix = "_zh" if zh else ""
                text = f"{verbs[int(zh)]} {item['kind_label' + suffix]}: {item['label' + suffix]} ({item['id']})"
                fields = [f"{field['label' + suffix]}: {field['before_text' + suffix]} → {field['after_text' + suffix]}"
                          for field in item.get("fields", [])]
                row["text" + suffix] = text + ("; " + "; ".join(fields) if fields else "")
            rows.append(row)
    return rows
