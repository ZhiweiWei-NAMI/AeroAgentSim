"""Feed narrative declarations for the traffic-accident pack.

Every scenario registry message with kind=event and a ``traffic.`` prefix is
declared, including traffic.inject controls and occupancy telemetry. Templates
only use required payload members; optional links (asset_id, source_cut, ...)
appear in entities only when genuinely present. Chinese siblings use the same
placeholders and never change the committed event or its links.
"""

from __future__ import annotations

import copy
from typing import Any


def declarations() -> dict[str, dict[str, Any]]:
    """Return a copy of the pack's narrative declarations."""
    return copy.deepcopy(EVENT_NARRATIVES)


EVENT_NARRATIVES: dict[str, dict[str, Any]] = {
    "traffic.incident.activated": {
        "template": "Accident activated",
        "template_zh": "交通事故发生",
        "category": "emergency",
        "severity": "critical",
        "entities": ["incident", "actor"],
        "position": "incident",
    },
    "traffic.incident.detected": {
        "template": "Accident detected",
        "template_zh": "发现交通事故",
        "category": "emergency",
        "severity": "warning",
        "entities": ["incident", "actor"],
        "position": "incident",
    },
    "traffic.road.blocked": {
        "template": "Road blocked after the accident",
        "template_zh": "事故导致道路阻塞",
        "category": "emergency",
        "severity": "warning",
        "entities": ["incident", "actor"],
        "position": "incident",
    },
    "traffic.road.cleared": {
        "template": "Road cleared after the accident",
        "template_zh": "事故路段恢复通行",
        "category": "emergency",
        "severity": "notice",
        "entities": ["incident", "actor"],
        "position": "incident",
    },
    "traffic.report.sent": {
        "template": "{actor} sent {report} for {incident}",
        "template_zh": "{actor}为{incident}发送报告{report}",
        "category": "emergency",
        "severity": "info",
        "entities": ["actor", "incident", "report"],
    },
    "traffic.broadcast": {
        "template": "Accident report broadcast to nearby responders",
        "template_zh": "事故报告已广播给附近的响应机",
        "category": "emergency",
        "severity": "info",
        "entities": ["incident", "actor"],
    },
    "traffic.bid.joined": {
        "template": "{actor} joined the response bid",
        "template_zh": "{actor}参与事故响应竞标",
        "category": "logistics",
        "severity": "info",
        "entities": ["actor", "bid"],
    },
    "traffic.award.proposed": {
        "template": "Response award proposed",
        "template_zh": "已提出响应机分配方案",
        "category": "logistics",
        "severity": "info",
        "entities": ["actor", "winner_id"],
    },
    "traffic.award.committed": {
        "template": "Response award committed",
        "template_zh": "响应机分配已确认",
        "category": "logistics",
        "severity": "notice",
        "entities": ["incident", "actor"],
    },
    "traffic.award.conflict": {
        "template": "Response award conflict",
        "template_zh": "响应机分配发生冲突",
        "category": "logistics",
        "severity": "warning",
        "entities": ["incident", "actor"],
    },
    "traffic.decision.failed": {
        "template": "Response decision failed",
        "template_zh": "事故响应决策失败",
        "category": "mixed",
        "severity": "warning",
        "entities": ["incident", "actor"],
    },
    "traffic.capture.requested": {
        "template": "{actor} requested a capture",
        "template_zh": "{actor}请求拍摄现场图像",
        "category": "inspection",
        "severity": "info",
        "entities": ["actor", "incident", "capture"],
        "position": "actor",
    },
    "traffic.capture.stored": {
        "template": "{actor} stored a capture",
        "template_zh": "{actor}已保存现场图像",
        "category": "inspection",
        "severity": "info",
        "entities": ["actor", "incident", "capture"],
    },
    "traffic.capture.accepted": {
        "template": "A capture from {actor} was accepted",
        "template_zh": "{actor}的现场图像已验收",
        "category": "inspection",
        "severity": "info",
        "entities": ["actor", "incident", "capture"],
    },
    "traffic.capture.failed": {
        "template": "A capture from {actor} failed",
        "template_zh": "{actor}的现场图像采集失败",
        "category": "inspection",
        "severity": "warning",
        "entities": ["actor", "incident"],
    },
    "traffic.capture.upload_verified": {
        "template": "Capture upload verified",
        "template_zh": "图像上传验证通过",
        "category": "inspection",
        "severity": "info",
        "entities": ["actor"],
    },
    "traffic.inject.accident": {
        "template": "Accident input received",
        "template_zh": "已收到事故注入指令",
        "category": "emergency",
        "severity": "critical",
        "entities": ["incident", "actor"],
        "position": "incident",
    },
    "traffic.inject.wind": {
        "template": "Wind input received",
        "template_zh": "已收到风况注入指令",
        "category": "weather",
        "severity": "warning",
        "entities": ["incident", "actor"],
    },
    "traffic.inject.pause-test": {
        "template": "Pause test input received",
        "template_zh": "已收到暂停测试指令",
        "category": "security",
        "severity": "notice",
        "entities": ["incident", "actor"],
    },
    "traffic.proposal.report": {
        "template": "{actor} proposed {action} for {incident}",
        "template_zh": "{actor}为{incident}提出行动建议：{action}",
        "category": "logistics",
        "severity": "info",
        "entities": ["actor", "incident"],
    },
    "traffic.proposal.bid": {
        "template": "{actor} bid on {task}: {reason}",
        "template_zh": "{actor}竞标任务{task}：{reason}",
        "category": "logistics",
        "severity": "info",
        "entities": ["actor", "task"],
    },
    "traffic.proposal.award": {
        "template": "Award proposal by {actor}: {action}",
        "template_zh": "{actor}提出分配建议：{action}",
        "category": "logistics",
        "severity": "info",
        "entities": ["actor"],
    },
    "traffic.road.occupancy_evaluated": {
        "template": "Road occupancy evaluated over {actors}",
        "template_zh": "已评估{actors}的道路占用情况",
        "category": "logistics",
        "severity": "info",
        "entities": ["actors"],
        "hidden": True,
    },
    "traffic.air.arrived": {
        "template": "{entity} arrived",
        "template_zh": "{entity}已到达",
        "category": "mobility",
        "severity": "info",
        "entities": ["entity"],
        "position": "position",
    },
}
