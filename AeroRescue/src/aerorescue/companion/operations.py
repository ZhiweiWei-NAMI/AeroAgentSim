"""Small durable operation ledger; restarts never replay physical commands."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

TERMINAL = frozenset({'succeeded', 'failed', 'canceled', 'rejected'})


class Operations:
    def __init__(self, directory: str | Path, run_id: str) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'operations.jsonl'
        self.run_id = run_id
        self.lock = threading.RLock()
        self.records: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                record = json.loads(line)
                if record['run_id'] != run_id:
                    raise ValueError('operation directory belongs to another run')
                self.records[record['operation_id']] = record
            for record in list(self.records.values()):
                if record['status'] not in TERMINAL:
                    self.update(record['operation_id'], status='reconcile_required',
                                reason='reconnect to the original backend before issuing new commands')

    def create(self, uav_id: str, action: str, params: dict[str, Any],
               task_id: str, task_revision: int, sim_ns: int) -> dict[str, Any]:
        with self.lock:
            operation_id = 'op-' + uuid.uuid4().hex
            record = dict(operation_id=operation_id, run_id=self.run_id,
                          uav_id=uav_id, action=action, params=params,
                          task_id=task_id, task_revision=task_revision,
                          status='accepted', submitted_sim_ns=sim_ns,
                          dispatched=False)
            self.records[operation_id] = record
            return self.update(operation_id)

    def update(self, operation_id: str, **changes: Any) -> dict[str, Any]:
        with self.lock:
            record = self.records[operation_id]
            record.update(changes, updated_wall_ns=time.time_ns())
            with self.path.open('a') as output:
                output.write(json.dumps(record, allow_nan=False) + '\n')
                output.flush()
            return dict(record)

    def get(self, operation_id: str, uav_id: str) -> dict[str, Any]:
        with self.lock:
            record = self.records[operation_id]
            if record['uav_id'] != uav_id:
                raise PermissionError('operation belongs to another UAV')
            return dict(record)

    def active(self, uav_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self.records.values()
                    if r['uav_id'] == uav_id and r['status'] not in TERMINAL]
