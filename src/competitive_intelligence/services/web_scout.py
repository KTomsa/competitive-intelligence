"""Web Scout: deterministic source intake with three honest outcomes.

1. Live fetch works and the page matches an approved baseline -> `baseline`
   (the web WAS checked just now; the approved claims are reused).
2. Live fetch works and the page is new or changed -> `live`.
3. Live fetch fails or returns nothing usable -> the dated `snapshot`, with
   the reason, or a source error when no snapshot exists.

A fallback is never presented as live content.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..connectors import SupplierWebClient
from ..parsers import PARSER_VERSION, canonicalize, sha256_bytes, sha256_text
from ..registry import RegisteredPage
from ..schemas import Origin, Source
from .repository import utc_now
from .stores import BaselineStore, Snapshot, SnapshotStore, SourceStorage

# Below this a "successful" response is a consent wall or an empty JavaScript
# shell, not a page we can read claims from.
MIN_USABLE_CHARS = 300


@dataclass(frozen=True)
class ScoutedSource:
    source: Source
    text: str
    baseline_claims: list[dict[str, Any]] | None = None
    snapshot_saved: bool = True


@dataclass(frozen=True)
class ScoutError:
    supplier_id: str
    source_id: str
    source_url: str
    reason: str


class WebScout:
    def __init__(
        self,
        client: SupplierWebClient,
        snapshots: SnapshotStore,
        baselines: BaselineStore,
        now: Callable[[], datetime] = utc_now,
        *,
        record_source: Callable[[Source], None] | None = None,
    ) -> None:
        self._client = client
        self._snapshots = snapshots
        self._baselines = baselines
        self._now = now
        self._record_source = record_source

    def with_storage(self, storage: SourceStorage) -> "WebScout":
        """Retain the approved client/clock while substituting durable storage."""
        def capture_time() -> datetime:
            value = self._now()
            # The existing Azure capture/link columns are datetime2(3). Choose
            # the same instant precision before creating either DTO or row.
            return value.replace(microsecond=(value.microsecond // 1000) * 1000)

        return WebScout(self._client, storage.snapshots, storage.baselines, capture_time,
                        record_source=storage.record)

    def collect(self, supplier_id: str, page: RegisteredPage) -> ScoutedSource | ScoutError:
        checked_at = self._now()
        result = self._client.fetch(page.url)
        failure = result.reason
        if result.ok and result.content is not None:
            text = canonicalize(result.content)
            if len(text) >= MIN_USABLE_CHARS:
                outcome = self._from_live(supplier_id, page, result.content, text, checked_at)
                if self._record_source is not None:
                    self._record_source(outcome.source)
                return outcome
            failure = "stránka nevrátila čitelný obsah (JavaScript nebo souhlas s cookies)"
        outcome = self._from_snapshot(supplier_id, page, failure or "neznámá chyba", checked_at)
        if isinstance(outcome, ScoutedSource) and self._record_source is not None:
            self._record_source(outcome.source)
        return outcome

    def _from_live(
        self, supplier_id: str, page: RegisteredPage, content: bytes, text: str, checked_at: datetime
    ) -> ScoutedSource:
        canonical_hash = sha256_text(text)
        content_hash = sha256_bytes(content)
        saved = self._snapshots.save(
            Snapshot(
                source_id=page.source_id,
                source_url=page.url,
                captured_at=checked_at,
                parser_version=PARSER_VERSION,
                content_sha256=content_hash,
                canonical_hash=canonical_hash,
                text=text,
            )
        )
        baseline = self._baselines.get(page.source_id)
        reuse = (
            baseline is not None
            and baseline.canonical_hash == canonical_hash
            and baseline.source_url == page.url
            and baseline.parser_version == PARSER_VERSION
        )
        source = Source(
            source_id=page.source_id,
            supplier_id=supplier_id,
            source_url=page.url,
            origin=Origin.BASELINE if reuse else Origin.LIVE,
            fetch_was_live=True,
            captured_at=checked_at,
            checked_at=checked_at,
            valid_as_of=checked_at.date(),
            content_sha256=content_hash,
            canonical_hash=canonical_hash,
            parser_version=PARSER_VERSION,
            baseline_approved_at=baseline.approved_at if reuse and baseline else None,
        )
        return ScoutedSource(
            source, text, baseline.claims if reuse and baseline else None, snapshot_saved=saved
        )

    def _from_snapshot(
        self, supplier_id: str, page: RegisteredPage, reason: str, checked_at: datetime
    ) -> ScoutedSource | ScoutError:
        snapshot = self._snapshots.get(page.source_id)
        if (
            snapshot is None
            or snapshot.source_url != page.url
            or snapshot.parser_version != PARSER_VERSION
        ):
            return ScoutError(supplier_id, page.source_id, page.url, reason)
        source = Source(
            source_id=page.source_id,
            supplier_id=supplier_id,
            source_url=page.url,
            origin=Origin.SNAPSHOT,
            fetch_was_live=False,
            captured_at=snapshot.captured_at,  # the real age of the content, not "now"
            checked_at=checked_at,
            valid_as_of=snapshot.captured_at.date(),
            content_sha256=snapshot.content_sha256,
            canonical_hash=snapshot.canonical_hash,
            parser_version=snapshot.parser_version,
            fallback_reason=reason,
        )
        return ScoutedSource(source, snapshot.text)
