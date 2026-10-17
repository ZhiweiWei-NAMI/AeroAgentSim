"""Executable companion client for its one private coordinator endpoint."""
from __future__ import annotations

import argparse
import json
import socket
import uuid
from pathlib import Path
from typing import Any


class FlightClient:
    def __init__(self, socket_path: str | Path, run_id: str, *, timeout_s: float = 10) -> None:
        self.socket_path, self.run_id = str(socket_path), run_id
        self.timeout_s = timeout_s

    def request(self, op: str, **params: Any) -> dict[str, Any]:
        request = dict(request_id=uuid.uuid4().hex, run_id=self.run_id, op=op, **params)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self.timeout_s)
            connection.connect(self.socket_path)
            connection.sendall((json.dumps(request, allow_nan=False) + '\n').encode())
            with connection.makefile('rb') as reader:
                response = json.loads(reader.readline(8 * 1024 * 1024))
        if response.get('status') == 'error':
            raise RuntimeError(response.get('reason', 'coordinator request failed'))
        return response

    def submit(self, action: str, params: dict[str, Any], task_id: str,
               task_revision: int) -> dict[str, Any]:
        return self.request('submit', action=action, params=params,
                            task_id=task_id, task_revision=task_revision)

    def poll(self, operation_id: str) -> dict[str, Any]:
        return self.request('poll', operation_id=operation_id)

    def cancel(self, operation_id: str) -> dict[str, Any]:
        return self.request('cancel', operation_id=operation_id)

    def state(self) -> dict[str, Any]:
        return self.request('state')

    def accept_task(self, message_id: str, task_id: str, task_revision: int) -> dict[str, Any]:
        return self.request('task_accept', message_id=message_id,
                            task_id=task_id, task_revision=task_revision)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', default='/run/aas-uav/agent.sock')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('request', help='JSON object with op and arguments')
    args = parser.parse_args()
    request = json.loads(args.request)
    print(json.dumps(FlightClient(args.socket, args.run_id).request(**request)))


if __name__ == '__main__':
    main()
