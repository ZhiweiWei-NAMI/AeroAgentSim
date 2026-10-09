"""Exercise actual per-stream service, lateness edges and engine-free replay.

Run from the worktree: PYTHONPATH=src python scenarios/realtime-streams-demo.py
--out /tmp/stream-demo. The YAML's bootstrap inputs are replaced with
explicit host submissions for this live experiment.
"""

from __future__ import annotations

import argparse
import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from aerokernel import CommandRequest, IngressStream, Instant, Stamp
from aerokernel.journal import replay

from aeroagentsim.platform import RunSession
from aeroagentsim.scenario import load_scenario


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    authored = load_scenario(Path(__file__).with_name("realtime-streams.yaml"))
    document = copy.deepcopy(authored.document)
    document["registry"]["snapshot"] = str(
        authored.base / document["registry"]["snapshot"]
    )
    document["bindings"]["commands"] = []
    for declaration in document["ingress_streams"]:
        declaration["initial_watermark_ns"] = 0
    scenario = load_scenario(document)
    fast, slow = scenario.ingress_streams
    allowance = fast.policy.allowed_lateness_ns
    if allowance is None or allowance <= 0:
        raise ValueError("demo requires a positive fast-stream allowed_lateness_ns")
    mappings = {
        mapping.mapping_id: mapping for mapping in scenario.clock_mappings or ()
    }

    def stamp(stream: IngressStream, ns: int) -> Stamp:
        mapping = mappings[stream.mapping_id]
        return Stamp(
            mapping.clock_id,
            (ns - mapping.offset_ns) * mapping.q,
            mapping.p,
            mapping.mapping_id,
        )

    def command(
        stream: IngressStream, ns: int, value: float, key: str
    ) -> CommandRequest:
        engine_id = stream.engine_ids[0]
        config = scenario.engines[engine_id]["config"]
        return CommandRequest(
            config["command"], engine_id, Instant(ns), {"value": value}, key
        )

    boundary = 50_000_000
    with RunSession(scenario, args.out) as session:
        session.start()
        fast_receipt = session.submit_live(
            fast.engine_ids[0],
            command(fast, boundary, 21.5, "fast-first"),
            stamp(fast, boundary),
            stream_id=fast.id,
        )
        slow_receipt = session.submit_live(
            slow.engine_ids[0],
            command(slow, boundary, 18.0, "slow-first"),
            stamp(slow, boundary),
            stream_id=slow.id,
        )
        assert fast_receipt.command_id is not None
        assert slow_receipt.command_id is not None
        session.advance_source_progress(
            stamp(fast, boundary + allowance), stream_id=fast.id
        )
        kernel = session.simulation.kernel
        with ThreadPoolExecutor() as pool:
            advancing = pool.submit(session.run_until, boundary)
            try:
                deadline = time.monotonic() + 3
                while kernel.action(fast_receipt.command_id).status != "succeeded":
                    if advancing.done():
                        advancing.result()
                        raise RuntimeError("common seal completed before slow closure")
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "fast stream did not process its actual input"
                        )
                    time.sleep(0.005)
                partial = {
                    "fast_action": kernel.action(fast_receipt.command_id).status,
                    "slow_action": kernel.action(slow_receipt.command_id).status,
                    "fast_result": dict(
                        kernel.action(fast_receipt.command_id).history[-1]["result"]
                    ),
                    "watermarks": dict(kernel.ingress_watermarks),
                }
                assert partial["slow_action"] == "pending"
                assert partial["fast_result"] == {"value": 21.5}
                assert not advancing.done()
                assert replay(kernel.journal.bytes).incomplete
                tail = session.submit_live(
                    fast.engine_ids[0],
                    command(fast, boundary + 1, 22.0, "tail"),
                    stamp(fast, boundary + 1),
                    stream_id=fast.id,
                )
                outside = session.submit_live(
                    fast.engine_ids[0],
                    command(fast, boundary, 23.0, "outside"),
                    stamp(fast, boundary),
                    stream_id=fast.id,
                )
                assert tail.disposition == "accepted"
                assert (
                    outside.disposition == "rejected" and outside.code == "LATE_INGRESS"
                )
            finally:
                session.advance_watermark(scenario.until_ns, stream_id=slow.id)
            advancing.result(timeout=3)
        session.advance_watermark(scenario.until_ns, stream_id=fast.id)
        session.run()
        before = kernel.records
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
            restored = replay(args.out / "journal.jsonl")
        assert restored.records == before and not restored.incomplete
        assert restored.ingress_receipt(fast_receipt.command_id) == fast_receipt
        print(
            json.dumps(
                {
                    "before_slow_closure": partial,
                    "tail": tail.disposition,
                    "closed_endpoint": outside.disposition,
                    "seal_ns": restored.view().instant.ns,
                    "exact_replay": restored.records == before,
                    "replay_live_calls": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
