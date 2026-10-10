"""Actual command packet receipt records for receiver-local controller replay."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandTransmission:
    command_id: str
    packet_id: str
    source_owner: str
    source_epoch: int
    receiver_owner: str
    receiver_epoch: int
    decision_ns: int
    first_tx_ns: int
    deadline_ns: int
    evidence_available_ns: int
    action: str


@dataclass(frozen=True)
class CommandReception:
    packet_id: str
    receiver_owner: str
    receiver_epoch: int
    receive_ns: int


def record_downlink_receipt(command, reception):
    """Bind an actual datagram RX to its exact destination generation.

    Sending is not receiving. A missing record stays unreceived. Neither
    source departure nor future successful delivery produces acceptance.
    This function does not schedule network delivery or claim physical action.
    """
    if not (command.evidence_available_ns <= command.decision_ns
            <= command.first_tx_ns < command.deadline_ns):
        raise ValueError("Command decision/TX/deadline must follow available evidence")
    row = {"schema_version": "p09.actual-downlink-command-receipt/v1",
           "command_id": command.command_id, "packet_id": command.packet_id,
           "source_owner": command.source_owner, "source_epoch": command.source_epoch,
           "receiver_owner": command.receiver_owner, "receiver_epoch": command.receiver_epoch,
           "evidence_available_ns": command.evidence_available_ns,
           "decision_ns": command.decision_ns, "send_ns": command.first_tx_ns,
           "deadline_ns": command.deadline_ns, "action": command.action,
           "receive_ns": None, "accepted_ns": None, "status": "unreceived"}
    if reception is None:
        return row
    if reception.packet_id != command.packet_id:
        raise ValueError("Receipt packet ID differs from command transmission")
    row["receive_ns"] = reception.receive_ns
    if (reception.receiver_owner, reception.receiver_epoch) != (
            command.receiver_owner, command.receiver_epoch):
        row["status"] = "wrong_receiver_epoch"
    elif reception.receive_ns < command.first_tx_ns:
        raise ValueError("Reception precedes actual command TX")
    elif reception.receive_ns >= command.deadline_ns:
        row["status"] = "late"
    else:
        row["accepted_ns"] = reception.receive_ns
        row["status"] = "accepted"
    return row


def controller_receipts_before(records, observation_ns, *, receiver_owner, receiver_epoch):
    """The controller consumes only accepted evidence strictly before t."""
    return tuple(row for row in records
                 if row["status"] == "accepted"
                 and (row["receiver_owner"], row["receiver_epoch"]) ==
                     (receiver_owner, receiver_epoch)
                 and row["accepted_ns"] < observation_ns)
