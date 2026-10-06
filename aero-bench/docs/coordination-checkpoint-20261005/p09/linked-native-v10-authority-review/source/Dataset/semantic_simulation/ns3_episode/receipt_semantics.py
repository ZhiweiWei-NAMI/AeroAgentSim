"""Explicit causal bindings for the already executed P09 coupled replay.

Receiver schedule loss and global accepted-TX loss have different denominators
and authorities. This module cannot substitute one for the other, create a
receipt, change motion or claim an aircraft received raw gateway observations.
"""
from .receipt_replay import (EPISODE, OWNER, GATEWAY, EVENTS, COMMANDED,
    MONITOR_VERSION, LOSS_THRESHOLD, CONSECUTIVE_LIMIT)
from .receipt_scope import DEFAULT_SCOPE

VERSION = "p09.receipt-state-rule-event-binding/v1"
RULE_VERSION = "p09.gateway-c2-policy/v1"
ESTIMATE = "p09.gateway.expected_sequence.ttl_not_timely_ratio"
CONSECUTIVE = "p09.gateway.expected_sequence.consecutive_missing"
REFERENCE = "p01.network.accepted_tx.mature_ttl_loss_ratio"


def state_definitions(source_run, *, scope=DEFAULT_SCOPE):
    return [
        {"field": REFERENCE, "unit": "1", "dimensions": [],
         "owner": "exact sender/receiver flow and life epochs", "authority": "simulation_packet_reference",
         "source_schema": "p01.ns3.mature-datagram-lp/v1",
         "source": f"{source_run}/network_states.jsonl.gz#/packet_loss_ratio/value",
         "time_support": "mature socket-accepted unique datagrams; cohort anchored by expiry; events strictly before observation",
         "denominator": "socket-accepted unique datagrams whose packet TTL matured in the declared cohort",
         "availability": "UNKNOWN on zero denominator; not accessible as a gateway-local sender-acceptance oracle",
         "controller_input": False},
        {"field": ESTIMATE, "unit": "1", "dimensions": [],
         "owner": {"entity_id": scope.gateway_owner, "life_epoch": 0}, "authority": "receiver_local_schedule_and_receptions",
         "source_schema": MONITOR_VERSION,
         "source": f"{source_run}/receipt_monitor.jsonl.gz#/missing_ratio",
         "time_support": "expected generation in [t-1s,t); generation+200msTTL <t; RX<expiry is timely",
         "denominator": "pre-agreed scheduled sequences with mature TTL, including sender-unsent slots",
         "availability": "KNOWN only with mature expected denominator; raw evidence belongs to gateway",
         "controller_input": "gateway only; aircraft consumes received action command"},
        {"field": CONSECUTIVE, "unit": "count", "dimensions": [],
         "owner": {"entity_id": scope.gateway_owner, "life_epoch": 0}, "authority": "receiver_local_schedule_and_receptions",
         "source_schema": MONITOR_VERSION,
         "source": f"{source_run}/receipt_monitor.jsonl.gz#/consecutive_missing_count",
         "time_support": "consecutive mature scheduled slots up to the same observation",
         "availability": "same schedule/receiver authority as expected-sequence metric", "controller_input": "gateway only"},
    ]


def evaluate_monitor(row, *, run_id, source_run, scope=DEFAULT_SCOPE):
    """Evaluate the declared local rule with no global-label input."""
    if row["schema_version"] != MONITOR_VERSION or (row["receiver_owner"], row["receiver_epoch"]) != (scope.gateway_owner, 0):
        raise ValueError("Gateway state source/schema/owner differs from the scoped policy")
    t = row["observation_ns"]
    if type(t) is not int or t < 0:
        raise ValueError("Observation time must be explicit integer nanoseconds")
    ratio = row["missing_ratio"]
    known = row["metric_status"] == "KNOWN" and ratio is not None
    bad = None if not known else ratio > LOSS_THRESHOLD
    degraded = None if not known else bad and row["consecutive_missing_count"] >= CONSECUTIVE_LIMIT
    if bad != row["bad_communication"] or (degraded is not None and degraded != row["degraded"]):
        raise ValueError("Persisted monitor value differs from its exact versioned rule")
    base = {"run_id": run_id, "episode_id": scope.episode_id, "time_ns": t, "available_ns": t,
        "owner": {"entity_id": scope.gateway_owner, "life_epoch": 0}, "rule_version": RULE_VERSION,
        "profile": {"ratio_threshold": LOSS_THRESHOLD, "consecutive_limit": CONSECUTIVE_LIMIT,
                    "window_ns": 1_000_000_000, "packet_TTL_ns": 200_000_000},
        "state_source": f"{source_run}/receipt_monitor.jsonl.gz#slot={row['slot']}",
        "state_schema": MONITOR_VERSION, "state_observation_ns": t,
        "states": {ESTIMATE: ratio, CONSECUTIVE: row["consecutive_missing_count"]},
        "availability": "KNOWN" if known else "UNKNOWN", "reason": None if known else "no_known_local_denominator",
        "actor_direct_observation": False}
    return [{**base, "evaluation_id": f"{run_id}:gateway:{t}:{name}",
        "predicate_id": f"p09.gateway.{name}/v1", "value": "UNKNOWN" if value is None else "TRUE" if value else "FALSE",
        "rule": rule} for name, value, rule in (
            ("bad_ratio", bad, {"op": "gt", "state": ESTIMATE, "threshold": LOSS_THRESHOLD}),
            ("degraded", degraded, {"op": "and", "args": [
                {"op": "gt", "state": ESTIMATE, "threshold": LOSS_THRESHOLD},
                {"op": "gte", "state": CONSECUTIVE, "threshold": CONSECUTIVE_LIMIT}]}))]


def bind_replay_events(evidence, monitor_rows, *, run_id, source_run, scope=DEFAULT_SCOPE):
    """Connect the exact local evaluation to commands and numerical motion."""
    decisions = evidence["decisions"]
    evaluated = [item for row in monitor_rows for item in evaluate_monitor(row, run_id=run_id, source_run=source_run, scope=scope)]
    by_key = {(item["time_ns"], item["predicate_id"]): item for item in evaluated}
    origins = {}
    for root, predicate, required in ((scope.events[0], "degraded", "TRUE"), (scope.events[3], "bad_ratio", "FALSE")):
        t = decisions["event_times_ns"][root]
        if t is None:
            origins[root] = None
            continue
        item = by_key[t, f"p09.gateway.{predicate}/v1"]
        if item["value"] != required:
            raise ValueError("Event is not supported by its actual receiver-local rule evaluation")
        origins[root] = item
    events = []
    for index, event in enumerate(scope.events):
        t = decisions["event_times_ns"][event]
        cause = origins[scope.events[3] if index >= 3 else scope.events[0]]
        row = {"schema_version": VERSION, "run_id": run_id, "episode_id": scope.episode_id,
            "event_id": event, "time_ns": t, "owner": scope.mission_owner if event in scope.commanded else scope.gateway_owner,
            "life_epoch": 0, "predicate_evaluation_id": None if cause is None else cause["evaluation_id"],
            "predicate_available_at_gateway_ns": None if cause is None else cause["available_ns"],
            "rule_version": RULE_VERSION, "state_source": None if cause is None else cause["state_source"],
            "parent_event_id": None if index == 0 else scope.events[index-1],
            "parent_event_time_ns": None if index == 0 else decisions["event_times_ns"][scope.events[index-1]],
            "status": "not_triggered" if t is None else "gateway_evaluation" if event not in scope.commanded else "numerical_executor_after_actual_receipt",
            "raw_gateway_state_available_to_actor_ns": None,
            "motion_source": f"{source_run}/{evidence['trajectory_ref']}" if event in scope.commanded and t is not None else None}
        if event in scope.commanded and t is not None:
            command = decisions["command_evidence"][event]
            consumption = command["controller_consumption"]
            receipt = next(r for r in command["attempts"] if r["status"] == "accepted" and r["accepted_ns"] == command["receipt_ns"])
            if (receipt["source_owner"],receipt["source_epoch"],receipt["receiver_owner"],receipt["receiver_epoch"]) != (scope.gateway_owner,0,scope.mission_owner,0):
                raise ValueError("Actual command receiver/source epochs differ from bound actors")
            if cause is None or not (cause["available_ns"] <= command["send_ns"] <= receipt["accepted_ns"] < t):
                raise ValueError("Local predicate evidence and command reception must precede execution")
            if consumption["executor_tick_ns"] != t or consumption["status"] != "ready_for_executor":
                raise ValueError("Physical event does not match the receiver controller consumption")
            if (consumption["command_id"],consumption["receiver_owner"],consumption["receiver_epoch"]) != (command["command_id"],scope.mission_owner,0):
                raise ValueError("Controller consumption identity differs from actual command receipt")
            if consumption["receive_ns"] != receipt["receive_ns"] or consumption["accepted_ns"] != receipt["accepted_ns"]:
                raise ValueError("Controller consumption uses a different command reception")
            row.update(command_id=command["command_id"], command_packet_id=receipt["packet_id"],
                command_send_ns=command["send_ns"], command_receive_ns=receipt["receive_ns"],
                command_accept_ns=receipt["accepted_ns"], controller_consumption=consumption,
                command_cause_reference="logical command ledger; raw local metric is not claimed serialized on wire",
                actor_information="received action command, not direct gateway/global loss observation")
        if t is not None and index > 0:
            parent = row["parent_event_time_ns"]
            if parent is None or parent > t:
                raise ValueError("Authored predecessor is absent or follows the linked event")
        events.append(row)
    return {"schema_version": VERSION, "run_id": run_id, "source_run": source_run,
        "definitions": state_definitions(source_run, scope=scope), "predicate_evaluations": evaluated,
        "event_bindings": events, "motion_changed_by_this_adapter": False,
        "old_P_semantics_overwritten": False,
        "boundary": "Actual ns3 UDP command RX followed by numerical authored-waypoint executor; not PX4/UE execution"}


def write_replay_bindings(output, evidence, monitor_rows, *, run_id, source_run, scope=DEFAULT_SCOPE):
    """Persist the stable run's evaluations and return exact evidence references."""
    import json
    from pathlib import Path
    from .event_logs import write_jsonl
    output = Path(output)
    bound = bind_replay_events(evidence, monitor_rows, run_id=run_id, source_run=source_run, scope=scope)
    evaluations = bound.pop("predicate_evaluations")
    events = bound.pop("event_bindings")
    names = ("local_predicate_evaluations.jsonl.gz", "causal_event_bindings.jsonl.gz", "predicate_binding_definitions.json")
    write_jsonl(output/names[0], evaluations, compression="gzip")
    write_jsonl(output/names[1], events, compression="gzip")
    bound.update(predicate_evaluations_ref=names[0], event_bindings_ref=names[1],
                 evaluation_count=len(evaluations), event_count=len(events))
    with (output/names[2]).open("x") as stream:
        json.dump(bound, stream, indent=2, allow_nan=False)
        stream.write("\n")
    by_event = {row["event_id"]: row for row in events}
    for row in evidence["timeline"]:
        binding = by_event[row["event_id"]]
        row.update(predicate_evaluation_id=binding["predicate_evaluation_id"],
            predicate_available_at_gateway_ns=binding["predicate_available_at_gateway_ns"],
            predicate_rule_version=RULE_VERSION, predicate_state_source=binding["state_source"],
            causal_binding_ref=f"{names[1]}#event_id={row['event_id']}")
    return {"schema_version": VERSION, "rule_version": RULE_VERSION,
        "definitions_ref": names[2], "predicate_evaluations_ref": names[0],
        "event_bindings_ref": names[1], "files": names,
        "old_P_semantics_overwritten": False}
