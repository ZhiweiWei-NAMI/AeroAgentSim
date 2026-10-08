"""Gazebo transport: exact paused barriers, timestamped poses and contacts."""

import asyncio
import json
import math
import os
import sys
import threading
import time


class Gazebo:
    def __init__(self, partition, world, vehicle_models):
        self.partition, self.world, self.models = partition, world, set(vehicle_models)
        self.node = None
        self.control_process = None
        self.topics, self.callbacks = [], []
        self.lock = threading.Lock()
        self.sim_ns = 0
        self.stats = None
        self.poses = {}
        self.contacts = []
        self.failure = None
        self.active = True

    @staticmethod
    def stamp(stamp):
        if not 0 <= stamp.nsec < 1_000_000_000 or stamp.sec < 0:
            raise ValueError("invalid Gazebo source timestamp")
        return stamp.sec * 1_000_000_000 + stamp.nsec

    def fail(self, error):
        with self.lock:
            if self.failure is None:
                self.failure = error

    def check(self):
        with self.lock:
            error = self.failure
        if error is not None:
            raise RuntimeError(f"Gazebo subscription failed: {error}") from error

    async def subscribe(self, message_type, topic, callback):
        self.callbacks.append(
            callback
        )  # Python callback lifetime must exceed native subscription.
        accepted = await asyncio.to_thread(
            self.node.subscribe, message_type, topic, callback
        )
        if accepted is not True:
            raise RuntimeError(f"Gazebo rejected subscription {topic}")
        self.topics.append(topic)

    async def start(self):
        sys.path.append("/usr/lib/python3/dist-packages")
        from gz.msgs10.contacts_pb2 import Contacts
        from gz.msgs10.pose_v_pb2 import Pose_V
        from gz.msgs10.world_stats_pb2 import WorldStatistics
        from gz.transport13 import Node, NodeOptions

        options = NodeOptions()
        options.partition = self.partition
        self.node = Node(options)
        await self.subscribe(
            WorldStatistics, f"/world/{self.world}/stats", self.on_stats
        )
        await self.subscribe(Pose_V, f"/world/{self.world}/pose/info", self.on_pose)
        await self.wait(
            lambda: self.stats is not None and self.stats[1],
            30,
            "initial paused WorldStatistics",
        )
        await self.wait(
            lambda: self.models <= self.poses.keys(), 30, "initial vehicle poses"
        )
        env = dict(os.environ, GZ_PARTITION=self.partition, GZ_IP="127.0.0.1")
        process = await asyncio.create_subprocess_exec(
            "gz",
            "topic",
            "-l",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, error = await asyncio.wait_for(process.communicate(), 10)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError(
                f"Gazebo topic listing: {error.decode(errors='replace')}"
            )
        topics = [
            topic
            for topic in output.decode().splitlines()
            if "/sensor/aero_contact_" in topic
        ]
        if not topics:
            raise RuntimeError("world has no instrumented contact topics")
        for topic in sorted(topics):

            def callback(message, source_topic=topic):
                self.on_contacts(message, source_topic)

            await self.subscribe(Contacts, topic, callback)
        self.contact_topics = topics
        self.control_process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "service.world_control",
            self.partition,
            self.world,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        ready = await asyncio.wait_for(self.control_process.stdout.readline(), 35)
        if ready != b"ready\n":
            raise RuntimeError(f"WorldControl child failed readiness: {ready!r}")

    def on_stats(self, message):
        if not self.active:
            return
        try:
            if not message.HasField("sim_time"):
                raise ValueError("WorldStatistics omitted sim_time")
            ns = self.stamp(message.sim_time)
            with self.lock:
                if self.stats is not None and ns < self.stats[0]:
                    raise ValueError("Gazebo time regressed")
                self.stats = ns, message.paused
        except Exception as error:
            self.fail(error)

    def on_pose(self, message):
        if not self.active:
            return
        try:
            if not message.HasField("header") or not message.header.HasField("stamp"):
                raise ValueError("Gazebo Pose_V omitted source timestamp")
            ns = self.stamp(message.header.stamp)
            poses = {}
            for pose in message.pose:
                if pose.name not in self.models:
                    continue
                if not pose.HasField("position") or not pose.HasField("orientation"):
                    raise ValueError(f"pose {pose.name} omitted geometry")
                position = [pose.position.x, pose.position.y, pose.position.z]
                q = pose.orientation
                quaternion = [q.x, q.y, q.z, q.w]
                if not all(math.isfinite(v) for v in position + quaternion):
                    raise ValueError("Gazebo pose nonfinite")
                if not 0.99 < sum(v * v for v in quaternion) < 1.01:
                    raise ValueError("Gazebo quaternion not normalized")
                poses[pose.name] = {
                    "sim_ns": ns,
                    "position_enu": position,
                    "attitude_quat": quaternion,
                }
            with self.lock:
                self.poses.update(poses)
        except Exception as error:
            self.fail(error)

    def on_contacts(self, message, topic):
        if not self.active:
            return
        try:
            events = []
            if not message.HasField("header") or not message.header.HasField("stamp"):
                raise ValueError("Gazebo Contacts omitted source timestamp")
            ns = self.stamp(message.header.stamp)
            for contact in message.contact:
                if not contact.collision1.name or not contact.collision2.name:
                    raise ValueError("Gazebo contact omitted collision names")
                events.append(
                    {
                        "sim_ns": ns,
                        "topic": topic,
                        "collision1": contact.collision1.name,
                        "collision2": contact.collision2.name,
                    }
                )
            with self.lock:
                self.contacts.extend(events)
        except Exception as error:
            self.fail(error)

    async def wait(self, predicate, timeout, label):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.check()
            with self.lock:
                ready = predicate()
            if ready:
                return
            await asyncio.sleep(0.001)
        raise TimeoutError(f"Gazebo timed out waiting for {label}; stats={self.stats}")

    async def step(self, iterations, physics_step_ns):
        self.check()
        if type(iterations) is not int or iterations <= 0:
            raise ValueError("multi_step must be positive integer")
        expected = self.sim_ns + iterations * physics_step_ns
        process = self.control_process
        if process.returncode is not None:
            raise RuntimeError(f"WorldControl child exited {process.returncode}")
        process.stdin.write((json.dumps(iterations) + "\n").encode())
        await process.stdin.drain()
        frame = await asyncio.wait_for(process.stdout.readline(), 12)
        if not frame:
            raise RuntimeError("EOF from WorldControl child; no retry")
        acknowledgment = json.loads(frame)
        if acknowledgment != {"result": True, "data": True}:
            raise RuntimeError(f"WorldControl failed {acknowledgment}; no retry")

        async def barrier():
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                self.check()
                with self.lock:
                    stats = self.stats
                if stats is not None:
                    if stats[0] > expected:
                        raise RuntimeError(
                            f"Gazebo overshot target {expected}: {stats}"
                        )
                    if stats == (expected, True):
                        return
                await asyncio.sleep(0.001)
            raise TimeoutError(
                f"WorldStatistics never confirmed {expected} paused; observed {stats}"
            )

        await barrier()
        self.sim_ns = expected
        await self.wait(
            lambda: all(
                model in self.poses and self.poses[model]["sim_ns"] == expected
                for model in self.models
            ),
            10,
            f"pose source timestamp {expected}",
        )

    async def sample(self, vehicle_model):
        self.check()
        with self.lock:
            pose = self.poses[vehicle_model]
            if pose["sim_ns"] != self.sim_ns:
                raise RuntimeError(
                    f"pose source time {pose['sim_ns']} differs from frontier {self.sim_ns}"
                )
            return dict(pose)

    def drain_contacts(self):
        self.check()
        with self.lock:
            result, self.contacts = self.contacts, []
        return result

    async def stop(self):
        self.active = False
        errors = []
        process, self.control_process = self.control_process, None
        if process is not None:
            if process.returncode is None:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        topics, self.topics = self.topics, []
        if self.node is not None:
            for topic in topics:
                try:
                    if (
                        await asyncio.to_thread(self.node.unsubscribe, topic)
                        is not True
                    ):
                        errors.append(f"unsubscribe rejected: {topic}")
                except Exception as error:
                    errors.append(f"{topic}: {error}")
        self.callbacks.clear()
        self.node = None
        if errors:
            raise RuntimeError("; ".join(errors))
