"""Private UAV endpoints. Only the coordinator drains requests and advances time."""
from __future__ import annotations

import json
import queue
import socketserver
import threading
from pathlib import Path
from typing import Any

from .operations import TERMINAL, Operations

FLIGHT_ACTIONS = frozenset({'arm', 'takeoff', 'goto', 'hold', 'land', 'disarm'})


class VehicleBridge:
    def __init__(self, run_id: str, socket_root: str | Path, data_root: str | Path,
                 uav_ids: tuple[str, ...] = ('u1', 'u2', 'u3', 'u4', 'centre')) -> None:
        self.run_id = run_id
        self.socket_root = Path(socket_root)
        self.operations = Operations(Path(data_root) / 'flight', run_id)
        self.uav_ids = uav_ids
        self.requests: queue.Queue[dict[str, Any]] = queue.Queue()
        self.tasks: dict[str, tuple[str, int]] = {}
        self.state_cache: dict[str, dict[str, Any]] = {}
        self.replies: dict[str, dict[str, Any]] = {}
        self.inboxes: dict[str, list[dict[str, Any]]] = {u: [] for u in uav_ids}
        self.accepted_messages: dict[tuple[str, str], dict[str, Any]] = {}
        self.event_revisions: dict[tuple[str, str], int] = {}
        self.received_messages: dict[str, dict[str, dict[str, Any]]] = {u: {} for u in uav_ids}
        self.servers: list[socketserver.UnixStreamServer] = []
        self.threads: list[threading.Thread] = []
        self.lock = threading.RLock()
        self.sim_ns = 0

    def start(self) -> None:
        bridge = self
        for uav_id in self.uav_ids:
            directory = self.socket_root / uav_id
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = directory / 'agent.sock'
            if path.exists():
                raise FileExistsError(f'endpoint already exists: {path}')

            class Handler(socketserver.StreamRequestHandler):
                def handle(self) -> None:
                    self.connection.settimeout(10)
                    try:
                        data = self.rfile.readline(8 * 1024 * 1024)
                        if not data.endswith(b'\n'):
                            raise ValueError('request must end with newline')
                        result = bridge.handle(self.server.uav_id, json.loads(data))
                    except (ValueError, KeyError, PermissionError, RuntimeError, OSError) as error:
                        result = {'status': 'error', 'reason': str(error)}
                    self.wfile.write((json.dumps(result, allow_nan=False) + '\n').encode())

            class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
                daemon_threads = True

            server = Server(str(path), Handler)
            server.uav_id = uav_id
            path.chmod(0o600)
            self.servers.append(server)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            self.threads.append(thread)
            thread.start()

    def authorize_task(self, uav_id: str, task_id: str, task_revision: int) -> None:
        """Coordinator-only, after the task's radio delivery and acceptance."""
        with self.lock:
            current = self.tasks.get(uav_id)
            if current and current[0] == task_id and task_revision < current[1]:
                raise ValueError('stale task revision')
            if self.operations.active(uav_id) and current != (task_id, task_revision):
                raise ValueError('finish or cancel the previous operation first')
            self.tasks[uav_id] = (task_id, task_revision)

    def handle(self, uav_id: str, request: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if request.get('run_id') != self.run_id:
                raise ValueError('request belongs to a different run')
            if request.get('uav_id', uav_id) != uav_id:
                raise PermissionError('identity is fixed by the private endpoint')
            op = request['op']
            if op == 'state':
                return dict(self.state_cache.get(uav_id, {
                    'status': 'unavailable', 'reason': 'no estimated telemetry yet',
                    'uav_id': uav_id, 'sim_ns': self.sim_ns}))
            if op == 'poll':
                return self.operations.get(request['operation_id'], uav_id)
            if op == 'submit':
                if (request['task_id'], request['task_revision']) != self.tasks.get(uav_id):
                    raise ValueError('task was not received/accepted, or revision is stale')
                if request['action'] not in FLIGHT_ACTIONS:
                    raise ValueError('unsupported flight action')
                if self.operations.active(uav_id):
                    raise ValueError('UAV already has an active or unreconciled operation')
                record = self.operations.create(uav_id, request['action'], request['params'],
                    request['task_id'], request['task_revision'], self.sim_ns)
                self.requests.put({'op': 'submit', **record})
                return record
            if op == 'cancel':
                record = self.operations.get(request['operation_id'], uav_id)
                if record['status'] in TERMINAL:
                    return record
                if not record['dispatched']:
                    return self.operations.update(record['operation_id'], status='canceled',
                        completed_sim_ns=self.sim_ns, reason='removed before kernel dispatch')
                if record['status'] != 'cancel_requested':
                    self.requests.put({'op': 'cancel', **record})
                    return self.operations.update(record['operation_id'], status='cancel_requested',
                        cancel_requested_sim_ns=self.sim_ns)
                return record
            if op == 'radio_inbox':
                return {'status': 'available', 'messages': list(self.inboxes[uav_id]), 'sim_ns': self.sim_ns}
            if op == 'task_accept':
                message = self.received_messages[uav_id].get(request['message_id'])
                if not message or message.get('kind') not in {'notice', 'plan'}:
                    raise ValueError('task message has not arrived over radio')
                if (message.get('task_id'), message.get('task_revision')) != (
                        request['task_id'], request['task_revision']):
                    raise ValueError('task identity differs from delivered message')
                identity = (uav_id, request['message_id'])
                if identity in self.accepted_messages:
                    return dict(self.accepted_messages[identity])
                event = (uav_id, message['event_id'])
                if request['task_revision'] < self.event_revisions.get(event, -1):
                    raise ValueError('stale task revision for this event')
                self.authorize_task(uav_id, request['task_id'], request['task_revision'])
                self.event_revisions[event] = request['task_revision']
                result = {'status': 'accepted', 'sim_ns': self.sim_ns,
                          'task_id': request['task_id'], 'task_revision': request['task_revision']}
                self.accepted_messages[identity] = result
                return dict(result)
            if op in {'radio_send', 'radio_route', 'radio_status', 'radio_inbox_ack', 'capture_request', 'capture_poll', 'capture_read',
                      'payload_release', 'payload_status', 'probe_route'}:
                request_id = request['request_id']
                self.requests.put({**request, 'uav_id': uav_id, 'sim_ns': self.sim_ns})
                return {'status': 'pending', 'request_id': request_id, 'sim_ns': self.sim_ns}
            if op == 'result':
                reply = self.replies.get(request['pending_request_id'])
                if reply and reply.get('uav_id') == uav_id:
                    return dict(reply)
                return {'status': 'pending', 'sim_ns': self.sim_ns}
            raise ValueError('unknown operation; world control is not exposed')

    def drain_requests(self) -> list[dict[str, Any]]:
        requests = []
        with self.lock:
            while not self.requests.empty():
                request = self.requests.get_nowait()
                if request['op'] == 'submit':
                    record = self.operations.get(request['operation_id'], request['uav_id'])
                    if record['status'] in TERMINAL:
                        continue
                    self.operations.update(record['operation_id'], dispatched=True)
                requests.append(request)
        return requests

    def publish_state(self, uav_id: str, sample: dict[str, Any], sim_ns: int) -> None:
        """No physical world position, ground truth IDs, or neighbour telemetry."""
        with self.lock:
            self.sim_ns = sim_ns
            estimate = sample.get('vehicle_estimate', {'status': 'unavailable',
                'reason': 'PX4 estimate not provided'})
            self.state_cache[uav_id] = {'uav_id': uav_id, 'sim_ns': sim_ns,
                'status': estimate['status'], 'vehicle_estimate': estimate,
                **{k: sample[k] for k in ('armed', 'flight_mode', 'landed_state', 'battery') if k in sample}}

    def deliver(self, node: str, message: dict[str, Any]) -> None:
        with self.lock:
            if message['message_id'] not in self.received_messages[node]:
                self.received_messages[node][message['message_id']] = dict(message)
                self.inboxes[node].append(dict(message))

    def acknowledge_inbox(self, node: str, message_id: str) -> None:
        with self.lock:
            self.inboxes[node] = [message for message in self.inboxes[node]
                                  if message['message_id'] != message_id]

    def reply(self, request: dict[str, Any], result: dict[str, Any]) -> None:
        with self.lock:
            self.replies[request['request_id']] = {**result, 'uav_id': request['uav_id']}

    def close(self) -> None:
        for server in self.servers:
            server.shutdown()
            server.server_close()
            Path(server.server_address).unlink(missing_ok=True)
        for thread in self.threads:
            thread.join(timeout=2)
