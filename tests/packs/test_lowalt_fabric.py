"""One journal proves cross-business effects and real live operator ingress."""

from __future__ import annotations

import gc
from pathlib import Path

import yaml
from aerokernel import CommandRequest, Fact, Instant, Stamp, replay

from aeroagentsim.packs.lowalt.overlay import EVENT_NARRATIVES, TASK_CATEGORIES
from aeroagentsim.platform import RunSession
from aeroagentsim.scenario import load_scenario

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/city-day"


def test_city_day_shared_fabric_and_replay(tmp_path: Path) -> None:
    document = yaml.safe_load((BASE / "scenario.yaml").read_text())
    # Offline defaults contain authored operator commands. Admit those same
    # decisions through the real named live stream for this fabric proof.
    commands = document["bindings"]["commands"]
    operator = [c for c in commands if c["schema"] == "aas.runtime.inject_event"]
    document["bindings"]["commands"] = [c for c in commands if c not in operator]
    document["ingress_streams"][0]["initial_watermark_ns"] = 0
    scenario = load_scenario(document, base=BASE)
    directory = tmp_path / "run"
    session = RunSession(scenario, directory)
    try:
        session.start()
        session.advance_watermark(110 * 10**9, stream_id="operator")
        session.run_until(110 * 10**9)
        receipts = []
        for c in operator:
            at = c["at_ns"]
            receipt = session.submit_live(
                "behaviour",
                CommandRequest(c["schema"], c["target"], Instant(at), c["payload"]),
                Stamp("canonical", at, 1, "canonical"),
                stream_id="operator",
            )
            assert receipt.command_id is not None
            receipts.append(receipt)
        session.advance_watermark(300 * 10**9, stream_id="operator")
        session.run_until(300 * 10**9)
        view = session.simulation.kernel.view()
        for receipt in receipts:
            assert view.action(receipt.command_id).status == "succeeded"
        # records builds a tuple of every expanded diagnostic tree. Stream the
        # identical trees instead, retaining only small business-event payloads.
        records = session.simulation.kernel.iter_records()
        from aerokernel.codec import decode_record
        from aerokernel.values import thaw

        events = []
        for record in records:
            for item in record.get("items", []):
                if "message" not in item or "proposal" not in item:
                    continue
                message = decode_record(item["message"])
                if message.kind == "event":
                    events.append(
                        {
                            "schemaId": message.schema_id,
                            "at": {"ns": str(message.at.ns)},
                            "payload": thaw(message.payload),
                        }
                    )
        by_schema = {}
        for e in events:
            by_schema.setdefault(e["schemaId"], []).append(e)
        for schema in (
            "traffic.incident.detected",
            "traffic.proposal.bid",
            "traffic.award.committed",
            "traffic.capture.accepted",
            "lowalt.airspace.zone_activated",
            "lowalt.airspace.zone_deactivated",
            "lowalt.task.preempted",
            "lowalt.task.resumed",
            "lowalt.inspection.observed",
            "lowalt.spraying.applied",
            "lowalt.site.closed",
        ):
            assert by_schema.get(schema), schema
        assert any(
            e["payload"].get("zone") == "incident-no-fly"
            for e in by_schema["lowalt.task.replanned"]
        )
        assert any(
            e["payload"]["reason"] == "vertiport closed"
            for e in by_schema["lowalt.task.replanned"]
        )
        assert any(
            e["payload"]["task"] == "operator-order-1"
            for e in by_schema["lowalt.task.completed"]
        )
        kinds = {
            e["payload"]["task"]: e["payload"]["kind"]
            for e in by_schema["lowalt.task.created"]
        }
        replans = [
            e
            for e in by_schema["lowalt.task.replanned"]
            if e["payload"].get("zone") == "incident-no-fly"
        ]
        assert {"parcel", "passenger"} <= {kinds[e["payload"]["task"]] for e in replans}
        activation = min(
            int(e["at"]["ns"]) for e in by_schema["lowalt.airspace.zone_activated"]
        )
        assert all(int(e["at"]["ns"]) >= activation for e in replans)
        classes = {
            e["id"]: e["facts"]["lowalt.vehicle_class"]
            for e in scenario.document["entities"]
            if e["type"] == "lowalt:UAV"
        }
        assert any(
            classes.get(e["payload"]["a"]) != classes.get(e["payload"]["b"])
            and e["payload"]["a"] in classes
            and e["payload"]["b"] in classes
            for e in by_schema["lowalt.airspace.conflict"]
        )
        cross_business = {
            tuple(sorted((e["payload"]["a"], e["payload"]["b"])))
            for e in by_schema["lowalt.airspace.conflict"]
            if e["payload"]["a"] in classes
            and e["payload"]["b"] in classes
            and classes[e["payload"]["a"]] != classes[e["payload"]["b"]]
        }
        resolved = {
            tuple(sorted((e["payload"]["a"], e["payload"]["b"])))
            for e in by_schema["lowalt.airspace.conflict_resolved"]
        }
        assert cross_business & resolved
        holds = by_schema["lowalt.weather.hold"]
        assert {"inspection", "spraying"} <= {
            classes[e["payload"]["vehicle"]] for e in holds
        }
        assert not any(classes[e["payload"]["vehicle"]] == "parcel" for e in holds)
        assert by_schema["lowalt.weather.release"]
        assert not by_schema.get("lowalt.task.failed")
        assert not by_schema.get("lowalt.airspace.geofence_entered")
        task_kinds = {
            e["payload"]["task"]: e["payload"]["kind"]
            for e in by_schema["lowalt.task.created"]
        }
        categories = [
            (
                int(e["at"]["ns"]),
                TASK_CATEGORIES[task_kinds[e["payload"]["task"]]]
                if e["schemaId"].startswith("lowalt.task.")
                and e["payload"].get("task") in task_kinds
                else EVENT_NARRATIVES[e["schemaId"]]["category"],
            )
            for e in events
            if e["schemaId"] in EVENT_NARRATIVES
        ]
        assert len({category for _, category in categories}) >= 4
        # Several categories occur during overlapping business lifetimes.
        early = {category for at, category in categories if 0 < at < 110 * 10**9}
        late = {
            category for at, category in categories if 110 * 10**9 <= at < 300 * 10**9
        }
        assert len(early & late) >= 4
        expected = {
            key: thaw(f.value)
            for key in view._store.writers
            for f in (view.field(key, view.instant),)
            if isinstance(f, Fact)
        }
        instant = view.instant
    finally:
        session.close()
    # Equality still covers every committed field, including dynamic entities.
    # The live runtime is no longer needed while constructing the replay runtime.
    del view, session, records, record, item, message, events, by_schema
    gc.collect()
    recorded = replay(directory / "journal.jsonl").view()
    assert recorded.instant == instant
    for key, value in expected.items():
        assert thaw(recorded.field(key, instant).value) == value, key
