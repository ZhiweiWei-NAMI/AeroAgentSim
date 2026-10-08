"""Persistent request-only Gazebo node, isolated from Python subscriptions.

Transport13's blocking pybind request retains the GIL. Subscriber callbacks
acquire that GIL, so mixing them in one process can block reply processing.
This child has no subscriptions and sends each stateful request exactly once.
"""

import json
import sys
import time


def main():
    sys.path.append("/usr/lib/python3/dist-packages")
    from gz.msgs10.boolean_pb2 import Boolean
    from gz.msgs10.world_control_pb2 import WorldControl
    from gz.transport13 import Node, NodeOptions

    partition, world = sys.argv[1:]
    options = NodeOptions()
    options.partition = partition
    node = Node(options)
    service = f"/world/{world}/control"
    deadline = time.monotonic() + 30
    while service not in node.service_list():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"WorldControl service not discovered: {service}")
        time.sleep(0.01)
    print("ready", flush=True)
    for line in sys.stdin:
        iterations = json.loads(line)
        if type(iterations) is not int or not 0 < iterations <= 125:
            raise ValueError("WorldControl iterations must be integer 1..125")
        request = WorldControl(pause=True, multi_step=iterations)
        result, response = node.request(service, request, WorldControl, Boolean, 10000)
        print(json.dumps({"result": result, "data": response.data}), flush=True)


if __name__ == "__main__":
    main()
