from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field


TriageCategory = Literal["Critical Bug/Crash", "Security/CVE", "Breaking Change", "Noise/Question"]
AlertSeverity = Literal["critical", "high", "medium", "low"]
AlertType = Literal["spike_detected", "critical_bug", "security_cve", "breaking_change", "anomaly"]


class IncomingGitHubEvent(BaseModel):
    """Normalized GitHub event received via Webhook or Poller."""
    repo: str = Field(description="Repository in owner/repo format.")
    event_type: str = Field(default="issue", description="Event kind: 'issue' or 'pull_request'.")
    action: str = Field(default="opened", description="GitHub event action: opened, reopened, edited, etc.")
    issue_number: int = Field(description="GitHub issue or pull request number.")
    title: str = Field(default="", description="Title of the issue.")
    author: str | None = Field(default=None, description="GitHub username of the author.")
    state: str = Field(default="open", description="State: open or closed.")
    labels: list[str] = Field(default_factory=list, description="Labels assigned to the issue.")
    body: str = Field(default="", description="Issue Markdown body text.")
    comments_count: int = Field(default=0, description="Total comment count.")
    upstream_created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 creation timestamp from GitHub.",
    )
    upstream_updated_at: str | None = Field(default=None, description="ISO 8601 update timestamp.")
    raw_payload: dict[str, Any] = Field(default_factory=dict, description="Raw JSON received from GitHub.")
    received_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Local arrival timestamp for lag-independent telemetry.",
    )


class TriageResult(BaseModel):
    """Classification and structured intelligence extracted from an incoming issue."""
    category: TriageCategory = Field(description="Category of the issue.")
    severity: AlertSeverity = Field(description="Assessed severity level.")
    summary: str = Field(description="Concise technical summary of the problem.")
    affected_versions: str | None = Field(default=None, description="Extracted library versions affected.")
    workaround: str | None = Field(default=None, description="Workaround, temporary fix, or patch.")
    is_alert_worthy: bool = Field(default=False, description="Whether this event should trigger an alert.")


class RealTimeAlert(BaseModel):
    """Real-time early warning alert emitted by the stream processor."""
    id: int | None = Field(default=None, description="Database ID if persisted.")
    repo: str = Field(description="Target repository.")
    issue_number: int | None = Field(default=None, description="Issue number if applicable.")
    title: str = Field(description="Alert headline or issue title.")
    alert_type: AlertType = Field(description="Category of alert: spike_detected, critical_bug, etc.")
    severity: AlertSeverity = Field(description="Severity rating.")
    summary: str = Field(description="Summary of the warning.")
    affected_versions: str | None = Field(default=None, description="Affected versions if identified.")
    workaround: str | None = Field(default=None, description="Available workaround if identified.")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Timestamp when alert was generated.",
    )
    payload: dict[str, Any] = Field(default_factory=dict, description="Contextual payload.")
