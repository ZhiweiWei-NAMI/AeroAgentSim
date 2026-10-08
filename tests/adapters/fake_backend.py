"""A real TCP test peer, never used as a production adapter data source."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Callable
from typing import Any


class FakeBackend:
    def __init__(self, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.handler = handler
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.endpoint = self.listener.getsockname()
        self.requests: list[dict[str, Any]] = []
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self) -> None:
        try:
            connection, _ = self.listener.accept()
            with connection, connection.makefile("rb") as reader:
                while data := reader.readline():
                    request = json.loads(data)
                    self.requests.append(request)
                    result = self.handler(request)
                    response = {
                        key: request[key]
                        for key in ("protocol", "major", "minor", "id")
                    }
                    response["result"] = result
                    connection.sendall((json.dumps(response) + "\n").encode())
                    if request["op"] == "close":
                        break
        except BaseException as error:  # noqa: BLE001 - propagate thread faults in close
            self.error = error
        finally:
            self.listener.close()

    def close(self) -> None:
        self.thread.join(2)
        if self.thread.is_alive():
            self.listener.close()
            raise RuntimeError("fake server did not stop")
        if self.error is not None:
            raise self.error
