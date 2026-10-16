"""Independent journal arithmetic and bilingual rescue event projection."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aerokernel.codec import decode_record, encode
from aerokernel.messages import Emit, Message
from aerokernel.registry import MemoryRegistry
from aerokernel.time import Instant
from aerokernel.values import freeze

from aeroagentsim.packs.metrics import compute
from aeroagentsim.services.kpis import KPIProjector, RESCUE_METRICS
from aeroagentsim.services.narratives import LOWALT_DEFAULTS, NarrativeProjector

SECOND = 1_000_000_000


def item(schema, payload, at_s, identifier, *, command=False):
    kind = "command" if command else "event"
    at = Instant(at_s * SECOND)
    message = Message(identifier, kind, schema, "partition", "business", 0, at, at, freeze(payload), ())
    return {"proposal": encode(Emit(kind, schema, schema, at, payload)), "message": encode(message)}


def envelope(kind, **data):
    return {"data_json": json.dumps({"event": "R7", "kind": kind, **data}, ensure_ascii=False)}


def journal():
    rows = [
        (5, "rescue.event", envelope("noticed")),
        (5, "rescue.bid", {"data_json": json.dumps({"roles": ["inspect", "relay"], "reason": "参与"}, ensure_ascii=False)}),
        (5, "rescue.bid", {"data_json": '{"roles": []}'}),
        (5, "rescue.event", envelope("plan_computed")),
        (5, "aas.langgraph.record", {"decision_id": "rescue_center/1", "phase": "model_call", "data_json": '{"identity": "call", "request": {}}'}),
        (21, "rescue.transport.send", envelope("report", source="U2", relay="U3", destination="command-center")),
        (21, "adapters.ns3.send", {"packet_id": "R7/rescue/report/0", "size": 100}),
        (22, "adapters.ns3.delivery", {"packet_id": "R7/rescue/report/0", "size": 100}),
        (22, "adapters.ns3.send", {"packet_id": "R7/rescue/report/1", "size": 120}),
        (30, "adapters.ns3.delivery", {"packet_id": "R7/rescue/report/1", "size": 120}),
        (30, "rescue.transport.delivered", envelope("report")),
        (30, "rescue.event", envelope("message_delivered", message_kind="report")),
        (30, "rescue.event", envelope("plan_computed")),
        (30, "aas.langgraph.record", {"decision_id": "rescue_center/2", "phase": "model_call", "data_json": '{"identity": "call", "request": {}}'}),
        (30, "aas.langgraph.record", {"decision_id": "unrelated/1", "phase": "model_call", "data_json": '{"identity": "call", "request": {}}'}),
        (60, "rescue.event", envelope("delivery_completed")),
        (65, "rescue.event", envelope("outcome", success=True, time_to_confirmation_s=25, time_to_delivery_s=55)),
    ]
    return [{"kind": "commit", "index": index, "instant": encode(Instant(at_s * SECOND)),
             "items": [item(schema, payload, at_s, f"m{index}", command=schema == "adapters.ns3.send")]}
            for index, (at_s, schema, payload) in enumerate(rows)]


def test_c2_arithmetic_checkpoint_dedup_and_cli_share_the_journal():
    registry = MemoryRegistry(())
    projector = KPIProjector(registry)
    rows = journal()
    for row in rows:
        projector.consume(row)
    values = {name: row["value"] for name, row in projector.snapshot().items()}
    expected_bus = [decode_record(row["items"][0]["message"]) for row in rows
                    if decode_record(row["items"][0]["message"]).schema_id.startswith("rescue.")]
    # The wire encoding is decoded by the normal projector below; expected
    # bytes are measured independently from the authored JSON strings.
    assert values["rescue_success"] == 1
    assert values["rescue_time_to_confirmation_s"] == 25
    assert values["rescue_time_to_delivery_s"] == 55
    assert values["rescue_plans"] == 2
    assert values["rescue_bids"] == 2
    assert values["rescue_candidates"] == 2
    assert values["rescue_radio_packets"] == values["rescue_radio_delivered"] == 2
    assert values["rescue_radio_bytes"] == 220
    assert values["rescue_model_calls"] == 2
    assert values["rescue_bus_messages"] == len(expected_bus)
    assert values["rescue_bus_bytes"] == sum(len(m.payload["data_json"].encode("utf-8")) for m in expected_bus)
    checkpoint = projector.cursor()
    restored = KPIProjector.from_cursor(registry, checkpoint)
    for row in rows:
        restored.consume(row)
    assert restored.snapshot() == projector.snapshot()
    kernel = SimpleNamespace(configuration=freeze({"scenario": {"id": "rescue", "engines": {
        "tasks": {"plugin": "lowalt_tasks", "config": {"rescue": {}}}}}}), records=rows)
    cli = compute(kernel)
    assert {name: cli[name] for name in RESCUE_METRICS} == {name: values[name] for name in RESCUE_METRICS}


def test_measured_times_available_before_outcome_and_failure_is_not_success():
    projector = KPIProjector(MemoryRegistry(()))
    rows = journal()
    for row in rows[:-1]:
        projector.consume(row)
    values = projector.snapshot()
    assert values["rescue_success"]["value"] is None
    assert values["rescue_time_to_confirmation_s"]["value"] == 25
    assert values["rescue_time_to_delivery_s"]["value"] == 55
    failure = {"kind": "commit", "index": 100, "instant": encode(Instant(240 * SECOND)),
               "items": [item("rescue.event", envelope("outcome", success=False,
                    time_to_confirmation_s=25, time_to_delivery_s=55), 240, "failed-outcome")]}
    projector.consume(failure)
    assert projector.snapshot()["rescue_success"]["value"] == 0


def test_no_observation_does_not_create_zero_time_or_false_success():
    projector = KPIProjector(MemoryRegistry(()))
    assert all(projector.snapshot()[name]["value"] is None for name in RESCUE_METRICS)
    projector.consume({"kind": "commit", "index": 0, "instant": encode(Instant(5 * SECOND)),
                       "items": [item("rescue.event", envelope("noticed"), 5, "notice")]})
    values = projector.snapshot()
    assert values["rescue_success"]["value"] is None
    assert values["rescue_time_to_delivery_s"]["value"] is None
    assert values["rescue_time_to_confirmation_s"]["value"] is None
    assert values["rescue_radio_packets"]["value"] == 0


@pytest.mark.parametrize("kind,extra,en,zh", [
    ("noticed", {}, "Possible rooftop rescue", "疑似求援"),
    ("plan_computed", {"plan": {"status": "feasible", "assignments": [{"vehicle": "U2", "role": "inspect"}, {"vehicle": "U3", "role": "relay"}]}}, "U2 inspect", "U2 复查"),
    ("arrived", {"vehicle": "U2", "role": "inspect"}, "U2 reached", "U2 到达复查点"),
    ("relay_ready", {"vehicle": "U3"}, "U3 is holding", "U3 已就位"),
    ("observation", {"vehicle": "U2", "evidence": "positive"}, "authored truth", "作者设定真值"),
    ("message_delivered", {"message_kind": "report"}, "Report received", "收到复查报告"),
    ("delivery_completed", {"vehicle": "U4"}, "U4 completed delivery", "U4 完成物资投送"),
    ("message_delivered", {"message_kind": "delivery_ack"}, "acknowledgement received", "收到投送回执"),
    ("roles_released", {}, "roles released", "角色已释放"),
    ("deadline_missed", {}, "deadline missed", "超过截止时间"),
    ("outcome", {"success": True}, "successfully", "成功解决"),
    ("model_failed", {}, "decision failed", "决策失败"),
    ("communication_failed", {}, "communication failed", "通信失败"),
    ("plan_rejected", {}, "plan rejected", "方案被拒绝"),
    ("relay_interrupted", {"vehicle": "U3"}, "interrupted", "中断"),
])
def test_every_rescue_event_is_a_readable_bilingual_line(kind, extra, en, zh):
    projector = NarrativeProjector(LOWALT_DEFAULTS)
    at = Instant(5 * SECOND)
    message = Message("m", "event", "rescue.event", "partition", "tasks", 0, at, at,
                      freeze(envelope(kind, **extra)), ())
    line = projector._narrate(message)
    assert en in line["text"] and zh in line["text_zh"]
    assert "R7/a4" not in line["text"]
    assert line["category"] == "emergency" and "R7" in line["entities"]
