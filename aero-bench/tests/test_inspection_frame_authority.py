"""Frame-authority unit events, not an accepted sensor or formal run."""

from types import SimpleNamespace

import pytest

from aero_bench.tasks.inspection.formal_v2_sealed import _public_frame_events


def _record(event_type, frame_id="frame.unit"):
    return SimpleNamespace(
        event=SimpleNamespace(
            event_type=event_type,
            frame_id=frame_id,
            interaction=SimpleNamespace(interaction_type="sensor.frame_ref.v2"),
        )
    )


def test_private_audit_and_public_capture_are_one_public_authority():
    audit = _record("provider.event.validated")
    public = _record("public.sensor-frame")
    assert _public_frame_events((audit, public)) == {"frame.unit": public.event}


def test_two_public_records_for_the_same_frame_are_rejected():
    with pytest.raises(ValueError, match="repeated RunEvent authority"):
        _public_frame_events(
            (_record("public.sensor-frame"), _record("public.sensor-frame"))
        )


def test_distinct_public_frames_keep_their_own_authority():
    first, second = (
        _record("public.sensor-frame", "frame.1"),
        _record("public.sensor-frame", "frame.2"),
    )
    assert _public_frame_events((first, second)) == {
        "frame.1": first.event,
        "frame.2": second.event,
    }
