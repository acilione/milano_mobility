"""Domain models for ingestion and data-quality reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any


class ManifestStatus(str, Enum):
    """Lifecycle states for one source payload."""

    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    QUARANTINED = "QUARANTINED"
    PUBLISHED = "PUBLISHED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class Severity(str, Enum):
    """Data-quality severity and publication behavior."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    INFO = "info"


@dataclass(frozen=True)
class ValidationIssue:
    """One validation result."""

    rule: str
    severity: Severity
    message: str
    table: str | None = None
    count: int = 1
    sample: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    """Complete validation report for one feed."""

    pipeline_run_id: str
    source_snapshot_date: date
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    issues: list[ValidationIssue] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        """Whether Gold publication must be blocked."""
        return any(issue.severity in {Severity.CRITICAL, Severity.HIGH} for issue in self.issues)

    @property
    def status(self) -> str:
        """Return the externally visible validation state."""
        return "failed" if self.blocking else "passed"

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report for object storage."""
        value = asdict(self)
        value["source_snapshot_date"] = self.source_snapshot_date.isoformat()
        value["generated_at"] = self.generated_at.isoformat()
        value["status"] = self.status
        value["blocking"] = self.blocking
        return value


@dataclass(frozen=True)
class Manifest:
    """Immutable metadata describing a downloaded feed."""

    source: str
    retrieved_at: datetime
    effective_snapshot_date: date
    object_uri: str
    sha256: str
    bytes: int
    pipeline_run_id: str
    status: ManifestStatus
    http_etag: str | None = None
    http_last_modified: str | None = None
    validator_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize manifest values using interoperable ISO timestamps."""
        value = asdict(self)
        value["retrieved_at"] = self.retrieved_at.isoformat()
        value["effective_snapshot_date"] = self.effective_snapshot_date.isoformat()
        value["status"] = self.status.value
        return value


@dataclass(frozen=True)
class PipelineResult:
    """Summary returned by the end-to-end pipeline."""

    pipeline_run_id: str
    status: ManifestStatus
    sha256: str
    object_uri: str
    row_counts: dict[str, int]
    validation_status: str
