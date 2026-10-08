"""Own and clean up only native processes started by this backend."""

import asyncio
import os
import shutil
import signal
import time

from .config import DATA, ROOT


class Processes:
    def __init__(self, directory, partition):
        self.directory = directory
        self.entries = []
        runtime = directory / "runtime"
        runtime.mkdir(mode=0o700)
        self.env = dict(os.environ)
        self.env.update(
            HOME=str(directory),
            TMPDIR=str(directory),
            XDG_RUNTIME_DIR=str(runtime),
            XDG_CACHE_HOME=str(directory / "cache"),
            GZ_PARTITION=partition,
            GZ_IP="127.0.0.1",
            HEADLESS="1",
            LP_NUM_THREADS="4",
        )

    async def spawn(self, argv, name, cwd=None, env=None):
        log = self.directory / (name + ".log")
        with log.open("wb") as output:
            process = await asyncio.create_subprocess_exec(
                *map(str, argv),
                cwd=cwd or self.directory,
                env=env or self.env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=output,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        self.entries.append((process, log))
        return process

    def check(self):
        for process, log in self.entries:
            if process.returncode is not None:
                raise RuntimeError(
                    f"{log.name} exited {process.returncode}: {log.read_text(errors='replace')[-4000:]}"
                )

    async def wait_log(self, name, phrase, timeout=90):
        deadline = time.monotonic() + timeout
        path = self.directory / (name + ".log")
        while time.monotonic() < deadline:
            self.check()
            if phrase in path.read_text(errors="replace"):
                return
            await asyncio.sleep(0.05)
        raise TimeoutError(
            f"{name} missing {phrase}: {path.read_text(errors='replace')[-4000:]}"
        )

    async def gazebo(self, config):
        await self.spawn(
            ("gz", "sim", "--seed", config.seed, "-s", config.sdf), "gazebo"
        )

    async def px4(self, vehicle, config):
        directory = self.directory / f"px4-{vehicle.id}"
        directory.mkdir()
        data = self.directory / f"data-{vehicle.id}"
        shutil.copytree(DATA, data)
        script = data / "init.d-posix/px4-rc.mavlink"
        if not script.is_file():
            raise FileNotFoundError(script)
        script.write_text(
            f"#!/bin/sh\nmavlink start -x -u {18570 + vehicle.index} -r 4000000 -f -m onboard -o {vehicle.udp_port}\nparam set MAV_SYS_ID {vehicle.index + 1}\n"
        )
        env = dict(self.env)
        env.update(
            PX4_GZ_STANDALONE="1",
            PX4_GZ_WORLD=config.world,
            PX4_GZ_MODEL_NAME=vehicle.model_name,
            PX4_GZ_NO_FOLLOW="1",
            PX4_SIM_MODEL="gz_" + vehicle.model,
            PX4_SYS_AUTOSTART=str(vehicle.autostart),
            PX4_SIMULATOR="gz",
            PX4_SEED=str(config.seed),
            HOME=str(directory),
        )
        await self.spawn(
            (ROOT / "bin/px4", "-d", "-i", vehicle.index, "-w", directory, data),
            "px4-" + vehicle.id,
            directory,
            env,
        )

    async def mavsdk(self, vehicle):
        # The inherited patched binary requires this argument. No audit decoder
        # or evidence consumer exists in the slim service.
        await self.spawn(
            (
                "/opt/mavsdk/mavsdk_server",
                "-p",
                vehicle.grpc_port,
                "--incoming-heartbeat-timeout-s",
                "3600",
                "--command-audit-journal-path",
                self.directory / f"mavsdk-{vehicle.id}.jsonl",
                f"udpin://0.0.0.0:{vehicle.udp_port}",
            ),
            "mavsdk-" + vehicle.id,
        )

    async def stop(self):
        entries, self.entries = self.entries, []
        for process, _ in reversed(entries):
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for process, _ in reversed(entries):
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
