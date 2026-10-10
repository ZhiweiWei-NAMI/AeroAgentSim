from __future__ import annotations

import importlib.util
import json
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from aero_bench.providers import rpc as _RPC


_ROOT = Path(__file__).parents[2]
_SERVICE_PATH = _ROOT / "containers" / "px4-gazebo" / "service.py"
_SPEC = importlib.util.spec_from_file_location(
    "aero_bench_px4_gazebo_service_command_audit", _SERVICE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_SERVICE = importlib.util.module_from_spec(_SPEC)
sys.modules["rpc"] = _RPC
sys.modules[_SPEC.name] = _SERVICE
_SPEC.loader.exec_module(_SERVICE)


def _frame_fields(
    *,
    message_id: int,
    payload: bytes,
    source_system_id: int,
    source_component_id: int,
    packet_sequence: int,
    frame_encoding: str,
    signature: bytes = b"",
) -> dict[str, object]:
    if signature and len(signature) != _SERVICE.MAVLINK_SIGNATURE_LENGTH:
        raise ValueError("MAVLink v2 signature fixture must contain exactly 13 bytes")
    incompat_flags = _SERVICE.MAVLINK_IFLAG_SIGNED if signature else 0
    header = bytes(
        (
            _SERVICE.MAVLINK_V2_MAGIC,
            len(payload),
            incompat_flags,
            0,
            packet_sequence,
            source_system_id,
            source_component_id,
        )
    ) + message_id.to_bytes(3, "little")
    checksum = _SERVICE._mavlink_x25_checksum(
        header[1:] + payload,
        crc_extra=_SERVICE.MAVLINK_CRC_EXTRAS[message_id],
    )
    frame = header + payload + checksum.to_bytes(2, "little") + signature
    return {
        "canonical_frame_hex": frame.hex(),
        "canonical_frame_length": len(frame),
        "checksum": checksum,
        "compat_flags": 0,
        "frame_encoding": frame_encoding,
        "incompat_flags": incompat_flags,
        "magic": _SERVICE.MAVLINK_V2_MAGIC,
        "message_id": message_id,
        "packet_sequence": packet_sequence,
        "payload_hex": payload.hex(),
        "payload_length": len(payload),
        "signature_hex": signature.hex(),
        "signed_frame": bool(signature),
        "wire_version": 2,
    }


def _command_audit_transport(
    *,
    seq: int = 1,
    timestamp_ns: int = 100,
    command: int = 22,
    packet_sequence: int = 7,
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = bytearray(33)
    payload[28:30] = command.to_bytes(2, "little")
    payload[30] = 1
    payload[31] = 1
    payload[32] = 0
    record: dict[str, object] = {
        "acceptance_boundary": _SERVICE.COMMAND_TRANSPORT_ACCEPTANCE_BOUNDARY,
        "accepted_connection_count": 1,
        **_frame_fields(
            message_id=_SERVICE.MAVLINK_COMMAND_LONG_MESSAGE_ID,
            payload=bytes(payload),
            source_system_id=_SERVICE.MAVSDK_SERVER_SYSTEM_ID,
            source_component_id=_SERVICE.MAVSDK_SERVER_COMPONENT_ID,
            packet_sequence=packet_sequence,
            frame_encoding=_SERVICE.COMMAND_TRANSPORT_FRAME_ENCODING,
        ),
        "command": command,
        "confirmation": 0,
        "kind": _SERVICE.COMMAND_AUDIT_TRANSPORT_KIND,
        "seq": seq,
        "source_component_id": _SERVICE.MAVSDK_SERVER_COMPONENT_ID,
        "source_system_id": _SERVICE.MAVSDK_SERVER_SYSTEM_ID,
        "target_component_id": 1,
        "target_system_id": 1,
        "timestamp_ns": timestamp_ns,
        "wire_type": _SERVICE.COMMAND_AUDIT_WIRE_LONG,
    }
    if overrides is not None:
        record.update(overrides)
    return record


def _command_audit_ack_ingress(
    *,
    result: int = _SERVICE.MAV_RESULT_ACCEPTED,
    seq: int = 2,
    timestamp_ns: int = 101,
    progress: int = 255,
    command: int = 22,
    source_system_id: int = 1,
    source_component_id: int = 1,
    target_system_id: int = _SERVICE.MAVSDK_SERVER_SYSTEM_ID,
    target_component_id: int = _SERVICE.MAVSDK_SERVER_COMPONENT_ID,
    packet_sequence: int = 8,
    result_param2: int = 0,
    signature: bytes = b"",
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = bytearray(10)
    payload[0:2] = command.to_bytes(2, "little")
    payload[2] = result
    payload[3] = progress
    payload[4:8] = result_param2.to_bytes(4, "little", signed=True)
    payload[8] = target_system_id
    payload[9] = target_component_id
    record: dict[str, object] = {
        **_frame_fields(
            message_id=_SERVICE.MAVLINK_COMMAND_ACK_MESSAGE_ID,
            payload=bytes(payload),
            source_system_id=source_system_id,
            source_component_id=source_component_id,
            packet_sequence=packet_sequence,
            frame_encoding=_SERVICE.COMMAND_ACK_FRAME_ENCODING,
            signature=signature,
        ),
        "capture_boundary": _SERVICE.COMMAND_ACK_CAPTURE_BOUNDARY,
        "command": command,
        "kind": _SERVICE.COMMAND_AUDIT_ACK_INGRESS_KIND,
        "progress": progress,
        "result": result,
        "result_param2": result_param2,
        "seq": seq,
        "source_component_id": source_component_id,
        "source_system_id": source_system_id,
        "target_component_id": target_component_id,
        "target_system_id": target_system_id,
        "timestamp_ns": timestamp_ns,
    }
    if overrides is not None:
        record.update(overrides)
    return record


def _command_audit_ack_disposition(
    *,
    ack_ingress_seq: int = 2,
    command: int = 22,
    seq: int = 3,
    timestamp_ns: int = 102,
    status: str = _SERVICE.COMMAND_AUDIT_MATCHED_DISPOSITION,
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "ack_ingress_seq": ack_ingress_seq,
        "command": command,
        "kind": _SERVICE.COMMAND_AUDIT_ACK_DISPOSITION_KIND,
        "seq": seq,
        "status": status,
        "timestamp_ns": timestamp_ns,
    }
    if overrides is not None:
        record.update(overrides)
    return record


def _accepted_transaction() -> tuple[dict[str, object], ...]:
    return (
        _command_audit_transport(),
        _command_audit_ack_ingress(),
        _command_audit_ack_disposition(),
    )


def _write_lines(path: Path, records: tuple[dict[str, object], ...]) -> None:
    path.write_bytes(
        b"".join(_SERVICE._canonical_json(record) + b"\n" for record in records)
    )


def test_command_audit_reader_rejects_duplicate_noncanonical_and_wrong_types(
    tmp_path: Path,
) -> None:
    canonical = _SERVICE._canonical_json(_command_audit_transport()) + b"\n"
    cases = (
        (
            canonical.replace(b'"command":22', b'"command":22,"command":22', 1),
            "strict",
        ),
        (b" " + canonical, "canonical"),
        (canonical.replace(b'"seq":1', b'"seq":"1"', 1), "must be an integer"),
    )
    for index, (line, message) in enumerate(cases):
        path = tmp_path / f"command-audit-{index}.jsonl"
        path.write_bytes(line)
        cursor = _SERVICE.CommandAuditJournalCursor(path=path)
        with pytest.raises(_SERVICE.Px4ServiceError, match=message):
            _SERVICE._read_command_audit_records(cursor)
        assert cursor.offset == 0
        assert cursor.last_seq == 0
        assert cursor.last_timestamp_ns == 0
        assert cursor.trailing_bytes == b""


def test_command_audit_reader_detects_truncation_and_replacement(
    tmp_path: Path,
) -> None:
    path = tmp_path / "command-audit.jsonl"
    _write_lines(path, _accepted_transaction())
    cursor = _SERVICE.CommandAuditJournalCursor(path=path)
    assert len(_SERVICE._read_command_audit_records(cursor)) == 3

    _write_lines(path, (_command_audit_transport(),))
    with pytest.raises(_SERVICE.Px4ServiceError, match="truncated"):
        _SERVICE._read_command_audit_records(cursor)

    replacement_path = tmp_path / "replacement.jsonl"
    _write_lines(replacement_path, _accepted_transaction())
    cursor = _SERVICE.CommandAuditJournalCursor(path=path)
    assert len(_SERVICE._read_command_audit_records(cursor)) == 1
    replacement_path.replace(path)
    with pytest.raises(_SERVICE.Px4ServiceError, match="replaced"):
        _SERVICE._read_command_audit_records(cursor)


def test_command_audit_reader_advances_offset_across_split_trailing_record(
    tmp_path: Path,
) -> None:
    path = tmp_path / "command-audit.jsonl"
    accepted = _accepted_transaction()
    trailing_line = (
        _SERVICE._canonical_json(
            _command_audit_transport(seq=4, timestamp_ns=103, packet_sequence=9)
        )
        + b"\n"
    )
    split_at = len(trailing_line) // 2
    _write_lines(path, accepted)
    with path.open("ab") as stream:
        stream.write(trailing_line[:split_at])
    cursor = _SERVICE.CommandAuditJournalCursor(path=path)

    first = _SERVICE._read_command_audit_records(cursor)
    assert first == accepted
    assert cursor.offset == path.stat().st_size
    assert cursor.last_seq == 3
    assert cursor.last_timestamp_ns == 102
    assert cursor.trailing_bytes == trailing_line[:split_at]

    with path.open("ab") as stream:
        stream.write(trailing_line[split_at:])

    second = _SERVICE._read_command_audit_records(cursor)
    assert second == (
        _command_audit_transport(seq=4, timestamp_ns=103, packet_sequence=9),
    )
    assert cursor.offset == path.stat().st_size
    assert cursor.last_seq == 4
    assert cursor.last_timestamp_ns == 103
    assert cursor.trailing_bytes == b""


def test_command_audit_reader_rejects_short_read_without_mutating_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "command-audit.jsonl"
    _write_lines(path, (_command_audit_transport(),))
    cursor = _SERVICE.CommandAuditJournalCursor(path=path)

    def _short_read(_fd: int, *, length: int) -> bytes:
        return b"x" * (length - 1)

    monkeypatch.setattr(_SERVICE, "_read_fd_exact", _short_read)
    with pytest.raises(_SERVICE.Px4ServiceError, match="short read"):
        _SERVICE._read_command_audit_records(cursor)
    assert cursor.offset == 0
    assert cursor.last_seq == 0
    assert cursor.last_timestamp_ns == 0
    assert cursor.trailing_bytes == b""


def test_command_audit_reader_rejects_sequence_gaps_and_frame_tampering(
    tmp_path: Path,
) -> None:
    gap_path = tmp_path / "gap.jsonl"
    _write_lines(
        gap_path,
        (
            _command_audit_transport(),
            _command_audit_ack_ingress(seq=3, timestamp_ns=103),
        ),
    )
    with pytest.raises(_SERVICE.Px4ServiceError, match="not contiguous"):
        _SERVICE._read_command_audit_records(
            _SERVICE.CommandAuditJournalCursor(path=gap_path)
        )

    tampered = _command_audit_ack_ingress()
    tampered["result"] = _SERVICE.MAV_RESULT_FAILED
    tampered_path = tmp_path / "tampered.jsonl"
    _write_lines(tampered_path, (_command_audit_transport(), tampered))
    with pytest.raises(_SERVICE.Px4ServiceError, match="payload is inconsistent"):
        _SERVICE._read_command_audit_records(
            _SERVICE.CommandAuditJournalCursor(path=tampered_path)
        )


def test_command_audit_reader_binds_mavlink_v2_signature_identity(
    tmp_path: Path,
) -> None:
    signature = bytes(range(_SERVICE.MAVLINK_SIGNATURE_LENGTH))
    ingress = _command_audit_ack_ingress(signature=signature)
    valid_path = tmp_path / "signed.jsonl"
    _write_lines(
        valid_path,
        (_command_audit_transport(), ingress, _command_audit_ack_disposition()),
    )
    assert len(
        _SERVICE._read_command_audit_records(
            _SERVICE.CommandAuditJournalCursor(path=valid_path)
        )
    ) == 3

    tampered = dict(ingress)
    tampered["signature_hex"] = (b"\xff" + signature[1:]).hex()
    tampered_path = tmp_path / "tampered-signature.jsonl"
    _write_lines(tampered_path, (_command_audit_transport(), tampered))
    with pytest.raises(_SERVICE.Px4ServiceError, match="signature_hex is inconsistent"):
        _SERVICE._read_command_audit_records(
            _SERVICE.CommandAuditJournalCursor(path=tampered_path)
        )


def test_command_audit_transactions_retain_but_do_not_match_unmatched_ingress() -> None:
    unmatched_ingress = _command_audit_ack_ingress(
        command=400,
        seq=2,
        timestamp_ns=101,
        packet_sequence=9,
    )
    unmatched_disposition = _command_audit_ack_disposition(
        ack_ingress_seq=2,
        command=400,
        seq=3,
        timestamp_ns=102,
        status=_SERVICE.COMMAND_AUDIT_UNMATCHED_DISPOSITION,
    )
    matched_ingress = _command_audit_ack_ingress(
        seq=4,
        timestamp_ns=103,
        packet_sequence=10,
    )
    matched_disposition = _command_audit_ack_disposition(
        ack_ingress_seq=4,
        seq=5,
        timestamp_ns=104,
    )
    records = (
        _command_audit_transport(),
        unmatched_ingress,
        unmatched_disposition,
        matched_ingress,
        matched_disposition,
    )

    completed, active = _SERVICE._command_audit_transactions(records)

    assert active is None
    assert len(completed) == 1
    assert completed[0].records == (
        records[0],
        matched_ingress,
        matched_disposition,
    )
    assert completed[0].terminal_ack_ingress_record == matched_ingress
    assert completed[0].terminal_ack_disposition_record == matched_disposition


def test_command_audit_transactions_accept_mavlink_ack_target_wildcards() -> None:
    wildcard_ingress = _command_audit_ack_ingress(
        target_system_id=0,
        target_component_id=0,
    )
    records = (
        _command_audit_transport(),
        wildcard_ingress,
        _command_audit_ack_disposition(),
    )

    completed, active = _SERVICE._command_audit_transactions(records)

    assert active is None
    assert len(completed) == 1
    assert completed[0].records == records
    assert completed[0].terminal_ack_ingress_record == wildcard_ingress


def test_command_audit_journal_path_is_process_scoped_and_rejects_reuse(
    tmp_path: Path,
) -> None:
    first = _SERVICE.allocate_mavsdk_command_audit_journal_path(
        tmp_path,
        "uav.1",
        process_generation=1,
    )
    second = _SERVICE.allocate_mavsdk_command_audit_journal_path(
        tmp_path,
        "uav.1",
        process_generation=2,
    )
    assert first != second
    assert first.parent.name == "process-1"
    assert second.parent.name == "process-2"

    first.write_bytes(b"")
    with pytest.raises(_SERVICE.Px4ServiceError, match="must not preexist"):
        _SERVICE.allocate_mavsdk_command_audit_journal_path(
            tmp_path,
            "uav.1",
            process_generation=1,
        )


def test_command_audit_journal_path_rejects_symlink_and_nonregular_parents(
    tmp_path: Path,
) -> None:
    symlink_path = _SERVICE.mavsdk_command_audit_journal_path(
        tmp_path,
        "uav.2",
        process_generation=1,
    )
    symlink_path.parent.mkdir(parents=True)
    symlink_path.symlink_to(tmp_path / "target.jsonl")
    with pytest.raises(_SERVICE.Px4ServiceError, match="must not preexist"):
        _SERVICE.allocate_mavsdk_command_audit_journal_path(
            tmp_path,
            "uav.2",
            process_generation=1,
        )

    blocked_root = tmp_path / "blocked"
    blocked_root.write_text("not-a-directory", encoding="utf-8")
    with pytest.raises(_SERVICE.Px4ServiceError, match="real directory"):
        _SERVICE.allocate_mavsdk_command_audit_journal_path(
            blocked_root,
            "uav.3",
            process_generation=1,
        )


def test_pinned_mavsdk_patch_records_only_transport_acceptance_and_decoded_ingress() -> None:
    patch_path = (
        _ROOT
        / "containers"
        / "px4-gazebo"
        / "mavsdk-incoming-heartbeat-timeout.patch"
    )
    dockerfile_path = _ROOT / "containers" / "px4-gazebo" / "Dockerfile"
    identity_path = _ROOT / "containers" / "px4-gazebo" / "identity.json"
    patch = patch_path.read_text(encoding="utf-8")
    dockerfile = dockerfile_path.read_text(encoding="utf-8")
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    patch_digest = sha256(patch_path.read_bytes()).hexdigest()

    transport_success_gate = patch.index("if (successful_emissions == 0)")
    transport_audit = patch.index(
        "!MavlinkCommandSender::audit_command_transport_accept(message, successful_emissions)"
    )
    ingress_audit = patch.index(
        "append_ack_ingress_record(message, command_ack)"
    )
    target_filter = patch.index("if ((command_ack.target_system &&")

    assert transport_success_gate < transport_audit
    assert "} else if (" in patch[transport_success_gate:transport_audit]
    assert ingress_audit < target_filter
    assert "append_send_record" not in patch
    assert "append_ack_record" not in patch
    assert "mavlink_msg_to_send_buffer_from_decoded_message" in patch
    assert "unmatched_no_outstanding_command" in patch
    assert "ARG MAVSDK_VERSION=3.17.2" in dockerfile
    assert "ARG MAVSDK_COMMIT=9e3ca17faa84aa868caea10a3bbdab7e53810ced" in dockerfile
    assert f"ARG MAVSDK_PATCH_SHA256={patch_digest}" in dockerfile
    assert identity["mavsdk"]["patch_sha256"] == patch_digest
    assert identity["mavsdk_server_sha256"] == (
        "6f7978e40127849d925cfc5b3181b4fd3e3bb2b3701d5fb591e92d8710e1deee"
    )
