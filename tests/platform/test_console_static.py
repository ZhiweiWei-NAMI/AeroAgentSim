from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aeroagentsim.services.app import create_app


def test_console_serves_public_folders(tmp_path: Path) -> None:
    # Vite copies frontend/public verbatim; decoders and artwork are requested by absolute path.
    dist = tmp_path / "dist"
    for name, body in {"index.html": "<html></html>", "assets/app.js": "1", "assistant/describe-card.webp": "art", "decoders/draco/draco_decoder.js": "d"}.items():
        (dist / name).parent.mkdir(parents=True, exist_ok=True)
        (dist / name).write_text(body)
    client = TestClient(create_app(tmp_path / "runs", frontend=dist))
    assert client.get("/assistant/describe-card.webp").text == "art"
    assert client.get("/decoders/draco/draco_decoder.js").text == "d"
    assert client.get("/assistant/missing.webp").status_code == 404
    assert client.get("/").text == "<html></html>"
