"""Authored two-peer pause example; no external simulator or telemetry is implied."""

import socket
import threading

from aerokernel import (
    BindingManifest,
    Kernel,
    MemoryRegistry,
    Partition,
    PausePolicy,
    Timing,
    replay,
)
from aerokernel.rpc import RemoteEngine, serve_engine
from aerokernel.sdk import SimpleEngine


def main() -> None:
    """Hold native-grid and exact-stop peers while the host makes a decision."""
    policy = PausePolicy(idle_timeout_s=10, max_hold_s=60, frame_timeout_s=2)
    peers = []
    sockets = []
    threads = []
    failures = []

    def serve(engine, stream):
        try:
            serve_engine(engine, stream, pause_policy=policy)
        except Exception as error:
            failures.append(error)
        finally:
            stream.close()

    for name, timing in (
        ("grid", Timing("lockstep", 20, certified_hold=True)),
        ("exact", Timing("lockstep", exact_stop=True)),
    ):
        client, server = socket.socketpair()
        engine = SimpleEngine(Partition(name, name, timing=timing))
        thread = threading.Thread(target=serve, args=(engine, server), daemon=True)
        thread.start()
        threads.append(thread)
        sockets.append(client)
        peers.append(RemoteEngine(client, pause_policy=policy))
    kernel = Kernel()
    try:
        kernel.bind(MemoryRegistry(()), BindingManifest("pause", "0"), tuple(peers))
        kernel.start()
        kernel.run_until(3)
        assert kernel._store.frontiers["grid"][1] == 0
        assert kernel._store.frontiers["exact"][1] == 3
        before = kernel._store.frontiers.copy()
        kernel.hold_wall_clock(30, "waiting for a host decision")
        assert kernel._store.frontiers == before
        assert replay(kernel.journal.bytes).records == kernel.records
        kernel.run_until(20)
        print({"sealed_ns": kernel._store.sealed_ns, "lease_s": 30})
    finally:
        kernel.close()
        for client in sockets:
            client.close()
        for thread in threads:
            thread.join(1)
        assert not failures and not any(thread.is_alive() for thread in threads)


if __name__ == "__main__":
    main()
