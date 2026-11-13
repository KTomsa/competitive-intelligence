"""Snapshot and baseline storage for the Web Scout.

File and memory adapters support the process-local demonstration and tests.
The durable worker substitutes Azure-backed adapters scoped to its lease.
"""

import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import AwareDatetime, Field

from ..schemas import Source, StrictModel
from ..schemas.claim import SHA256_PATTERN


class Snapshot(StrictModel):
    source_id: str
    source_url: str
    captured_at: AwareDatetime
    parser_version: str
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    canonical_hash: str = Field(pattern=SHA256_PATTERN)
    text: str


class Baseline(StrictModel):
    """Claims a person approved for one exact version of one page."""

    source_id: str
    source_url: str
    parser_version: str
    canonical_hash: str = Field(pattern=SHA256_PATTERN)
    approved_at: AwareDatetime
    claims: list[dict[str, Any]]


class SnapshotStore(Protocol):
    def get(self, source_id: str) -> Snapshot | None: ...

    def save(self, snapshot: Snapshot) -> bool: ...


class BaselineStore(Protocol):
    def get(self, source_id: str) -> Baseline | None: ...


class SourceStorage(Protocol):
    snapshots: SnapshotStore
    baselines: BaselineStore

    def record(self, source: Source) -> None: ...


def _read[T: StrictModel](model: type[T], path: Path) -> T | None:
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # a missing or damaged file is the same as no file


class FileSnapshotStore:
    """Keeps the latest snapshot per source. An unchanged page does not rewrite its file."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def get(self, source_id: str) -> Snapshot | None:
        return _read(Snapshot, self._dir / f"{source_id}.json")

    def save(self, snapshot: Snapshot) -> bool:
        current = self.get(snapshot.source_id)
        if current is not None and current.canonical_hash == snapshot.canonical_hash:
            return True
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2)
            (self._dir / f"{snapshot.source_id}.json").write_text(payload + "\n", encoding="utf-8")
            return True
        except OSError:
            return False


class FileBaselineStore:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def get(self, source_id: str) -> Baseline | None:
        return _read(Baseline, self._dir / f"{source_id}.json")


class MemorySnapshotStore:
    def __init__(self, snapshots: list[Snapshot] | None = None) -> None:
        self._items = {s.source_id: s for s in snapshots or []}

    def get(self, source_id: str) -> Snapshot | None:
        return self._items.get(source_id)

    def save(self, snapshot: Snapshot) -> bool:
        self._items[snapshot.source_id] = snapshot
        return True


class MemoryBaselineStore:
    def __init__(self, baselines: list[Baseline] | None = None) -> None:
        self._items = {b.source_id: b for b in baselines or []}

    def get(self, source_id: str) -> Baseline | None:
        return self._items.get(source_id)
