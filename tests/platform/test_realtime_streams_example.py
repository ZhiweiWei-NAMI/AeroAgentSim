"""Exercise the shipped two-source scenario through CLI and worker HTTP."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from aerokernel.compact import expand_record
from aerokernel.errors import KernelError
from aerokernel.journal import iter_records, read_records, replay
from fastapi.testclient import TestClient

from aeroagentsim.platform import RunSession
from aeroagentsim.scenario import ScenarioError, load_scenario
from aeroagentsim.services.app import create_app
from aeroagentsim.services.cli import main


@pytest.fixture
def streams() -> dict[str, Any]:
    authored = load_scenario(Path("scenarios/realtime-streams.yaml"))
    document = copy.deepcopy(authored.document)
    document["registry"]["snapshot"] = str(
        authored.base / document["registry"]["snapshot"]
    )
    document["bindings"]["commands"] = []
    for stream in document["ingress_streams"]:
        stream["initial_watermark_ns"] = 0
    return document


def live_body(stream_id: str, target: str, ns: int, key: str) -> dict[str, Any]:
    if stream_id == "fast-source":
        clock, mapping, numerator, denominator = "fast-clock", "fast-ns", ns, 1
    else:
        clock, mapping, numerator, denominator = (
            "slow-clock",
            "slow-ticks",
            ns - 1000,
            2,
        )
    return {
        "stream_id": stream_id,
        "schema": "aas.stream.observe",
        "target": target,
        "at_ns": ns,
        "payload": {"value": 21.5},
        "idempotency_key": key,
        "source_stamp": {
            "clock_id": clock,
            "mapping_id": mapping,
            "numerator": numerator,
            "denominator": denominator,
        },
    }


def test_two_source_cli_and_offline_replay(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with patch(
        "sys.argv",
        [
            "aeroagentsim",
            "run",
            "scenarios/realtime-streams.yaml",
            "--out",
            str(tmp_path),
        ],
    ):
        main()
    directory = Path(json.loads(capsys.readouterr().out)["run"])
    records = [
        expand_record(record) for record in iter_records(directory / "journal.jsonl")
    ]
    assert (
        records[0]["major"],
        records[0]["minor"],
        records[0]["semantic_version"],
    ) == (2, 1, 4)
    assert records[0]["provenance"] == "lean"
    assert records[0]["ingress_policy"] is None
    assert not any(record["type"] == "live_ingress" for record in records)
    with (
        patch("sys.argv", ["aeroagentsim", "replay", str(directory)]),
        patch(
            "aeroagentsim.platform.plugins.EngineCatalog.build",
            side_effect=AssertionError("factory called"),
        ),
        patch(
            "aeroagentsim.engines.ingress_consumer.IngressConsumer.on_inputs",
            side_effect=AssertionError("engine called"),
        ),
        patch("time.monotonic", side_effect=AssertionError("clock called")),
        patch("time.perf_counter", side_effect=AssertionError("clock called")),
    ):
        main()
    result = json.loads(capsys.readouterr().out)
    assert result["ns"] == "300000000" and not result["incomplete"]


def test_cli_provenance_flag_overrides_scenario_header(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI --provenance override reaches the kernel header without a demo rerun."""
    with patch(
        "sys.argv",
        [
            "aeroagentsim",
            "run",
            "scenarios/realtime-streams.yaml",
            "--out",
            str(tmp_path),
            "--provenance",
            "full",
        ],
    ):
        main()
    directory = Path(json.loads(capsys.readouterr().out)["run"])
    header = expand_record(next(iter_records(directory / "journal.jsonl")))
    assert "provenance" not in header
    assert (header["major"], header["minor"], header["semantic_version"]) == (2, 0, 3)


def test_cli_rejects_unknown_provenance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        pytest.raises(SystemExit),
        patch(
            "sys.argv",
            [
                "aeroagentsim",
                "run",
                "scenarios/realtime-streams.yaml",
                "--out",
                str(tmp_path),
                "--provenance",
                "audit",
            ],
        ),
    ):
        main()
    capsys.readouterr()


def test_two_source_http_partial_service_lateness_and_replay(
    streams: dict[str, Any], tmp_path: Path
) -> None:
    with TestClient(create_app(tmp_path / "runs")) as client:
        created = client.post("/v1/runs", json=streams)
        assert created.status_code == 201, created.text
        run_id = created.json()["id"]
        url = f"/v1/runs/{run_id}"
        directory = tmp_path / "runs" / run_id
        fast = live_body("fast-source", "fast", 50_000_000, "first")
        slow = live_body("slow-source", "slow", 50_000_000, "first")
        fast_response = client.post(url + "/ingress", json=fast)
        slow_response = client.post(url + "/ingress", json=slow)
        assert fast_response.status_code == slow_response.status_code == 200
        fast_receipt, slow_receipt = fast_response.json(), slow_response.json()
        assert fast_receipt["contract"] == "aeroagentsim.ingress-receipt/v2"
        assert fast_receipt["stream_id"] == "fast-source"
        assert slow_receipt["stream_id"] == "slow-source"
        assert fast_receipt["command_id"] != slow_receipt["command_id"]
        assert client.post(url + "/ingress", json=fast).json() == fast_receipt
        assert (
            client.post(
                url + "/watermark", json={"watermark_ns": 100_000_000}
            ).status_code
            == 422
        )
        wrong = client.post(
            url + "/ingress",
            json={**fast, "target": "slow", "idempotency_key": "wrong-domain"},
        )
        assert (
            wrong.status_code == 422
            and wrong.json()["detail"]["code"] == "INGRESS_BINDING"
        )
        progress = {
            "stream_id": "fast-source",
            "source_stamp": live_body("fast-source", "fast", 70_000_000, "progress")[
                "source_stamp"
            ],
        }
        closed = client.post(url + "/watermark", json=progress)
        assert closed.status_code == 200, closed.text
        assert closed.json() == {
            "contract": "aeroagentsim.watermark-receipt/v2",
            "stream_id": "fast-source",
            "watermark_ns": 50_000_000,
        }
        assert (
            client.post(
                url + "/watermark", json={**progress, "watermark_ns": 50_000_000}
            ).status_code
            == 422
        )
        deadline = time.monotonic() + 3
        while True:
            prefix = replay(directory / "journal.jsonl")
            if prefix.action(fast_receipt["command_id"]).status == "succeeded":
                break
            assert time.monotonic() < deadline
            time.sleep(0.005)
        assert prefix.action(slow_receipt["command_id"]).status == "pending"
        assert prefix.action(fast_receipt["command_id"]).history[-1]["result"] == {
            "value": 21.5
        }
        assert prefix.ingress_watermarks == {
            "fast-source": 50_000_000,
            "slow-source": 0,
        }
        assert prefix.incomplete
        tail = client.post(
            url + "/ingress", json=live_body("fast-source", "fast", 50_000_001, "tail")
        )
        assert tail.status_code == 200 and tail.json()["disposition"] == "accepted"
        beyond = live_body("fast-source", "fast", 50_000_000, "outside")
        rejected = client.post(url + "/ingress", json=beyond)
        assert rejected.status_code == 200
        assert rejected.json()["disposition"] == "rejected"
        assert rejected.json()["code"] == "LATE_INGRESS"
        assert rejected.json()["command_id"] is None
        assert client.post(url + "/ingress", json=beyond).json() == rejected.json()
        # Different mapped source progress: 2 * 174999500 + 1000 = 350000000,
        # with the slow stream's 50ms tail this closes exactly 300ms.
        slow_progress = live_body("slow-source", "slow", 350_000_000, "progress")[
            "source_stamp"
        ]
        slow_closed = client.post(
            url + "/watermark",
            json={"stream_id": "slow-source", "source_stamp": slow_progress},
        )
        assert (
            slow_closed.status_code == 200
            and slow_closed.json()["watermark_ns"] == 300_000_000
        )
        fast_closed = client.post(
            url + "/watermark",
            json={"stream_id": "fast-source", "watermark_ns": 300_000_000},
        )
        assert fast_closed.status_code == 200
        assert (
            fast_closed.json()["watermark_ns"] == 300_000_000
        )  # no second subtraction
        deadline = time.monotonic() + 3
        while True:
            status = next(
                run for run in client.get("/v1/runs").json() if run["id"] == run_id
            )["status"]
            if status == "completed":
                break
            assert status != "faulted" and time.monotonic() < deadline
            time.sleep(0.005)
        records, incomplete = read_records((directory / "journal.jsonl").read_bytes())
        assert not incomplete
        with (
            patch(
                "aeroagentsim.platform.plugins.EngineCatalog.build",
                side_effect=AssertionError("factory called"),
            ),
            patch(
                "aeroagentsim.engines.ingress_consumer.IngressConsumer.on_inputs",
                side_effect=AssertionError("engine called"),
            ),
            patch("time.monotonic", side_effect=AssertionError("clock called")),
            patch("time.perf_counter", side_effect=AssertionError("clock called")),
        ):
            restored = replay(directory / "journal.jsonl")
        assert [restored.header, *restored.records] == [
            expand_record(record) for record in records
        ]
        assert not restored.incomplete and restored.view().instant.ns == 300_000_000
        assert (
            restored.ingress_receipt(fast_receipt["command_id"]).journal_index
            == fast_receipt["journal_index"]
        )
        assert restored.action(slow_receipt["command_id"]).status == "succeeded"
        assert restored.action(tail.json()["command_id"]).status == "succeeded"


@pytest.mark.parametrize(
    "key,value,path",
    [
        ("allowed_lateness_ns", None, "allowed_lateness_ns"),
        ("allowed_lateness_ns", True, "allowed_lateness_ns"),
        ("allowed_lateness_ns", -1, "allowed_lateness_ns"),
        ("engine_ids", [], "engine_ids"),
        ("engine_ids", ["fast", "fast"], "engine_ids"),
        ("engine_ids", ["missing"], r"engine_ids\[0\]"),
        ("mapping_id", "missing", "mapping_id"),
        ("timeout_s", 0, "timeout_s"),
    ],
)
def test_named_stream_errors_have_authored_path(
    streams: dict[str, Any], key: str, value: Any, path: str
) -> None:
    streams["ingress_streams"][0][key] = value
    with pytest.raises(ScenarioError, match=r"ingress_streams\[0\]\." + path):
        load_scenario(streams)


def test_multiple_sources_binding_and_explicit_default(
    streams: dict[str, Any], tmp_path: Path
) -> None:
    additional = copy.deepcopy(streams["ingress_streams"][0])
    additional["id"] = "fast-other"
    streams["ingress_streams"].append(additional)
    with RunSession(load_scenario(streams), tmp_path / "multi") as session:
        session.start()
        simulation = session.simulation
        with pytest.raises(KernelError, match="INGRESS_STREAM"):
            simulation.resolve_stream(None, "fast")
        with pytest.raises(KernelError, match="INGRESS_STREAM"):
            simulation.resolve_stream("default")
        with pytest.raises(KernelError, match="INGRESS_BINDING"):
            simulation.resolve_stream("fast-source", "slow")
        assert simulation.resolve_stream("fast-other", "fast") == "fast-other"
        assert simulation.resolve_stream(None, "slow") == "slow-source"
    additional["id"] = "default"
    with RunSession(load_scenario(streams), tmp_path / "default") as session:
        session.start()
        assert session.simulation.resolve_stream(None) == "default"
        assert session.simulation.resolve_stream(None, "fast") == "default"
        # A declared default outside the engine domain cannot override its stream.
        assert session.simulation.resolve_stream(None, "slow") == "slow-source"


def test_explicit_duplicate_stream_reports_second_authored_path(
    streams: dict[str, Any],
) -> None:
    original = copy.deepcopy(streams["ingress_streams"][0])
    streams["ingress_streams"].append(copy.deepcopy(original))
    with pytest.raises(ScenarioError, match=r"ingress_streams\[2\]\.id.*duplicate"):
        load_scenario(streams)
    assert streams["ingress_streams"][0] == original


def test_shorthand_matches_explicit_stream_or_reports_both_paths(
    streams: dict[str, Any],
) -> None:
    explicit = streams["ingress_streams"][0]
    shorthand = {
        key: copy.deepcopy(value)
        for key, value in explicit.items()
        if key not in {"id", "engine_ids"}
    }
    shorthand["stream_id"] = explicit["id"]
    streams["engines"]["fast"]["ingress"] = shorthand
    scenario = load_scenario(streams)
    assert len(scenario.ingress_streams) == 2
    fast = next(
        stream for stream in scenario.ingress_streams if stream.id == "fast-source"
    )
    assert fast.engine_ids == ("fast",)
    shorthand["lateness"] = "delay"
    with pytest.raises(
        ScenarioError, match=r"engines\.fast\.ingress.*inconsistent"
    ) as caught:
        load_scenario(streams)
    assert "first declared at ingress_streams[0]" in str(caught.value)
