"""Host pacing gates actual kernel commits without changing the scientific run."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest
from aerokernel.journal import replay

from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.platform.simulation import RunSession
from aeroagentsim.scenario import Scenario, load_scenario
from aeroagentsim.services.feed_index import kpi_result, page
from aeroagentsim.services.storage import RunStorage
from aeroagentsim.services.worker import execute


def wait_for(predicate: Any, timeout: float = 60) -> Any:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.005)
    raise AssertionError("Worker did not acknowledge the requested state")


def scenario(template: str, until_ns: int, *, subsecond: bool = False) -> Scenario:
    prepared = TemplateRegistry().apply(template, {})
    prepared.document["run"]["until_ns"] = until_ns
    if subsecond:
        # An explicitly authored 100 ms motion step, identical at every speed.
        # Production template physics settings are never rewritten by pacing.
        prepared.document["engines"]["motion"]["config"]["step_ns"] = 100_000_000
    return load_scenario(prepared.document, base=prepared.base)


@contextmanager
def worker(source: Scenario, directory: Path, factor: float) -> Iterator[dict[str, Any]]:
    context = multiprocessing.get_context("fork")
    paused, stopped, started = (context.Event() for _ in range(3))
    speed = context.Value("d", factor)
    RunStorage(directory).prepare(source)
    process = context.Process(
        target=execute,
        args=(source, directory, paused, stopped, None, started, speed),
    )
    process.start()
    storage = RunStorage(directory)

    def status() -> str:
        return storage.metadata()["status"]

    def head() -> int:
        storage.refresh_committed()
        if not storage.entries:
            return 0
        return storage.records(storage.entries[-1]["index"], 1)[0]["instant"]["fields"]["ns"]

    try:
        wait_for(lambda: status() in {"initialized", "faulted"})
        assert status() == "initialized", storage.metadata()
        yield {"paused": paused, "stopped": stopped, "started": started,
               "speed": speed, "process": process, "status": status,
               "head": head, "storage": storage}
    finally:
        stopped.set()
        paused.clear()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
        assert process.exitcode == 0


@pytest.mark.parametrize("factor", [0.05, 0.5, 1, 5, 50, 1000])
def test_committed_time_never_leads_wall_budget(tmp_path: Path, factor: float) -> None:
    source = scenario("food-delivery", 2_000_000_000, subsecond=True)
    with worker(source, tmp_path / "paced", factor) as active:
        started = time.perf_counter()
        active["started"].set()
        heads = [0]
        # Simulated time that elapsed between the two samples observing each new
        # head. A descheduled poller can miss intermediate publications; that
        # sampling delay must not be blamed on the worker's publication cadence.
        windows: list[float] = []
        previous = started
        deadline = started + (2.25 if factor == 0.05 else 2 / factor + 1)
        while time.perf_counter() < deadline:
            # Completion is published after its committed prefix. Observe it
            # before reading that prefix: a newer status must not end sampling
            # using a head read just before the worker's final publication.
            status = active["status"]()
            head = active["head"]()
            observed = time.perf_counter()
            assert head / 1e9 <= (observed - started) * factor
            if head != heads[-1]:
                heads.append(head)
                windows.append((observed - previous) * factor * 1e9)
            previous = observed
            if status == "completed":
                break
            time.sleep(0.005)
        assert len(heads) > 1, "Low-speed motion must publish before the 10 s run limit"
        if factor <= 1:
            assert all(
                0 < b - a <= 100_000_000 + window
                for a, b, window in zip(heads, heads[1:], windows)
            )
        if factor == 0.05:
            assert heads[-1] == 100_000_000
        else:
            assert wait_for(lambda: active["status"]() == "completed")


@pytest.mark.parametrize("template,until_ns", [("food-delivery", 60_000_000_000), ("city-day", 20_000_000_000)])
def test_template_journal_and_kpis_identical_across_speed_and_pause(
    tmp_path: Path, template: str, until_ns: int
) -> None:
    source = scenario(template, until_ns)
    paths = []
    for name, factor in [("max", 0), ("five", 5), ("changes", 5)]:
        directory = tmp_path / name
        paths.append(directory)
        with worker(source, directory, factor) as active:
            active["started"].set()
            if name == "changes":
                wait_for(lambda: active["head"]() > 0)
                active["speed"].value = 0.5
                active["paused"].set()
                wait_for(lambda: active["status"]() == "paused")
                paused_bytes = (directory / "journal.jsonl").read_bytes()
                time.sleep(0.05)
                assert (directory / "journal.jsonl").read_bytes() == paused_bytes
                active["speed"].value = 50
                active["paused"].clear()
                wait_for(lambda: active["status"]() == "running")
                active["speed"].value = 2
                time.sleep(0.03)
                active["speed"].value = 0
            wait_for(lambda: active["status"]() in {"completed", "faulted"})
            assert active["status"]() == "completed", active["storage"].metadata()
    # Headless execution remains the same schedule and the same source document.
    headless = tmp_path / "headless"
    paths.append(headless)
    with RunSession(source, headless) as session:
        session.run()
    journals = [(path / "journal.jsonl").read_bytes() for path in paths]
    assert len({hashlib.sha256(data).hexdigest() for data in journals}) == 1
    assert all(data == journals[0] for data in journals[1:])
    # One replay validates the records; identical bytes prove the other three
    # contain exactly those records without repeating the expensive rebuild.
    replay(paths[0] / "journal.jsonl")
    results = [kpi_result(path, include_series=False) for path in paths]
    from aerokernel.values import canonical_json

    assert len({canonical_json(result) for result in results}) == 1


def test_slowdown_does_not_spend_a_high_speed_backlog(tmp_path: Path) -> None:
    with worker(scenario("food-delivery", 120_000_000_000), tmp_path / "slowdown", 1000) as active:
        active["started"].set()
        wait_for(lambda: active["head"]() > 0)
        active["speed"].value = 0.05
        # Let the already reserved atomic wave finish; subsequent boundaries
        # must use the new rate instead of old, unspent 1000x wall credit.
        time.sleep(0.05)
        before = active["head"]()
        time.sleep(0.15)
        assert active["head"]() - before <= 1_000_000_000
        assert active["status"]() == "running"


@pytest.mark.parametrize("template", ["food-delivery", "city-day"])
def test_max_speed_controls_are_acknowledged_at_natural_boundaries(
    tmp_path: Path, template: str
) -> None:
    with worker(scenario(template, 120_000_000_000), tmp_path / "controls", 0) as active:
        active["started"].set()
        wait_for(lambda: active["head"]() > 0)
        requested = time.perf_counter()
        active["paused"].set()
        wait_for(lambda: active["status"]() == "paused", timeout=1)
        assert time.perf_counter() - requested < 0.25
        paused_head = active["head"]()
        active["speed"].value = 0.05
        requested = time.perf_counter()
        active["paused"].clear()
        wait_for(lambda: active["status"]() == "running", timeout=1)
        assert time.perf_counter() - requested < 0.25
        time.sleep(0.05)
        assert active["head"]() == paused_head
        active["speed"].value = 0
        wait_for(lambda: active["head"]() > paused_head)
        requested = time.perf_counter()
        active["stopped"].set()
        # Physical advancement stops independently of final resource cleanup.
        time.sleep(0.2)
        stopped_head = active["head"]()
        time.sleep(0.05)
        assert active["head"]() == stopped_head
        wait_for(lambda: active["status"]() == "stopped", timeout=1)
        assert time.perf_counter() - requested < 1
        replay(tmp_path / "controls" / "journal.jsonl")


def test_worker_collects_its_own_cycles_without_finalizing_inherited_host_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gc
    import os

    from aerokernel.coordinator import Kernel

    finalized = tmp_path / "finalized.jsonl"

    class Cycle:
        def __init__(self, owner: str) -> None:
            self.owner = owner
            self.link = self

        def __del__(self) -> None:
            with finalized.open("a") as stream:
                stream.write(json.dumps({"owner": self.owner, "pid": os.getpid()}) + "\n")

    original = Kernel.run_until

    def collect_owned(*args: Any, **kwargs: Any) -> Any:
        cycle = Cycle("worker")
        del cycle
        gc.collect()
        assert gc.isenabled(), "New simulation cycles must remain collectable"
        return original(*args, **kwargs)

    monkeypatch.setattr(Kernel, "run_until", collect_owned)
    enabled = gc.isenabled()
    gc.disable()
    abandoned = Cycle("host")
    del abandoned
    try:
        with worker(scenario("food-delivery", 1_000_000_000), tmp_path / "owned-heap", 0) as active:
            active["started"].set()
            wait_for(lambda: active["status"]() in {"completed", "faulted"})
            assert active["status"]() == "completed", active["storage"].metadata()
            worker_pid = active["process"].pid
        rows = [json.loads(line) for line in finalized.read_text().splitlines()]
        assert rows and all(row == {"owner": "worker", "pid": worker_pid} for row in rows)
        gc.collect()
        rows = [json.loads(line) for line in finalized.read_text().splitlines()]
        assert {"owner": "host", "pid": os.getpid()} in rows
    finally:
        if enabled:
            gc.enable()


def test_city_controls_remain_responsive_with_a_retained_host_heap(tmp_path: Path) -> None:
    # A long-lived HTTP host retains viewer/index graphs from earlier runs.
    # These belong to the host, not each new simulation's cyclic collector.
    retained = [[{"index": index}] for index in range(250_000)]
    test_max_speed_controls_are_acknowledged_at_natural_boundaries(tmp_path, "city-day")
    assert len(retained) == 250_000


def test_live_projection_reads_only_acknowledged_prefix_and_keeps_offsets_private(tmp_path: Path) -> None:
    source = scenario("food-delivery", 2_000_000_000, subsecond=True)
    directory = tmp_path / "feed"
    with worker(source, directory, 1) as active:
        initial_index = (directory / "index.json").read_bytes()
        active["started"].set()
        wait_for(lambda: active["head"]() >= 300_000_000)
        cuts = page(directory, 1, 4096)
        assert cuts[-1]["at"]["ns"] != "0"
        assert int(cuts[-1]["at"]["ns"]) <= active["head"]()
        assert (directory / "index.json").read_bytes() == initial_index
        active["paused"].set()
        wait_for(lambda: active["status"]() == "paused")
        actual = active["storage"]
        actual.refresh_committed()
        paused_cuts = page(directory, 1, 4096)
        acknowledged = (directory / "journal.jsonl").read_bytes()
        retained_index = (directory / "index.json").read_bytes()
        # A full, syntactically valid extra line is still unacknowledged.
        last = json.loads(acknowledged.splitlines()[-1])
        last["index"] += 1
        with (directory / "journal.jsonl").open("ab") as stream:
            stream.write(json.dumps(last).encode() + b"\n")
        try:
            assert page(directory, 1, 4096) == paused_cuts
            reader = RunStorage(directory)
            reader.refresh_committed()
            assert reader.offset == len(acknowledged)
            # Even an offsets file ahead of the advertised live coordinate
            # must not make a newly connected reader release future records.
            RunStorage(directory).index()
            new_reader = RunStorage(directory)
            new_reader.refresh_committed()
            assert new_reader.offset == len(acknowledged)
            assert new_reader.entries[-1]["index"] == last["index"] - 1
        finally:
            with (directory / "journal.jsonl").open("r+b") as stream:
                stream.truncate(len(acknowledged))
            (directory / "index.json").write_bytes(retained_index)


def test_corrupt_acknowledged_coordinate_is_reported(tmp_path: Path) -> None:
    source = scenario("food-delivery", 1_000_000_000)
    directory = tmp_path / "corrupt"
    with RunSession(source, directory) as session:
        session.start()
    storage = RunStorage(directory)
    storage.publish_prefix(storage.offset + 1, storage.entries[-1]["index"] + 1)
    with pytest.raises(ValueError, match="beyond the journal"):
        storage.refresh_committed()


def test_control_status_stream_does_not_wait_for_projection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import threading

    from aeroagentsim.services import feed_index
    from aeroagentsim.services.app import create_app

    application = create_app(tmp_path)
    endpoint = next(route.endpoint for route in application.routes if route.path == "/v1/runs/{run_id}/stream")
    release = threading.Event()
    original = feed_index.page

    def delayed(*args: Any, **kwargs: Any) -> Any:
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(feed_index, "page", delayed)

    class Request:
        headers: dict[str, str] = {}

        async def is_disconnected(self) -> bool:
            return False

    with RunSession(scenario("food-delivery", 1_000_000_000), tmp_path / "status") as session:
        session.start()

        async def check() -> None:
            response = await endpoint("status", Request(), from_index=1)
            iterator = response.body_iterator
            try:
                first = await asyncio.wait_for(anext(iterator), 0.2)
                assert '"status":"running"' in first
                session.storage.status("paused")
                second = await asyncio.wait_for(anext(iterator), 0.2)
                assert '"status":"paused"' in second
                assert not release.is_set()
            finally:
                release.set()
                await iterator.aclose()

        asyncio.run(check())


def test_live_stream_releases_small_ordered_pages_without_losing_cuts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from aeroagentsim.services import feed_index
    from aeroagentsim.services.app import create_app

    application = create_app(tmp_path)
    endpoint = next(route.endpoint for route in application.routes if route.path == "/v1/runs/{run_id}/stream")
    sizes: list[int] = []
    original = feed_index.page

    def measured(path: Path, start: int, limit: int) -> Any:
        sizes.append(limit)
        return original(path, start, limit)

    monkeypatch.setattr(feed_index, "page", measured)

    class Request:
        headers: dict[str, str] = {}

        async def is_disconnected(self) -> bool:
            return False

    directory = tmp_path / "ordered"
    with RunSession(scenario("food-delivery", 3_000_000_000), directory) as session:
        session.run()

    async def collect() -> list[int]:
        response = await endpoint("ordered", Request(), from_index=1)
        indices = []
        async for frame in response.body_iterator:
            if frame.startswith("id: "):
                indices.append(int(frame.splitlines()[0][4:]))
        return indices

    indices = asyncio.run(collect())
    assert indices == list(range(1, RunStorage(directory).metadata()["final_cursor"]))
    assert len(indices) > 32 and len(sizes) > 2
    assert set(sizes) == {32}


def test_idle_live_stream_does_not_rebuild_the_projector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from aeroagentsim.services import feed_index
    from aeroagentsim.services.app import create_app

    application = create_app(tmp_path)
    endpoint = next(route.endpoint for route in application.routes if route.path == "/v1/runs/{run_id}/stream")

    def unnecessary_page(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("No new acknowledged cut: projection work should not be queued")

    monkeypatch.setattr(feed_index, "page", unnecessary_page)

    class Request:
        headers: dict[str, str] = {}

        async def is_disconnected(self) -> bool:
            return False

    with RunSession(scenario("food-delivery", 1_000_000_000), tmp_path / "idle") as session:
        session.start()
        session.storage.publish_prefix(*session.simulation.kernel.acknowledged_prefix)
        cursor = session.storage.entries[-1]["index"] + 1

        async def check() -> None:
            response = await endpoint("idle", Request(), from_index=cursor)
            iterator = response.body_iterator

            async def next_status() -> str:
                async for frame in iterator:
                    if frame.startswith("event: status"):
                        return frame
                raise AssertionError("Live status stream ended")

            try:
                assert '"status":"running"' in await asyncio.wait_for(next_status(), 0.2)
                session.storage.status("paused")
                assert '"status":"paused"' in await asyncio.wait_for(next_status(), 0.2)
            finally:
                await iterator.aclose()

        asyncio.run(check())


def test_background_projection_yields_and_retains_the_complete_prefix(tmp_path: Path) -> None:
    import sqlite3

    from aeroagentsim.services.feed_index import _decode, update

    directory = tmp_path / "warmup"
    with RunSession(scenario("food-delivery", 20_000_000_000), directory) as session:
        session.run()
    final_cursor = RunStorage(directory).metadata()["final_cursor"]
    assert final_cursor > 128
    assert not update(directory, max_batches=1)
    with sqlite3.connect(directory / "viewer-index.sqlite3") as database:
        checkpoint = _decode(database.execute("SELECT data FROM checkpoint").fetchone()[0])
    assert checkpoint["next"] == 65
    # Foreground first-page reads reuse that prefix; background catchup cannot
    # force them to wait for an entire run before publishing any real cuts.
    assert [cut["commitIndex"] for cut in page(directory, 1, 32)] == list(range(1, 33))
    batches = 1
    while not update(directory, max_batches=1):
        batches += 1
        assert batches < final_cursor
    assert [cut["commitIndex"] for cut in page(directory, 1, final_cursor)] == list(range(1, final_cursor))


def test_version_run_warms_the_feed_only_after_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from aeroagentsim.services import feed_index
    from aeroagentsim.services.app import create_app
    from aeroagentsim.services.storage import committed_prefix

    observed: list[tuple[dict[str, int] | None, int | None]] = []
    original = feed_index.update

    def measured(path: Path, **kwargs: Any) -> bool:
        observed.append((committed_prefix(path), kwargs.get("max_batches")))
        return original(path, **kwargs)

    monkeypatch.setattr(feed_index, "update", measured)
    with TestClient(create_app(tmp_path / "runs", studio_root=tmp_path / "studio"), base_url="http://localhost") as client:
        created = client.post("/v1/templates/food-delivery/workspace", json={"knobs": {}})
        assert created.status_code == 201, created.text
        workspace = created.json()["workspace_id"]
        draft = client.get(f"/v1/studio/workspaces/{workspace}").json()
        validation = client.post(
            f"/v1/studio/workspaces/{workspace}/validate", json={"revision": draft["revision"]}
        )
        assert validation.json()["valid"], validation.text
        saved = client.post(
            f"/v1/studio/workspaces/{workspace}/versions", json={"revision": draft["revision"]}
        )
        assert saved.status_code == 201, saved.text
        run = client.post("/v1/runs", json={"studio_workspace": workspace, "version": saved.json()["version"]})
        assert run.status_code == 201, run.text
        assert run.json()["status"] == "initialized"
        wait_for(lambda: bool(observed))
        assert all(prefix is not None and prefix["index"] > 0 and size == 1 for prefix, size in observed)
        assert client.post(f"/v1/runs/{run.json()['id']}/stop", json={}).status_code == 200


def test_live_cached_page_does_not_wait_for_future_projection(tmp_path: Path) -> None:
    import sqlite3
    import threading

    from aeroagentsim.services.feed_index import _path_lock, stream_page, update

    directory = tmp_path / "ready-page"
    with RunSession(scenario("food-delivery", 20_000_000_000), directory) as session:
        session.run()
    storage = RunStorage(directory)
    entries = storage.entries
    storage.publish_prefix(entries[-1]["offset"] + entries[-1]["length"], entries[-1]["index"])
    assert not update(directory, max_batches=1)
    acquired, release = threading.Event(), threading.Event()

    def later_projection() -> None:
        with _path_lock(directory.resolve()), sqlite3.connect(directory / "viewer-index.sqlite3") as database:
            database.execute("BEGIN IMMEDIATE")
            acquired.set()
            assert release.wait(5)

    background = threading.Thread(target=later_projection)
    background.start()
    assert acquired.wait(1)
    try:
        before = time.perf_counter()
        assert [cut["commitIndex"] for cut in stream_page(directory, 1, 32)] == list(range(1, 33))
        assert time.perf_counter() - before < 0.2
        # SQLite can contain a longer cut prefix than the currently advertised
        # coordinate during finalization. Readers still obey the real ACK.
        tail = entries[19]
        storage.publish_prefix(tail["offset"] + tail["length"], tail["index"])
        assert [cut["commitIndex"] for cut in stream_page(directory, 1, 32)] == list(range(1, 20))
    finally:
        release.set()
        background.join(5)
    assert not background.is_alive()


def test_source_progress_remains_readable_during_worker_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = multiprocessing.get_context("fork")
    closing, release = context.Event(), context.Event()
    original = RunSession.close

    def delayed_close(session: RunSession) -> None:
        closing.set()
        assert release.wait(10)
        original(session)

    monkeypatch.setattr(RunSession, "close", delayed_close)
    source = scenario("food-delivery", 1_000_000_000)
    directory = tmp_path / "closing"
    RunStorage(directory).prepare(source)
    host, child = context.Pipe()
    paused, stopped, started = (context.Event() for _ in range(3))
    started.set()
    process = context.Process(target=execute, args=(
        source, directory, paused, stopped, child, started, context.Value("d", 0),
    ))
    process.start()
    child.close()
    try:
        assert closing.wait(10)
        host.send(("progress", {}))
        assert host.poll(1)
        actual = host.recv()["result"]
        assert actual["status"] == "running"
        release.set()
        process.join(10)
        assert process.exitcode == 0
        assert json.loads((directory / "input-progress.json").read_text())["watermarks"] == actual["watermarks"]
        assert actual["watermarks"] == dict(replay(directory / "journal.jsonl").ingress_watermarks)
        assert RunStorage(directory).metadata()["status"] == "completed"
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
        host.close()


def test_paused_input_wait_accepts_ingress_and_does_not_expire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aerokernel.coordinator import Kernel

    prepared = TemplateRegistry().apply("food-delivery", {"run_mode": "interactive"})
    prepared.document["run"]["until_ns"] = 3_000_000_000
    prepared.document["ingress_streams"][0]["timeout_s"] = 0.5
    source = load_scenario(prepared.document, base=prepared.base)
    context = multiprocessing.get_context("fork")
    paused, stopped, started = (context.Event() for _ in range(3))
    original = Kernel.run_until
    first_wait = True

    def suspend_wait(*args: Any, **kwargs: Any) -> Any:
        original_wait = kwargs["on_wait"]

        def wait(state: Any) -> bool:
            nonlocal first_wait
            if state is not None and first_wait:
                first_wait = False
                paused.set()
            return original_wait(state)

        kwargs["on_wait"] = wait
        return original(*args, **kwargs)

    monkeypatch.setattr(Kernel, "run_until", suspend_wait)
    directory = tmp_path / "paused-input-wait"
    RunStorage(directory).prepare(source)
    host, child = context.Pipe()
    started.set()
    process = context.Process(target=execute, args=(
        source, directory, paused, stopped, child, started, context.Value("d", 0),
    ))
    process.start()
    child.close()
    try:
        storage = RunStorage(directory)
        wait_for(lambda: storage.metadata()["status"] == "paused")
        time.sleep(0.75)  # Pause exceeds the real, unchanged source-wait budget.
        host.send(("ingress", {
            "schema": "aas.runtime.inject_event", "target": "behaviour",
            "stream_id": "operator", "at_ns": 1,
            "source_stamp": {"clock_id": "canonical", "mapping_id": "canonical",
                             "numerator": 1, "denominator": 1},
            "idempotency_key": "paused-input-delay",
            "payload": {"injection_point": "restaurant_delay",
                        "payload": {"restaurant": "restaurant-east", "delay_s": 45.0}},
        }))
        assert host.poll(0.2), "Admission during an input wait must not need Resume"
        receipt = host.recv()
        assert receipt["result"]["disposition"] == "accepted", receipt
        host.send(("progress", {}))
        assert host.poll(0.2)
        progress = host.recv()["result"]
        assert progress["status"] == "paused" and progress["watermarks"]["operator"] == 0
        paused.clear()
        host.send(("watermark", {"stream_id": "operator", "watermark_ns": 3_000_000_000}))
        assert host.poll(0.2), "Resume must retain the source's remaining active wait budget"
        closed = host.recv()
        assert closed["result"]["watermark_ns"] == 3_000_000_000, closed
        process.join(10)
        assert process.exitcode == 0
        assert storage.metadata()["status"] == "completed", storage.metadata()
        restored = replay(directory / "journal.jsonl")
        assert restored.ingress_watermarks["operator"] == 3_000_000_000
        assert sum(row["type"] == "run_limit" for row in restored.records) == 1
    finally:
        stopped.set()
        paused.clear()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
        host.close()


def test_paused_wave_keeps_source_progress_and_admission_responsive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aerokernel.coordinator import Kernel

    from aeroagentsim.services.storage import committed_prefix

    context = multiprocessing.get_context("fork")
    paused, stopped, started = (context.Event() for _ in range(3))
    requested_prefix = context.Array("q", [0, 0], lock=False)
    original = Kernel.run_until
    original_wave = Kernel._wave
    entering_wave = False

    def wave(*args: Any, **kwargs: Any) -> Any:
        nonlocal entering_wave
        entering_wave = True
        try:
            return original_wave(*args, **kwargs)
        finally:
            entering_wave = False

    def pause_inside_wave(*args: Any, **kwargs: Any) -> Any:
        original_poll = kwargs["on_poll"]

        def pause() -> None:
            # The request can arrive immediately after a host poll. It must
            # not be suppressed at this next available committed cut.
            if entering_wave and not paused.is_set():
                original_poll()
                requested_prefix[:] = args[0].acknowledged_prefix
                paused.set()
            original_poll()

        kwargs["on_poll"] = pause
        return original(*args, **kwargs)

    monkeypatch.setattr(Kernel, "run_until", pause_inside_wave)
    monkeypatch.setattr(Kernel, "_wave", wave)
    directory = tmp_path / "private-wave"
    RunStorage(directory).prepare(scenario("food-delivery", 1_000_000_000))
    host, child = context.Pipe()
    started.set()
    process = context.Process(target=execute, args=(
        scenario("food-delivery", 1_000_000_000), directory, paused, stopped,
        child, started, context.Value("d", 0),
    ))
    process.start()
    child.close()
    try:
        storage = RunStorage(directory)
        wait_for(lambda: storage.metadata()["status"] == "paused")
        prefix = committed_prefix(directory)
        assert prefix is not None
        assert (prefix["bytes"], prefix["index"]) == tuple(requested_prefix), (
            "A pause at an available committed cut must precede the next invocation"
        )
        frozen = (directory / "journal.jsonl").read_bytes()
        host.send(("progress", {}))
        assert host.poll(0.2), "A progress read must not wait for Resume"
        progress = host.recv()["result"]
        assert progress["status"] == "paused" and progress["pause_requested"]
        assert progress["watermarks"] == dict(replay(directory / "journal.jsonl").ingress_watermarks)
        assert (directory / "journal.jsonl").read_bytes() == frozen
        # A real future command, beyond this template's initial source
        # watermark, must be acknowledged while physical execution is paused.
        host.send(("ingress", {
            "schema": "aas.runtime.inject_event", "target": "behaviour",
            "stream_id": "operator", "at_ns": 361_000_000_000,
            "source_stamp": {"clock_id": "canonical", "mapping_id": "canonical",
                             "numerator": 361_000_000_000, "denominator": 1},
            "idempotency_key": "paused-restaurant-delay",
            "payload": {"injection_point": "restaurant_delay",
                        "payload": {"restaurant": "restaurant-east", "delay_s": 45.0}},
        }))
        assert host.poll(0.2), "Admission must not wait for Resume"
        receipt = host.recv()
        assert "result" in receipt, receipt
        assert receipt["result"]["disposition"] == "accepted"
    finally:
        stopped.set()
        paused.clear()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
        host.close()
    assert process.exitcode == 0


@pytest.mark.parametrize("template", ["food-delivery", "city-day"])
def test_stop_yields_an_exact_headless_prefix_between_reactive_waves(
    tmp_path: Path, template: str
) -> None:
    source = scenario(template, 10_000_000_000)
    stopped, complete = tmp_path / "stopped", tmp_path / "complete"
    with RunSession(source, stopped) as session:
        session.start()
        kernel = session.simulation.kernel

        def stop_at_first_positive_commit() -> bool:
            return kernel.view().cut.instant.ns == 0

        kernel.run_until(source.until_ns, on_poll=stop_at_first_positive_commit)
        assert kernel.view().cut.instant.ns > 0
        assert not kernel._store.pending_intents and not kernel._store.faulted
        stopped_bytes = (stopped / "journal.jsonl").read_bytes()
    # close() appends the existing, truthful run_stop control record. The
    # executed scientific records preceding it must be a headless byte prefix.
    replay(stopped / "journal.jsonl")
    with RunSession(source, complete) as session:
        session.run()
    complete_bytes = (complete / "journal.jsonl").read_bytes()
    assert len(stopped_bytes) < len(complete_bytes)
    assert complete_bytes.startswith(stopped_bytes)


def test_stop_request_does_not_hide_a_real_engine_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stopped = False
    with RunSession(scenario("food-delivery", 1_000_000_000), tmp_path / "fault") as session:
        session.start()
        kernel = session.simulation.kernel

        def failing_advance(*args: Any, **kwargs: Any) -> Any:
            nonlocal stopped
            stopped = True
            raise RuntimeError("actual motion failure")

        monkeypatch.setattr(kernel._by_partition["motion"], "advance", failing_advance)
        with pytest.raises(RuntimeError, match="actual motion failure"):
            kernel.run_until(1_000_000_000, on_poll=lambda: not stopped)
        assert kernel._store.faulted
        assert any(record["type"] == "fault" for record in kernel._store.records)


@pytest.mark.parametrize("template", ["food-delivery", "city-day"])
def test_dispatch_cache_preserves_journal_bytes_and_kpis(
    tmp_path: Path, template: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aerokernel.codec import _DecodedRecord, encode
    from aerokernel.values import canonical_json

    source = scenario(template, 2_000_000_000)
    cached, wire = tmp_path / "cached", tmp_path / "wire"
    with RunSession(source, cached) as session:
        session.run()
    # Decode the same plain wire dispatches rather than reusing typed objects.
    # Both executions must publish exactly the same scientific journal.
    monkeypatch.setattr(_DecodedRecord, "from_record", classmethod(lambda cls, record: encode(record)))
    with RunSession(source, wire) as session:
        session.run()
    assert (cached / "journal.jsonl").read_bytes() == (wire / "journal.jsonl").read_bytes()
    assert canonical_json(kpi_result(cached)) == canonical_json(kpi_result(wire))


@pytest.mark.parametrize("template", ["food-delivery", "city-day"])
def test_projection_cache_keeps_every_cut_identical(tmp_path: Path, template: str) -> None:
    from aerokernel.registry import MemoryRegistry

    from aeroagentsim.services.kpis import KPIProjector
    from aeroagentsim.services.narratives import NarrativeProjector
    from aeroagentsim.services.projector import project
    from aeroagentsim.services.subjects import SubjectProjection

    directory = tmp_path / "projection"
    source = scenario(template, 2_000_000_000)
    with RunSession(source, directory) as session:
        session.run()
    registry = MemoryRegistry.from_data(json.loads((directory / "runtime.registry.json").read_text()))
    subjects = SubjectProjection(registry, source.document)
    kpis = KPIProjector(registry, source.document.get("presentation", []))
    narratives = NarrativeProjector.from_directory(directory)
    expected = [
        {**project(record, subjects=subjects, kpis=kpis), "events": narratives.consume(record)}
        for record in RunStorage(directory).records(1, 4096)
    ]
    assert page(directory, 1, 4096) == expected


@pytest.mark.parametrize("data", [
    b'{"float":-0.0,"integer":12345,"nested":[[],{},true,null]}',
    b'{"float":1e-300,"unicode":"\\ud83d\\ude00","key\\n":"\\u0000"}',
    b'{"duplicate":1,"duplicate":2}', b'{"infinite":1e999}',
    b'{"surrogate":"\\ud800"}', b'{"\\udfff":0}',
    '{"城市":"淮安","unit":"🚁","tree":{"label":"餐饮配送"}}'.encode(),
    b'{"bad":"\xed\xa0\x80"}',
])
def test_fast_live_json_read_preserves_strict_value_semantics(data: bytes) -> None:
    from aerokernel.errors import KernelError
    from aerokernel.values import ResourceBudget, canonical_json, normalize, parse_json

    for frame_bytes in [max(1, len(data)), max(1, len(data) + 1), 8192]:
        for depth in [1, 2, 128]:
            budget = ResourceBudget(4096, frame_bytes, depth)
            try:
                expected = normalize(json.loads(data.decode()), budget)
            except (KernelError, ValueError):
                with pytest.raises(KernelError):
                    parse_json(data, budget)
            else:
                # Duplicate keys are intentionally rejected even though stdlib
                # loads would collapse them. The admitted trees retain bytes.
                if b'"duplicate":' in data:
                    with pytest.raises(KernelError, match="duplicate"):
                        parse_json(data, budget)
                else:
                    assert canonical_json(parse_json(data, budget), budget) == canonical_json(expected, budget)
