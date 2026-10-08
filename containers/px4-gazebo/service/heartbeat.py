"""Keep the real MAVSDK GCS link alive in the PX4 simulation time domain."""

import asyncio
import json

# GCS / INVALID autopilot / ACTIVE. This describes the SDK endpoint, never
# vehicle health, flight mode or command success. MAVSDK supplies its own
# configured source IDs and performs native MAVLink framing/serialization.
FIELDS = json.dumps(
    {
        "type": 6,
        "autopilot": 8,
        "base_mode": 0,
        "custom_mode": 0,
        "system_status": 4,
        "mavlink_version": 3,
    }
)


class Heartbeat:
    def __init__(self, sessions, message_type):
        self.sessions = sessions
        self.message = message_type("HEARTBEAT", 0, 0, 0, 0, FIELDS)
        self.next_ns = 0

    async def pulse(self, native_sim_ns):
        """Queue one SDK heartbeat per native second before further integration.

        WorldControl grants are chunked to at most 500 ms in the default world.
        Waiting for native send acceptance does not imply a PX4 acknowledgment.
        Failures propagate; the next frontier is never fabricated or retried.
        """
        if native_sim_ns < self.next_ns:
            return
        await asyncio.gather(
            *(
                session.mavlink_direct.send_message(self.message)
                for session in self.sessions.values()
            )
        )
        self.next_ns = native_sim_ns + 1_000_000_000
