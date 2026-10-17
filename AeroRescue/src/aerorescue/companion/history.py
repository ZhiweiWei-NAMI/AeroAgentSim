"""Private persistent observations, received copies, and text retrieval."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np


class LocalBGE:
    def __init__(self, model_path: str | Path):
        os.environ["ORT_DISABLE_TELEMETRY"] = "1"
        import onnxruntime as ort
        from fastembed import TextEmbedding

        ort.disable_telemetry_events()
        self.model = TextEmbedding(
            model_name="BAAI/bge-small-zh-v1.5",
            specific_model_path=str(model_path),
            local_files_only=True,
            threads=1,
        )

    def __call__(self, texts: list[str]) -> np.ndarray:
        return np.asarray(list(self.model.embed(texts)), dtype=np.float32)


class ObservationStore:
    def __init__(
        self, root: str | Path, run_id: str, uav_id: str, embedder: Any = None
    ):
        self.root = Path(root).resolve()
        self.run_id, self.uav_id, self.embedder = run_id, uav_id, embedder
        for folder in ("observations", "artifacts", "inbox", "outbox", "tasks", "logs"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "index.sqlite", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS identity(run_id TEXT,uav_id TEXT);
            CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,path TEXT,sha256 TEXT,metadata TEXT);
            CREATE TABLE IF NOT EXISTS observations(frame_id TEXT PRIMARY KEY,observer TEXT,observed_at INTEGER,
              received_at INTEGER,source TEXT,description TEXT,metadata TEXT,embedding BLOB);
            CREATE VIRTUAL TABLE IF NOT EXISTS sighting_text USING fts5(frame_id UNINDEXED, description);
            CREATE TABLE IF NOT EXISTS analyses(id INTEGER PRIMARY KEY,frame_id TEXT,analyzed_at INTEGER,result TEXT);
        """)
        identity = self.db.execute("SELECT * FROM identity").fetchone()
        if identity and (identity["run_id"], identity["uav_id"]) != (run_id, uav_id):
            raise ValueError(
                "this private data volume belongs to a different run or UAV"
            )
        if not identity:
            self.db.execute("INSERT INTO identity VALUES (?,?)", (run_id, uav_id))
            self.db.commit()

    def save_artifact(
        self, content: bytes, suffix: str, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        digest = hashlib.sha256(content).hexdigest()
        suffix = {".png": ".png", ".jpg": ".jpg", ".npy": ".npy", ".json": ".json"}.get(
            suffix, ".bin"
        )
        path = self.root / "artifacts" / (digest + suffix)
        if not path.exists():
            with path.open("xb") as handle:
                handle.write(content)
        self.db.execute(
            "INSERT OR IGNORE INTO artifacts VALUES (?,?,?,?)",
            (
                digest,
                str(path.relative_to(self.root)),
                digest,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        self.db.commit()
        return {
            "artifact_id": digest,
            "sha256": digest,
            "bytes": len(content),
            "path": str(path),
        }

    def read(self, artifact_id: str) -> tuple[bytes, dict[str, Any]]:
        row = self.db.execute(
            "SELECT * FROM artifacts WHERE id=?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise FileNotFoundError(
                "artifact has not been recorded or received by this UAV"
            )
        path = (self.root / row["path"]).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("artifact escapes private data root")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise ValueError("artifact bytes changed since recording")
        return content, json.loads(row["metadata"])

    def artifact_path(self, artifact_id: str) -> Path:
        self.read(artifact_id)
        row = self.db.execute(
            "SELECT path FROM artifacts WHERE id=?", (artifact_id,)
        ).fetchone()
        return self.root / row["path"]

    def record(
        self,
        observation: dict[str, Any],
        rgb_bytes: bytes,
        depth_npy_bytes: bytes | None = None,
        *,
        received: bool = False,
    ) -> dict[str, Any]:
        metadata = dict(observation)
        if metadata["run_id"] != self.run_id:
            raise ValueError("observation is from a different run")
        if not received and metadata["uav_id"] != self.uav_id:
            raise ValueError("only this UAV's sensor can create local observations")
        if received and metadata.get("received_at") is None:
            raise ValueError("a delivered copy needs its actual receive time")
        frame_id = metadata["frame_id"]
        existing = self.db.execute(
            "SELECT metadata FROM observations WHERE frame_id=?", (frame_id,)
        ).fetchone()
        if existing:
            old = json.loads(existing["metadata"])
            if old["rgb_sha256"] != hashlib.sha256(rgb_bytes).hexdigest():
                raise ValueError("frame_id cannot overwrite an earlier observation")
            return old
        rgb = self.save_artifact(
            rgb_bytes, ".png", {"frame_id": frame_id, "kind": "rgb"}
        )
        metadata.update(
            acquisition_source=metadata.get("acquisition_source", metadata.get("source")),
            rgb_artifact_id=rgb["artifact_id"],
            rgb_sha256=rgb["sha256"],
            source="received" if received else "local",
        )
        if depth_npy_bytes is not None:
            depth = self.save_artifact(
                depth_npy_bytes, ".npy", {"frame_id": frame_id, "kind": "depth"}
            )
            metadata["depth_artifact_id"] = depth["artifact_id"]
        description = metadata.get("description", "")
        embedding = (
            self.embedder([description])[0].astype(np.float32).tobytes()
            if description and self.embedder
            else None
        )
        with self.db:
            self.db.execute(
                "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)",
                (
                    frame_id,
                    metadata["uav_id"],
                    metadata["observed_at"],
                    metadata.get("received_at"),
                    metadata["source"],
                    description,
                    json.dumps(metadata, ensure_ascii=False),
                    embedding,
                ),
            )
            self.db.execute(
                "INSERT INTO sighting_text VALUES (?,?)", (frame_id, description)
            )
        return metadata

    def observation(self, frame_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT metadata FROM observations WHERE frame_id=?", (frame_id,)
        ).fetchone()
        if row is None:
            raise FileNotFoundError("frame has not been recorded or received")
        return dict(json.loads(row["metadata"]))

    def retrieve(
        self,
        query: str = "",
        observed_after_ns: int | None = None,
        observed_before_ns: int | None = None,
        region_enu: list[float] | None = None,
        source: str | None = None,
        limit: int = 5,
        observer: str | None = None,
        category: str | None = None,
        min_semantic_score: float = 0.5,
    ) -> list[dict[str, Any]]:
        clauses, values = [], []
        for expression, value in (
            ("observed_at>=?", observed_after_ns),
            ("observed_at<=?", observed_before_ns),
            ("source=?", source),
            ("observer=?", observer),
        ):
            if value is not None:
                clauses.append(expression)
                values.append(value)
        sql = "SELECT * FROM observations" + (
            " WHERE " + " AND ".join(clauses) if clauses else ""
        )
        rows = self.db.execute(sql, values).fetchall()
        query_embedding = self.embedder([query])[0] if query and self.embedder else None
        lexical_ids: set[str] = set()
        if query:
            # Quoting makes user text a phrase, never executable FTS syntax.
            phrase = '"' + query.replace('"', '""') + '"'
            lexical_ids = {
                r[0]
                for r in self.db.execute(
                    "SELECT frame_id FROM sighting_text WHERE sighting_text MATCH ?",
                    (phrase,),
                )
            }
        results = []
        for row in rows:
            metadata = json.loads(row["metadata"])
            if category and category not in metadata.get("categories", []):
                continue
            if region_enu is not None:
                footprint = metadata.get("footprint_enu", [])
                if not footprint:
                    continue
                points = np.asarray(footprint)
                x1, y1, x2, y2 = region_enu
                if (
                    points[:, 0].max() < x1
                    or points[:, 0].min() > x2
                    or points[:, 1].max() < y1
                    or points[:, 1].min() > y2
                ):
                    continue
            score = 0.0
            if query:
                score = (
                    1.0
                    if row["frame_id"] in lexical_ids
                    or query.casefold() in row["description"].casefold()
                    else 0.0
                )
                if query_embedding is not None and row["embedding"]:
                    vector = np.frombuffer(row["embedding"], dtype=np.float32)
                    semantic = float(
                        np.dot(vector, query_embedding)
                        / (np.linalg.norm(vector) * np.linalg.norm(query_embedding))
                    )
                    if semantic >= min_semantic_score:
                        score = max(score, semantic)
                if not score:
                    continue
            result = {**metadata, "retrieval_score": score}
            if region_enu is not None:
                result["spatial_match_method"] = "candidate footprint bounding-box overlap, not exact observed-area coverage"
            results.append(result)
        return sorted(
            results,
            key=lambda r: (r["retrieval_score"], r["observed_at"]),
            reverse=True,
        )[:limit]

    def add_analysis(
        self, frame_id: str, result: dict[str, Any], analyzed_at_ns: int
    ) -> None:
        metadata = self.observation(frame_id)
        # Detection IDs include output slots. A reanalysis can reuse a slot for
        # a different box, so old positions/tracks must not follow it silently.
        metadata.pop("locations", None)
        metadata.pop("tracking", None)
        detections = result.get("detections", [])
        categories = sorted({item["class"] for item in detections})
        description = metadata.setdefault(
            "original_description", metadata.get("description", "")
        )
        summary = (
            f"人员检测 person: {len(detections)} 个候选"
            if detections
            else "此帧未检出人员；不能证明无人"
        )
        description = (description + " " + summary).strip()
        metadata.update(
            categories=categories,
            description=description,
            analyzed_at=analyzed_at_ns,
            last_analysis={
                "detections": detections,
                "model_sha256": result.get("model_sha256"),
            },
        )
        embedding = (
            self.embedder([description])[0].astype(np.float32).tobytes()
            if self.embedder
            else None
        )
        with self.db:
            self.db.execute(
                "INSERT INTO analyses(frame_id,analyzed_at,result) VALUES (?,?,?)",
                (frame_id, analyzed_at_ns, json.dumps(result, ensure_ascii=False)),
            )
            self.db.execute(
                "UPDATE observations SET description=?,metadata=?,embedding=? WHERE frame_id=?",
                (
                    description,
                    json.dumps(metadata, ensure_ascii=False),
                    embedding,
                    frame_id,
                ),
            )
            self.db.execute("DELETE FROM sighting_text WHERE frame_id=?", (frame_id,))
            self.db.execute(
                "INSERT INTO sighting_text VALUES (?,?)", (frame_id, description)
            )

    def save_location(self, frame_id: str, location: dict[str, Any]) -> None:
        metadata = self.observation(frame_id)
        locations = metadata.setdefault("locations", {})
        locations[location["detection_id"]] = location
        with self.db:
            self.db.execute(
                "UPDATE observations SET metadata=? WHERE frame_id=?",
                (json.dumps(metadata, ensure_ascii=False), frame_id),
            )

    def save_tracks(self, frame_id: str, result: dict[str, Any]) -> None:
        metadata = self.observation(frame_id)
        metadata["tracking"] = result
        observed = {
            item["detection_id"]: item["track_id"]
            for item in result["tracks"]
            if item["status"] == "observed"
        }
        for detection in metadata.get("last_analysis", {}).get("detections", []):
            if detection["detection_id"] in observed:
                detection["track_id"] = observed[detection["detection_id"]]
        with self.db:
            self.db.execute(
                "UPDATE observations SET metadata=? WHERE frame_id=?",
                (json.dumps(metadata, ensure_ascii=False), frame_id),
            )

    def reanalyze(
        self, frame_id: str, detector: Any, analyzed_at_ns: int
    ) -> dict[str, Any]:
        observation = self.observation(frame_id)
        result = detector.detect(
            self.artifact_path(observation["rgb_artifact_id"]),
            frame_id,
            self.root / "observations",
        )
        result.update(
            observed_at=observation["observed_at"],
            analyzed_at=analyzed_at_ns,
            original_rgb_sha256=observation["rgb_sha256"],
            analysis_kind="historical_reanalysis",
        )
        self.add_analysis(frame_id, result, analyzed_at_ns)
        return result

    def close(self) -> None:
        self.db.close()
