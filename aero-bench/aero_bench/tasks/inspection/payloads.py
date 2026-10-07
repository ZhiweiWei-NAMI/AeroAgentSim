from __future__ import annotations

import hashlib
import struct
from typing import Literal

from aero_bench.runtime.ledger import ledger_jsonl_bytes
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.contracts import InspectionEvidenceBundle


EvidenceKind = Literal[
    "business_state",
    "trajectory",
    "delivery",
    "observation",
    "sensor_frame",
    "camera_frame_data",
    "truth",
    "detection",
    "report",
    "theoretical_bounds",
    "event_log",
]


def evidence_payload_bytes(
    evidence: InspectionEvidenceBundle,
    kind: EvidenceKind,
) -> bytes:
    if kind == "event_log":
        return ledger_jsonl_bytes(evidence.event_ledger.records)
    if kind == "business_state":
        payload = evidence.business_state.model_dump(mode="json")
    elif kind == "theoretical_bounds":
        payload = evidence.theoretical_bounds.model_dump(mode="json")
    elif kind == "trajectory":
        payload = [item.model_dump(mode="json") for item in evidence.trajectory]
    elif kind == "delivery":
        payload = [item.model_dump(mode="json") for item in evidence.deliveries]
    elif kind == "observation":
        payload = [item.model_dump(mode="json") for item in evidence.observations]
    elif kind == "sensor_frame":
        payload = [item.model_dump(mode="json") for item in evidence.sensor_frames]
    elif kind == "camera_frame_data":
        return b"".join(
            struct.pack(">8sIQ", b"ABFRAME1", len(frame_id), len(item.png_bytes))
            + frame_id
            + item.png_bytes
            for item in evidence.camera_frames
            for frame_id in (item.frame_id.encode("ascii"),)
        )
    elif kind == "truth":
        payload = [item.model_dump(mode="json") for item in evidence.truth]
    elif kind == "detection":
        payload = [item.model_dump(mode="json") for item in evidence.detections]
    elif kind == "report":
        payload = [item.model_dump(mode="json") for item in evidence.report]
    else:
        raise ValueError(f"unsupported evidence payload kind: {kind}")
    return canonical_json_bytes(payload)


def evidence_payload_digest(
    evidence: InspectionEvidenceBundle,
    kind: EvidenceKind,
) -> str:
    return hashlib.sha256(evidence_payload_bytes(evidence, kind)).hexdigest()


__all__ = [
    "EvidenceKind",
    "evidence_payload_bytes",
    "evidence_payload_digest",
]
