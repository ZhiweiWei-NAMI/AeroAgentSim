"""Receiver-local consumption of actual command receipts.

This produces controller decisions only. Physical motion must be replayed by
the existing trajectory executor and the coupled network trace recomputed.
"""
from .command_receipts import controller_receipts_before


def consume_received_commands(records, *, observation_ns, receiver_owner,
                              receiver_epoch, policy_delay_ns,
                              consumed_command_ids=(), queue_available_ns=0):
    """Apply declared policy/queue delays after actual receiver acceptance.

    No command is obtained from future faults, planned interventions, sender
    records or an omniscient truth table. A planned motion segment is not
    rewritten here. The caller owns authoritative executor/queue timing.
    """
    if type(policy_delay_ns) is not int or policy_delay_ns < 0:
        raise ValueError("Policy delay must be an explicit nonnegative integer nanosecond value")
    consumed = set(consumed_command_ids)
    ready = sorted(controller_receipts_before(records, observation_ns,
        receiver_owner=receiver_owner, receiver_epoch=receiver_epoch),
        key=lambda r: (r["accepted_ns"], r["command_id"]))
    decisions = []
    for receipt in ready:
        command_id = receipt["command_id"]
        if command_id in consumed:
            continue
        policy_ready_ns = receipt["accepted_ns"] + policy_delay_ns
        execution_ns = max(policy_ready_ns, queue_available_ns)
        if execution_ns >= observation_ns:
            continue
        consumed.add(command_id)
        decisions.append({"schema_version": "p09.receiver-controller-consumption/v1",
            "command_id": command_id, "receiver_owner": receiver_owner,
            "receiver_epoch": receiver_epoch, "action": receipt["action"],
            "receive_ns": receipt["receive_ns"], "accepted_ns": receipt["accepted_ns"],
            "policy_delay_ns": policy_delay_ns, "policy_ready_ns": policy_ready_ns,
            "queue_available_ns": queue_available_ns, "execution_decision_ns": execution_ns,
            "status": "expired_before_execution" if execution_ns >= receipt["deadline_ns"]
                      else "ready_for_executor",
            "physical_motion_evidence": None})
    return tuple(decisions), tuple(sorted(consumed))
