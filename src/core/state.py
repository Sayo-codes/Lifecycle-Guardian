"""Shared context and execution state for multi-agent lifecycle runs."""

from enum import Enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class LifecycleStage(str, Enum):
    SECURE = "Secure"
    VERIFY = "Verify"
    RELEASE = "Release"
    MONITOR = "Monitor"
    GOVERN = "Govern"


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    WARNING = "warning"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineStatus(str, Enum):
    """Overall status of a completed Lifecycle Guardian run."""
    PASSED = "PASSED"
    BLOCKED = "BLOCKED"
    PARTIAL = "PARTIAL"   # Some agents skipped / warned, none blocked


class AgentResult(BaseModel):
    """Result returned by an individual agent."""

    agent_name: str
    stage: LifecycleStage
    status: AgentStatus
    summary: str
    details: Dict[str, Any] = Field(default_factory=dict)
    recommendations: List[str] = Field(default_factory=list)
    artifacts: Dict[str, Any] = Field(default_factory=dict)
    execution_time_seconds: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_blocking(self) -> bool:
        """True when this result should halt downstream agents."""
        return self.status in [AgentStatus.BLOCKED, AgentStatus.FAILED]

    @property
    def status_icon(self) -> str:
        """Emoji icon for Markdown reports."""
        return {
            AgentStatus.PASSED: "✅",
            AgentStatus.WARNING: "⚠️",
            AgentStatus.BLOCKED: "🛑",
            AgentStatus.FAILED: "❌",
            AgentStatus.SKIPPED: "⏭️",
            AgentStatus.PENDING: "⏳",
            AgentStatus.RUNNING: "🔄",
        }.get(self.status, "❓")


class LifecycleContext(BaseModel):
    """Shared mutable state passed sequentially through all agents."""

    # Identity
    run_id: str
    project_id: int
    project_name: str
    mr_iid: int
    mr_title: str
    target_branch: str
    source_branch: str
    merge_commit_sha: Optional[str] = None
    author_username: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    # Per-stage structured data (written by agents, read by downstream agents)
    security_report: Dict[str, Any] = Field(default_factory=dict)
    verification_report: Dict[str, Any] = Field(default_factory=dict)
    release_info: Dict[str, Any] = Field(default_factory=dict)
    monitoring_data: Dict[str, Any] = Field(default_factory=dict)
    governance_audit: Dict[str, Any] = Field(default_factory=dict)

    # Execution tracking
    results: List[AgentResult] = Field(default_factory=list)
    aborted: bool = False
    abort_reason: Optional[str] = None

    # Final outputs written by the Orchestrator
    pipeline_status: Optional[PipelineStatus] = None
    final_report: Optional[str] = None      # Markdown report
    final_recommendation: Optional[str] = None

    # ---------------------------------------------------------------------------
    # Mutation helpers
    # ---------------------------------------------------------------------------

    def add_result(self, result: AgentResult) -> None:
        """Append a result; flip aborted flag if the result is a blocker."""
        self.results.append(result)
        if result.is_blocking and not self.aborted:
            self.aborted = True
            self.abort_reason = (
                f"[{result.stage.value}] {result.agent_name} blocked the pipeline: {result.summary}"
            )

    # ---------------------------------------------------------------------------
    # Convenience accessors
    # ---------------------------------------------------------------------------

    def get_result(self, stage: LifecycleStage) -> Optional[AgentResult]:
        """Return the AgentResult for a given stage, or None."""
        for res in self.results:
            if res.stage == stage:
                return res
        return None

    @property
    def has_blockers(self) -> bool:
        """True if any agent returned BLOCKED or FAILED."""
        return any(res.is_blocking for res in self.results)

    @property
    def security_passed(self) -> bool:
        """Security stage cleared (passed, warned, or skipped)."""
        res = self.get_result(LifecycleStage.SECURE)
        if not res:
            return True
        return res.status in [AgentStatus.PASSED, AgentStatus.WARNING, AgentStatus.SKIPPED]

    @property
    def verification_passed(self) -> bool:
        """Verification stage cleared (passed or skipped)."""
        res = self.get_result(LifecycleStage.VERIFY)
        if not res:
            return True
        return res.status in [AgentStatus.PASSED, AgentStatus.SKIPPED]

    @property
    def total_duration_seconds(self) -> float:
        """Sum of individual agent execution times."""
        return round(sum(r.execution_time_seconds for r in self.results), 3)
