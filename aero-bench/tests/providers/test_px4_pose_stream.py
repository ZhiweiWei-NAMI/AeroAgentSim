from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aero_bench.providers import rpc as _RPC


_SERVICE_PATH = Path(__file__).parents[2] / "containers" / "px4-gazebo" / "service.py"
_SPEC = importlib.util.spec_from_file_location(
    "aero_bench_px4_gazebo_pose_stream_service", _SERVICE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_SERVICE = importlib.util.module_from_spec(_SPEC)
sys.modules["rpc"] = _RPC
sys.modules[_SPEC.name] = _SERVICE
_SPEC.loader.exec_module(_SERVICE)

_MODEL_NAMES = frozenset({"uav.01", "uav.02"})


def _pose(name: object, *, x: float = 0.0, geometry: bool = True) -> object:
    return SimpleNamespace(
        name=name,
        position=SimpleNamespace(x=x, y=1.0, z=2.0) if geometry else None,
        orientation=(
            SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0)
            if geometry
            else None
        ),
    )


def _message(*poses: object) -> object:
    return SimpleNamespace(pose=poses)


def test_pose_callback_selects_only_configured_uav_models(tmp_path: Path) -> None:
    stack = _SERVICE.RealPx4Stack(tmp_dir=tmp_path)
    stack._config = SimpleNamespace(
        vehicles=(
            {"gazebo_model_name": "uav.01"},
            {"gazebo_model_name": "uav.02"},
        )
    )
    stack._pose_topic_active = True

    stack._on_pose_topic_message(
        _message(
            _pose("uav.01", x=-320.0),
            _pose("base_link", geometry=False),
            _pose("rotor_0", geometry=False),
            _pose("uav.02", x=300.0),
            _pose("base_link", geometry=False),
            _pose("rotor_0", geometry=False),
            _pose("rotor_1", geometry=False),
        )
    )

    assert stack._pose_topic_failure is None
    assert stack._pose_topic_latest is not None
    assert set(stack._pose_topic_latest) == _MODEL_NAMES
    assert stack._pose_topic_latest["uav.01"]["x_m"] == -320.0
    assert stack._pose_topic_latest["uav.02"]["x_m"] == 300.0
    assert stack._pose_topic_version == 1


def test_pose_parser_rejects_duplicate_selected_model() -> None:
    with pytest.raises(_SERVICE.Px4ServiceError, match="repeated model name 'uav.01'"):
        _SERVICE.RealPx4Stack._parse_pose_message(
            _message(_pose("uav.01"), _pose("uav.01")),
            model_names=_MODEL_NAMES,
        )


def test_pose_parser_rejects_malformed_selected_geometry() -> None:
    with pytest.raises(_SERVICE.Px4ServiceError, match=r"pose\[0\] geometry"):
        _SERVICE.RealPx4Stack._parse_pose_message(
            _message(_pose("uav.01", geometry=False), _pose("uav.02")),
            model_names=_MODEL_NAMES,
        )


def test_pose_parser_does_not_fabricate_missing_selected_model() -> None:
    poses = _SERVICE.RealPx4Stack._parse_pose_message(
        _message(
            _pose("uav.01"),
            _pose("base_link", geometry=False),
            _pose("base_link", geometry=False),
        ),
        model_names=_MODEL_NAMES,
    )

    assert set(poses) == {"uav.01"}
    assert "uav.02" not in poses


def test_pose_parser_still_requires_every_entry_name() -> None:
    with pytest.raises(_SERVICE.Px4ServiceError, match=r"pose\[0\]\.name"):
        _SERVICE.RealPx4Stack._parse_pose_message(
            _message(_pose(None, geometry=False)),
            model_names=_MODEL_NAMES,
        )


def test_startup_progress_is_bounded_public_json(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_SERVICE.time, "monotonic", lambda: 12.5)

    _SERVICE._startup_progress(
        "stack-reset.warmup-begin",
        started_at=10.0,
        detail={"iterations": 2500},
    )

    assert json.loads(capsys.readouterr().err) == {
        "detail": {"iterations": 2500},
        "elapsed_ms": 2500,
        "kind": "px4-startup-progress",
        "stage": "stack-reset.warmup-begin",
    }
