"""Abstract Base Agent for Lifecycle Guardian."""

import time
from abc import ABC, abstractmethod
from typing import Optional
from src.core.state import LifecycleContext, LifecycleStage, AgentResult, AgentStatus
from src.core.gitlab_client import GitLabClient, gitlab_client
from src.utils.logger import logger


class BaseAgent(ABC):
    """Base class for all DevSecOps lifecycle agents.

    Subclasses must implement:
        is_enabled() → bool
        execute(context) → AgentResult

    The ``run()`` method handles cross-cutting concerns:
        - Skipping disabled agents
        - Timing execution
        - Catching unhandled exceptions and converting them to FAILED results
        - Structured entry / exit logging
    """

    def __init__(
        self,
        name: str,
        stage: LifecycleStage,
        client: Optional[GitLabClient] = None,
    ):
        self.name = name
        self.stage = stage
        self.client = client or gitlab_client

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------

    @abstractmethod
    def is_enabled(self) -> bool:
        """Return True when this agent should run."""

    @abstractmethod
    async def execute(self, context: LifecycleContext) -> AgentResult:
        """Perform the agent's core DevSecOps analysis.

        Args:
            context: The shared LifecycleContext. Agents **read** upstream stage
                     data from context fields and **write** their own findings
                     back into the appropriate field so downstream agents can
                     consume them.

        Returns:
            AgentResult — a fully-populated result with status, summary,
            details, recommendations, and any artifacts.
        """

    # ------------------------------------------------------------------
    # Execution wrapper (called by the Orchestrator)
    # ------------------------------------------------------------------

    async def run(self, context: LifecycleContext) -> AgentResult:
        """Execute the agent with timing, logging, and exception safety."""

        if not self.is_enabled():
            logger.info(
                "agent_disabled",
                agent=self.name,
                stage=self.stage.value,
            )
            return AgentResult(
                agent_name=self.name,
                stage=self.stage,
                status=AgentStatus.SKIPPED,
                summary=f"{self.name} is disabled in configuration.",
            )

        logger.info(
            "agent_start",
            agent=self.name,
            stage=self.stage.value,
            run_id=context.run_id,
            mr_iid=context.mr_iid,
        )

        start = time.monotonic()

        try:
            result = await self.execute(context)
        except Exception as exc:
            logger.error(
                "agent_unhandled_exception",
                agent=self.name,
                stage=self.stage.value,
                error=str(exc),
            )
            result = AgentResult(
                agent_name=self.name,
                stage=self.stage,
                status=AgentStatus.FAILED,
                summary=f"Unhandled exception in {self.name}: {exc}",
                details={"exception": str(exc), "type": type(exc).__name__},
            )

        result.execution_time_seconds = round(time.monotonic() - start, 3)

        logger.info(
            "agent_end",
            agent=self.name,
            stage=self.stage.value,
            status=result.status.value,
            duration_s=result.execution_time_seconds,
            blocking=result.is_blocking,
        )

        return result
