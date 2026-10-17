"""Persistent application bytes gated by real ns-3 last-hop events.

The native provider models packet sizes. Image bytes live in a private spool,
not inside its UDP buffers. Only the coordinator may call consume_native_event;
companions may receive committed copies, never this object's spool directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

NS = 1_000_000_000
BUSINESS_TYPES = {"notice", "bid", "plan", "report", "file", "handover", "receipt"}


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("wb") as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


class RadioTransport:
    """One coordinator-owned router, independent of the physics clock owner.

    tick() only queues commands; it never advances ns-3 or waits for delivery.
    All protocol replies use the identical native command/event path.
    """

    def __init__(self, root: str | Path, run_id: str, *, chunk_bytes: int = 1024) -> None:
        if not 1 <= chunk_bytes <= 1024:
            raise ValueError("chunk_bytes must be in 1..1024")
        self.root, self.run_id, self.chunk_bytes = Path(root), run_id, chunk_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        self.spool = self.root / "private-payloads"
        self.spool.mkdir(mode=0o700, exist_ok=True)
        self.state_path = self.root / "transport-state.json"
        self.state: dict[str, Any] = {"run_id": run_id, "messages": {}, "packets": {}, "incoming": {}, "committed": {}, "ready": {}, "commands": [], "deliveries": {}, "now_ns": 0}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
            if self.state["run_id"] != run_id:
                raise ValueError("transport spool belongs to another run")

    def _save(self) -> None:
        save_json(self.state_path, self.state)

    def _log(self, event: str, **data: Any) -> None:
        with (self.root / "packet-ledger.jsonl").open("ab") as stream:
            stream.write(encoded({"run_id": self.run_id, "event": event, "sim_ns": self.state["now_ns"], **data}) + b"\n")

    def queue_message(self, source: str, destination: str, kind: str, data: dict[str, Any], *, event_id: str, task_id: str, task_revision: int, deadline_ns: int, now_ns: int, route: list[str] | None = None, message_id: str | None = None) -> str:
        if kind not in BUSINESS_TYPES:
            raise ValueError("unsupported business kind")
        return self.queue_file(source, destination, encoded(data), kind=kind, event_id=event_id, task_id=task_id, task_revision=task_revision, deadline_ns=deadline_ns, now_ns=now_ns, route=route, message_id=message_id, metadata={"content_type": "application/json"})

    def queue_file(self, source: str, destination: str, data: bytes, *, event_id: str, task_id: str, task_revision: int, deadline_ns: int, now_ns: int, route: list[str] | None = None, message_id: str | None = None, kind: str = "file", metadata: dict[str, Any] | None = None) -> str:
        route = route or [source, destination]
        if len(route) < 2 or route[0] != source or route[-1] != destination or len(set(route)) != len(route):
            raise ValueError("route must be acyclic and connect source/destination")
        if any(not node or "/" in node or node in {".", ".."} for node in route):
            raise ValueError("invalid radio identity")
        if deadline_ns <= now_ns:
            raise ValueError("message deadline has passed")
        message_id = message_id or uuid.uuid4().hex
        if not message_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError("message_id must be an opaque safe identifier")
        file_hash = digest(data)
        old = self.state["messages"].get(message_id)
        if old is not None:
            if (old["file_hash"], old["source"], old["destination"], old["event_id"], old["task_id"], old["task_revision"]) != (file_hash, source, destination, event_id, task_id, task_revision):
                raise ValueError("message identity reused for different contents or task")
            return message_id
        directory = self.spool / message_id
        directory.mkdir()
        chunks = [data[i:i + self.chunk_bytes] for i in range(0, len(data), self.chunk_bytes)] or [b""]
        hashes = []
        for index, chunk in enumerate(chunks):
            (directory / str(index)).write_bytes(chunk)
            hashes.append(digest(chunk))
        self.state["messages"][message_id] = {"message_id": message_id, "source": source, "destination": destination, "route": route, "kind": kind, "event_id": event_id, "task_id": task_id, "task_revision": task_revision, "metadata": metadata or {}, "file_hash": file_hash, "size": len(data), "chunk_hashes": hashes, "deadline_ns": min(deadline_ns, now_ns + 120 * NS), "created_ns": now_ns, "status": "pending_route", "attempt": 0, "chunk_sends": {}, "last_protocol_ns": now_ns, "query_retries": 0}
        self.state["now_ns"] = now_ns
        self._log("queued", message_id=message_id, file_hash=file_hash, chunk_hashes=hashes, size=len(data), source=source, destination=destination, kind=kind, event_id=event_id, task_id=task_id, task_revision=task_revision)
        self._probe(message_id, now_ns)
        self._save()
        return message_id

    def _packet(self, mid: str, kind: str, content: dict[str, Any], route: list[str], now_ns: int) -> None:
        message = self.state["messages"][mid]
        packet_id = f"rc-{uuid.uuid4().hex}"
        header = {"message_id": mid, "event_id": message["event_id"], "task_id": message["task_id"], "task_revision": message["task_revision"], "kind": kind, "attempt": message["attempt"], **content}
        data_size = 0
        if kind == "chunk":
            data_size = (self.spool / mid / str(content["chunk_index"])).stat().st_size
            if content["chunk_index"] == 0:
                header["manifest"] = {key: message[key] for key in ("message_id", "source", "destination", "kind", "event_id", "task_id", "task_revision", "metadata", "file_hash", "size", "created_ns")}
        packet = {"packet_id": packet_id, "message_id": mid, "kind": kind, "header": header, "route": route, "hop": 0, "size": len(encoded(header)) + data_size, "created_ns": now_ns}
        self.state["packets"][packet_id] = packet
        self._hop(packet, now_ns)

    def _hop(self, packet: dict[str, Any], now_ns: int) -> None:
        route, hop = packet["route"], packet["hop"]
        native_id = f'{packet["packet_id"]}-h{hop}'
        packet["native_id"] = native_id
        self.state["commands"].append({"packet_id": native_id, "src": route[hop], "dst": route[hop + 1], "size": packet["size"], "payload_ref": "rescue-gate:" + packet["packet_id"], "lifetime_ns": 5 * NS})
        self._log("native_submit", message_id=packet["message_id"], packet_id=native_id, attempt=packet["header"]["attempt"], header=packet["header"], source=route[hop], destination=route[hop + 1], size=packet["size"])

    def _probe(self, mid: str, now_ns: int) -> None:
        message = self.state["messages"][mid]
        message["last_protocol_ns"] = now_ns
        self._packet(mid, "probe", {}, message["route"], now_ns)

    def start_probe(self, source: str, destination: str, *, route: list[str], now_ns: int, deadline_ns: int) -> str:
        mid = self.queue_message(source, destination, "report", {}, event_id="route-probe", task_id="route-probe", task_revision=0, now_ns=now_ns, deadline_ns=deadline_ns, route=route)
        self.state["messages"][mid]["probe_only"] = True
        self._save()
        return mid

    def probe_route(self, source: str, destination: str, *, route: list[str], now_ns: int) -> dict[str, Any]:
        """Report only recently measured round trips; submitting is pending."""
        key = "|".join(route)
        result = self.state["ready"].get(key)
        if result and now_ns - result["observed_ns"] <= 5 * NS:
            return {"status": "succeeded", **result}
        return {"status": "pending", "route": route, "reason": "No unexpired native probe round trip"}

    def update_route(self, message_id: str, route: list[str], now_ns: int) -> None:
        message = self.state["messages"][message_id]
        if route[0] != message["source"] or route[-1] != message["destination"] or len(set(route)) != len(route):
            raise ValueError("invalid replacement route")
        if message["status"] not in {"succeeded", "failed"}:
            message.update(route=route, status="pending_route", last_protocol_ns=now_ns)
            self._probe(message_id, now_ns)
            self._save()

    def _chunks(self, mid: str, indices: list[int], now_ns: int) -> None:
        message = self.state["messages"][mid]
        sent = False
        for index in indices:
            key = str(index)
            count = message["chunk_sends"].get(key, 0)
            if count >= 3:
                continue
            message["chunk_sends"][key] = count + 1
            self._packet(mid, "chunk", {"chunk_index": index, "chunk_hash": message["chunk_hashes"][index], "chunk_count": len(message["chunk_hashes"]), "file_hash": message["file_hash"]}, message["route"], now_ns)
            sent = True
        message["status"] = "awaiting_ack" if sent else "pending_route"
        message["last_protocol_ns"] = now_ns
        if not sent:
            self._probe(mid, now_ns)

    def tick(self, now_ns: int) -> None:
        if now_ns < self.state["now_ns"]:
            raise ValueError("radio simulation clock cannot go backwards")
        self.state["now_ns"] = now_ns
        for mid, message in self.state["messages"].items():
            if message["status"] in {"succeeded", "failed"}:
                continue
            if now_ns >= message["deadline_ns"]:
                message.update(status="failed", reason="deadline_exceeded")
                self._log("message_expired", message_id=mid)
            elif now_ns - message["last_protocol_ns"] >= 5 * NS:
                if message["status"] == "pending_route":
                    if message["attempt"] >= 2:
                        message.update(status="failed", reason="attempt_limit")
                    else:
                        self._probe(mid, now_ns)
                elif message["query_retries"] < 2:
                    message["query_retries"] += 1
                    message["last_protocol_ns"] = now_ns
                    self._packet(mid, "status_query", {"chunk_count": len(message["chunk_hashes"]), "file_hash": message["file_hash"]}, message["route"], now_ns)
                else:
                    message.update(status="pending_route", query_retries=0)
                    self._probe(mid, now_ns)
        self._save()

    def pop_commands(self) -> list[dict[str, Any]]:
        commands = self.state["commands"]
        self.state["commands"] = []
        self._save()
        return commands

    def consume_native_event(self, kind: str, event: dict[str, Any]) -> None:
        """Called with actual ns-3 delivery/drop records by the adapter owner."""
        native_id = event["packet_id"]
        packet_id = native_id.rsplit("-h", 1)[0]
        packet = self.state["packets"].get(packet_id)
        if packet is None or packet["native_id"] != native_id:
            return  # late/duplicate delivery cannot expose bytes twice
        now_ns = int(event.get("available_sim_ns", event.get("available_ns", event.get("received_ns", event.get("sim_ns", self.state["now_ns"])))))
        self.state["now_ns"] = max(self.state["now_ns"], now_ns)
        if kind not in {"delivery", "drop"}:
            raise ValueError("native event must be delivery or drop")
        self._log("native_" + kind, packet_id=native_id, message_id=packet["message_id"], native_event=event)
        if kind == "drop":
            del self.state["packets"][packet_id]
        else:
            packet["hop"] += 1
            if packet["hop"] < len(packet["route"]) - 1:
                self._hop(packet, now_ns)
            else:
                self._log("last_hop_delivery", packet_id=native_id, message_id=packet["message_id"], header=packet["header"])
                del self.state["packets"][packet_id]
                self._receive(packet, now_ns)
        self._save()

    def _receive(self, packet: dict[str, Any], now_ns: int) -> None:
        mid, kind = packet["message_id"], packet["kind"]
        message = self.state["messages"][mid]
        if now_ns > message["deadline_ns"]:
            return
        reverse = list(reversed(packet["route"]))
        if kind == "probe":
            self._packet(mid, "probe_ack", {"probe_started_ns": packet["created_ns"]}, reverse, now_ns)
        elif kind == "probe_ack":
            measured_route = list(reversed(packet["route"]))
            if measured_route != message["route"]:
                self._log("stale_route_probe_ack", message_id=mid, measured_route=measured_route)
                return
            self.state["ready"]["|".join(measured_route)] = {"route": message["route"], "observed_ns": now_ns, "round_trip_ns": now_ns - packet["header"]["probe_started_ns"], "packet_id": packet["native_id"]}
            if message.get("probe_only"):
                message.update(status="succeeded", ack_received_ns=now_ns)
            elif message["status"] == "pending_route" and message["attempt"] < 2:
                message.update(attempt=message["attempt"] + 1, chunk_sends={}, query_retries=0)
                self._chunks(mid, list(range(len(message["chunk_hashes"]))), now_ns)
        elif kind == "chunk":
            self._receive_chunk(packet, now_ns)
        elif kind in {"status_query", "NACK"}:
            if mid in self.state["committed"]:
                self._packet(mid, "final_ACK", {"file_hash": message["file_hash"]}, reverse, now_ns)
            else:
                got = self.state["incoming"].get(mid, {})
                missing = [i for i in range(packet["header"]["chunk_count"]) if str(i) not in got]
                self._packet(mid, "status_response", {"missing_chunks": missing}, reverse, now_ns)
        elif kind == "status_response":
            if packet["header"]["attempt"] == message["attempt"]:
                self._chunks(mid, packet["header"]["missing_chunks"], now_ns)
        elif kind == "final_ACK" and packet["header"]["file_hash"] == message["file_hash"]:
            message.update(status="succeeded", ack_received_ns=now_ns)
            self._log("radio_final_ack", message_id=mid, packet_id=packet["native_id"], file_hash=message["file_hash"])

    def _receive_chunk(self, packet: dict[str, Any], now_ns: int) -> None:
        mid, header = packet["message_id"], packet["header"]
        message = self.state["messages"][mid]
        reverse = list(reversed(packet["route"]))
        if mid in self.state["committed"]:
            self._packet(mid, "final_ACK", {"file_hash": message["file_hash"]}, reverse, now_ns)
            return
        index = header["chunk_index"]
        data = (self.spool / mid / str(index)).read_bytes()
        if digest(data) != header["chunk_hash"]:
            raise ValueError("immutable payload changed before native delivery")
        receiver_dir = self.root / "received" / message["destination"] / mid
        receiver_dir.mkdir(parents=True, exist_ok=True)
        chunk_path = receiver_dir / f"{index}.part"
        with chunk_path.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        received = self.state["incoming"].setdefault(mid, {})
        received[str(index)] = {"hash": digest(data), "packet_id": packet["native_id"], "received_ns": now_ns, "manifest": header.get("manifest")}
        self._log("receiver_chunk_commit", message_id=mid, chunk_index=index, chunk_hash=digest(data), packet_id=packet["native_id"])
        if len(received) != len(message["chunk_hashes"]):
            return
        assembled = b"".join((receiver_dir / f"{i}.part").read_bytes() for i in range(len(message["chunk_hashes"])))
        if digest(assembled) != message["file_hash"]:
            raise ValueError("received file hash mismatch")
        path = receiver_dir / "content"
        with path.open("wb") as stream:
            stream.write(assembled)
            stream.flush()
            os.fsync(stream.fileno())
        result = dict(received["0"]["manifest"])
        result.update(received_ns=now_ns, content_path=str(path), native_packet_ids=[row["packet_id"] for row in received.values()])
        if message["metadata"].get("content_type") == "application/json":
            result["data"] = json.loads(assembled)
        self.state["committed"][mid] = result
        self.state["deliveries"].setdefault(message["destination"], []).append(result)
        self._save()  # durable complete record precedes final ACK
        self._log("receiver_file_commit", **result)
        self._packet(mid, "final_ACK", {"file_hash": message["file_hash"]}, reverse, now_ns)

    def pop_delivered(self, node: str) -> list[dict[str, Any]]:
        return list(self.state["deliveries"].get(node, []))

    def acknowledge_local_delivery(self, node: str, message_id: str) -> None:
        """Management acknowledgement after companion persists its inbox copy.

        This cannot confirm radio transmission; final_ACK still goes through ns-3.
        """
        self.state["deliveries"][node] = [row for row in self.state["deliveries"].get(node, []) if row["message_id"] != message_id]
        self._save()

    def status(self, message_id: str) -> dict[str, Any]:
        message = self.state["messages"][message_id]
        return {key: message[key] for key in ("message_id", "status", "attempt", "deadline_ns", "file_hash", "size", "chunk_sends")}
