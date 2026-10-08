"""Owned native process, exact TraCI barriers and source state projection."""

import logging
import socket
import subprocess
import tempfile
import threading
import time
from decimal import Decimal
from pathlib import Path

from .commands import Commands
from .scenario import prepare
from .wire import fields, integer

LOG = logging.getLogger("sumo-backend")


class NativeFailure(RuntimeError):
    """Native termination with its actual captured SUMO diagnosis."""


class Runtime:
    def __init__(self):
        self.connection = None
        self.process = None
        self.directory = None
        self.log = None
        self.commands = None
        self.sim_ns = 0
        self.step_ns = None
        self.cancelled = threading.Event()
        self.native_errors = ()
        self.geo = False
        self.subscribed = {"vehicle": set(), "person": set()}

    def abort(self):
        self.cancelled.set()

    @staticmethod
    def version():
        text = subprocess.check_output(["sumo", "--version"], text=True, timeout=5)
        return text.splitlines()[0]

    def native_ns(self):
        value = Decimal(str(self.connection.simulation.getTime())) * 1_000_000_000
        if value != value.to_integral_value():
            raise RuntimeError("SUMO returned fractional-nanosecond time")
        return int(value)

    def reset(self, payload):
        self.directory = tempfile.TemporaryDirectory(prefix="aas-sumo-")
        seed, self.step_ns, args, self.geo = prepare(payload, self.directory.name)
        # Reserve a loopback port until launch. TraCI startup is the only retry:
        # no stateful simulation or RPC operation is replayed.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        self.log = open(Path(self.directory.name) / "sumo.log", "w+b")  # noqa: SIM115
        self.process = subprocess.Popen(
            ["sumo", "--remote-port", str(port), *args],
            stdin=subprocess.DEVNULL,
            stdout=self.log,
            stderr=self.log,
            cwd=self.directory.name,
        )
        import traci

        self.native_errors = (traci.exceptions.FatalTraCIError,)
        deadline = time.monotonic() + 30
        while True:
            if self.cancelled.is_set():
                raise RuntimeError("native operation cancelled")
            if self.process.poll() is not None:
                self.log.seek(0)
                raise RuntimeError(f"SUMO startup failed: {self.log.read().decode()}")
            try:
                self.connection = traci.connect(
                    port=port, host="127.0.0.1", numRetries=0
                )
                break
            except (ConnectionRefusedError, traci.exceptions.FatalTraCIError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("TraCI startup timed out")
                time.sleep(0.02)
        self.connection._socket.settimeout(25)
        version = self.connection.getVersion()
        self.sim_ns = self.native_ns()
        if self.sim_ns != 0:
            raise RuntimeError("reset native frontier differs from zero")
        native_step = (
            Decimal(str(self.connection.simulation.getDeltaT())) * 1_000_000_000
        )
        if native_step != self.step_ns:
            raise RuntimeError("native step differs from requested step")
        self.commands = Commands(self.connection, self.step_ns)
        return {
            "reached_sim_ns": 0,
            "step_length_ns": self.step_ns,
            "sumo_version": version[1],
            "traci_version": version[0],
            "seed": seed,
            "geo_referenced": self.geo,
            "entities": self.entities(),
            "traffic_lights": self.traffic_lights(),
        }

    def command(self, payload):
        return self.commands.submit(payload, self.sim_ns)

    def entities(self):
        import traci.constants as tc

        samples = []
        c = self.connection
        common = (
            tc.VAR_POSITION,
            tc.VAR_SPEED,
            tc.VAR_ANGLE,
            tc.VAR_TYPE,
            tc.VAR_LANE_ID,
            tc.VAR_ROAD_ID,
        )
        vehicle_fields = (
            tc.VAR_ROUTE_ID,
            tc.VAR_EDGES,
            tc.VAR_ROUTE_INDEX,
            tc.VAR_SIGNALS,
        )
        for kind, domain in (("vehicle", c.vehicle), ("person", c.person)):
            active = set(domain.getIDList())
            variables = common + (vehicle_fields if kind == "vehicle" else ())
            for ident in sorted(active - self.subscribed[kind]):
                domain.subscribe(ident, variables)
            self.subscribed[kind] = active
            cached = domain.getAllSubscriptionResults()
            for ident in sorted(active):
                data = cached[ident]
                if set(variables) - data.keys():
                    raise RuntimeError(f"incomplete native subscription for {ident}")
                xy = list(data[tc.VAR_POSITION])
                position = {"xy": xy}
                if self.geo:
                    position["lon_lat"] = list(c.simulation.convertGeo(*xy))
                sample = {
                    "id": ident,
                    "kind": kind,
                    "type": data[tc.VAR_TYPE],
                    "sim_ns": self.sim_ns,
                    "position": position,
                    "angle": data[tc.VAR_ANGLE],
                    "speed": data[tc.VAR_SPEED],
                    "lane": data[tc.VAR_LANE_ID],
                    "edge": data[tc.VAR_ROAD_ID],
                }
                if kind == "vehicle":
                    sample.update(
                        route_id=data[tc.VAR_ROUTE_ID],
                        route=list(data[tc.VAR_EDGES]),
                        route_index=data[tc.VAR_ROUTE_INDEX],
                        signals=data[tc.VAR_SIGNALS],
                    )
                else:
                    # TraCI person stage routes require a stage-index argument.
                    sample["route"] = list(domain.getEdges(ident))
                samples.append(sample)
        return samples

    def traffic_lights(self):
        domain = self.connection.trafficlight
        return [
            {
                "id": ident,
                "state": domain.getRedYellowGreenState(ident),
                "phase": domain.getPhase(ident),
                "program": domain.getProgram(ident),
                "next_switch_s": domain.getNextSwitch(ident),
            }
            for ident in sorted(domain.getIDList())
        ]

    def events(self):
        simulation = self.connection.simulation
        result = {}
        getters = {
            "departed": (
                simulation.getDepartedIDList,
                simulation.getDepartedPersonIDList,
            ),
            "arrived": (simulation.getArrivedIDList, simulation.getArrivedPersonIDList),
            "teleported_start": (simulation.getStartingTeleportIDList,),
            "teleported_end": (simulation.getEndingTeleportIDList,),
        }
        for key, methods in getters.items():
            result[key] = [
                {"id": ident, "kind": ("vehicle", "person")[i], "sim_ns": self.sim_ns}
                for i, method in enumerate(methods)
                for ident in sorted(method())
            ]
        # Rich collisions are native per-step observations, not inferred overlap.
        result["collisions"] = [
            {
                "collider": col.collider,
                "victim": col.victim,
                "collider_type": col.colliderType,
                "victim_type": col.victimType,
                "collider_speed": col.colliderSpeed,
                "victim_speed": col.victimSpeed,
                "type": col.type,
                "lane": col.lane,
                "position": col.pos,
                "sim_ns": self.sim_ns,
            }
            for col in simulation.getCollisions()
        ]
        result["removed"] = self.commands.take_removed()
        return result

    def advance(self, payload):
        fields(payload, ("to_sim_ns",))
        target = integer(payload["to_sim_ns"], "to_sim_ns")
        if target < self.sim_ns or target % self.step_ns:
            raise ValueError("target must be monotone and step-aligned")
        # Commands never mutate native state on a held/equal-time advance.
        collected = {
            key: []
            for key in (
                "departed",
                "arrived",
                "teleported_start",
                "teleported_end",
                "collisions",
                "removed",
            )
        }
        updates = []
        deadline = time.monotonic() + 29
        while self.sim_ns < target:
            if self.cancelled.is_set():
                raise RuntimeError("native operation cancelled")
            if time.monotonic() >= deadline:
                raise TimeoutError("advance native integration deadline")
            updates.extend(self.commands.before_step(self.sim_ns))
            expected = self.sim_ns + self.step_ns
            try:
                self.connection.simulationStep()
                reached = self.native_ns()
            except self.native_errors as error:
                # Read a separate descriptor: seeking the inherited process log
                # descriptor could interfere with the child writer's offset.
                with open(self.log.name, "rb") as stream:
                    stream.seek(0, 2)
                    stream.seek(max(0, stream.tell() - 16384))
                    diagnostic = stream.read().decode(
                        "utf-8", errors="backslashreplace"
                    )
                raise NativeFailure(
                    f"{error}; native log tail (max 16384 bytes): {diagnostic}"
                ) from error
            if reached != expected:
                raise RuntimeError(f"native frontier {reached} differs from {expected}")
            self.sim_ns = reached
            events = self.events()
            updates.extend(self.commands.after_step(self.sim_ns, events))
            for key, records in collected.items():
                records.extend(events[key])
        return {
            "reached_sim_ns": self.sim_ns,
            "entities": self.entities(),
            **collected,
            "pending_vehicle_ids": sorted(
                self.connection.simulation.getPendingVehicles()
            ),
            "traffic_lights": self.traffic_lights(),
            "command_updates": updates,
        }

    def stop(self):
        errors = []
        connection, process = self.connection, self.process
        self.connection = None
        self.process = None
        try:
            if connection is not None:
                connection.close(wait=False)
        except Exception as error:  # noqa: BLE001 - aggregate and report cleanup faults
            errors.append(f"TraCI cleanup: {error}")
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            except Exception as error:  # noqa: BLE001 - aggregate and report cleanup faults
                errors.append(f"process cleanup: {error}")
        if self.log is not None:
            self.log.close()
            self.log = None
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None
        if errors:
            raise RuntimeError("; ".join(errors))
