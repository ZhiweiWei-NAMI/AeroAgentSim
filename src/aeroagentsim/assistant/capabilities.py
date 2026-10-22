"""Compact model-facing catalogue grounded in installed starting points."""

from __future__ import annotations

import copy
from typing import Any

from .catalogue import OperationCatalogue, metadata


def _topics(catalogue: OperationCatalogue) -> dict:
    topics = metadata("topics", {}, catalogue=catalogue)
    for spec in catalogue.operations.values():
        topics.setdefault(spec.topic, (spec.topic, spec.topic))
    return topics


def capability_topics() -> list[str]:
    catalogue = OperationCatalogue()
    return ["all", *_topics(catalogue), *metadata("topic_aliases", {}, catalogue=catalogue)]


# Compatibility constants; requests rebuild from the effective discovery snapshot.
TOPICS = metadata("topics", {})
TOPIC_ALIASES = metadata("topic_aliases", {})
CAPABILITY_TOPICS = capability_topics()


def _excerpt(text: str, length: int) -> str:
    return text if len(text) <= length else text[:length].rstrip() + "…"


def capabilities(topic: str | None, *, templates, algorithms) -> dict:
    catalogue = OperationCatalogue()
    topics = _topics(catalogue)
    topic = metadata("topic_aliases", {}, catalogue=catalogue).get(topic, topic)
    if topic == "all":
        topic = None
    if topic is not None and topic not in topics:
        raise ValueError(f"topic: {topic!r}; valid options: {capability_topics()} / 请选择列出的能力主题")
    installed = algorithms() if callable(algorithms) else algorithms
    if not isinstance(installed, dict):
        raise TypeError("algorithms must be the installed slot catalogue / algorithms 必须是已安装算法槽目录")
    template_metadata = templates.list()
    rows = catalogue.display()
    if topic is None:
        operations = {}
        for name in topics:
            operations[name] = {key: {"description": catalogue.resolve(key).title, "description_zh": catalogue.resolve(key).title_zh}
                                for key, row in rows.items() if row["topic"] == name}
        return {"topics": list(topics), "operations": operations,
                "detail": "Select a topic for parameter schemas, ranges, defaults and examples / 选择主题查看参数、范围、默认值和示例",
                "operation_shape": {"id": "op-N", "op": "operation name", "params": {}, "explain": "optional / 可选"},
                "algorithms": {slot: [algorithm["id"] for algorithm in choices] for slot, choices in installed.items()},
                "starting_points": [{"id": row["id"], "summary": _excerpt(row["summary"], 40), "summary_zh": _excerpt(row.get("summary_zh", row["summary"]), 16)} for row in template_metadata],
                "limits": metadata("overview_limits", [], catalogue=catalogue)}
    operation_topic = metadata("operation_topics", {}, catalogue=catalogue).get(topic, topic)
    selected = [row for row in rows.values() if row["topic"] == operation_topic]
    names = metadata("topic_operations", {}, catalogue=catalogue).get(topic)
    if names is not None:
        selected = [row for row in selected if row["name"] in names]
    result: dict[str, Any] = {"topic": topic, "label": topics[topic][0], "label_zh": topics[topic][1],
                              "operations": selected, "limits": metadata("limits", [], catalogue=catalogue)}
    if topic == metadata("algorithms_topic", None, catalogue=catalogue):
        result["algorithms"] = copy.deepcopy(installed)
    if topic == metadata("roles_topic", None, catalogue=catalogue):
        result["roles"] = {name: {"vehicle_class": role[0], "task_kinds": role[1], "label": role[2], "label_zh": role[3]}
                           for name, role in metadata("ROLES", {}, catalogue=catalogue).items()}
    if topic == metadata("controls_topic", None, catalogue=catalogue):
        result["starting_points"] = [{"id": row["id"], "title": row["title"], "title_zh": row.get("title_zh", row["title"]), "controls": copy.deepcopy(row["knobs"])} for row in template_metadata]
    return result
