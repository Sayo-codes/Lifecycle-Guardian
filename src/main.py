"""Lifecycle Guardian — FastAPI webhook server & application entrypoint.

Endpoints
─────────
GET  /health                    Liveness probe + agent configuration summary
GET  /runs/{run_id}             Poll status of a background run
POST /webhooks/gitlab           GitLab merge-request webhook handler
POST /runs/manual               Ad-hoc manual trigger (dev/testing)
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    import uvicorn
    from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, status
    from pydantic import BaseModel
    _has_fastapi = True
except ImportError:
    _has_fastapi = False

from src.config import settings
from src.core.events import MergeRequestEvent
from src.core.orchestrator import LifecycleOrchestrator
from src.core.state import LifecycleContext, PipelineStatus
from src.utils.logger import logger

# ---------------------------------------------------------------------------
# Application bootstrap
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Lifecycle Guardian",
    description=(
        "Autonomous multi-agent DevSecOps orchestrator that activates after a GitLab "
        "Merge Request is merged and manages the full post-code lifecycle: "
        "Secure → Verify → Release → Monitor → Govern."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
) if _has_fastapi else None

orchestrator = LifecycleOrchestrator()

# In-memory run registry {run_id → LifecycleContext}.
# In production, replace with Redis or a database-backed store.
_runs: Dict[str, LifecycleContext] = {}


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class ManualRunRequest(BaseModel):
    project_id: int
    project_name: str
    mr_iid: int
    mr_title: str
    target_branch: str = "main"
    source_branch: str = "feature/update"
    merge_commit_sha: Optional[str] = "HEAD"
    author_username: str = "developer"


# ---------------------------------------------------------------------------
# Background task wrapper
# ---------------------------------------------------------------------------

async def _run_lifecycle(context: LifecycleContext) -> None:
    """Execute the full lifecycle pipeline and store the result in the run registry."""
    try:
        completed = await orchestrator.execute_lifecycle(context)
        _runs[context.run_id] = completed
    except Exception as e:
        logger.error("pipeline_uncaught_error", run_id=context.run_id, error=str(e))
        context.aborted = True
        context.abort_reason = f"Uncaught orchestrator error: {str(e)}"
        context.pipeline_status = PipelineStatus.BLOCKED
        _runs[context.run_id] = context


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _context_summary(ctx: LifecycleContext) -> Dict[str, Any]:
    """Serialise the key fields of a LifecycleContext for API responses."""
    return {
        "run_id": ctx.run_id,
        "project": ctx.project_name,
        "mr_iid": ctx.mr_iid,
        "mr_title": ctx.mr_title,
        "target_branch": ctx.target_branch,
        "author": ctx.author_username,
        "pipeline_status": ctx.pipeline_status.value if ctx.pipeline_status else "running",
        "aborted": ctx.aborted,
        "abort_reason": ctx.abort_reason,
        "total_duration_seconds": ctx.total_duration_seconds,
        "final_recommendation": ctx.final_recommendation,
        "stages": [
            {
                "stage": r.stage.value,
                "agent": r.agent_name,
                "status": r.status.value,
                "summary": r.summary,
                "duration_s": r.execution_time_seconds,
            }
            for r in ctx.results
        ],
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", tags=["System"])
async def health_check() -> Dict[str, Any]:
    """Liveness probe — returns service status and per-agent enable flags."""
    return {
        "status": "healthy",
        "service": "Lifecycle Guardian",
        "version": "0.1.0",
        "environment": settings.app_env,
        "dry_run": settings.dry_run,
        "pipeline_order": [
            "SecurityAgent",
            "VerificationAgent",
            "ReleaseAgent",
            "MonitoringAgent",
            "GovernanceAgent",
        ],
        "agents": {
            "security":     settings.enable_security_agent,
            "verification": settings.enable_verification_agent,
            "release":      settings.enable_release_agent,
            "monitoring":   settings.enable_monitoring_agent,
            "governance":   settings.enable_governance_agent,
        },
        "active_runs": len(_runs),
    }


@app.get("/runs/{run_id}", tags=["Runs"])
async def get_run_status(run_id: str) -> Dict[str, Any]:
    """
    Poll the status and summary of a lifecycle run.

    Returns HTTP 404 if the run_id is unknown (may still be starting up).
    Returns HTTP 200 with status="running" if the pipeline has not yet completed.
    """
    ctx = _runs.get(run_id)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found. It may still be initialising.",
        )

    response = _context_summary(ctx)

    # Attach the full Markdown report if the pipeline has completed
    if ctx.final_report:
        response["final_report"] = ctx.final_report

    return response


@app.post("/webhooks/gitlab", tags=["GitLab Webhook"], status_code=202)
async def gitlab_webhook(
    event_payload: Dict[str, Any],
    background_tasks: BackgroundTasks,
    x_gitlab_token: Optional[str] = Header(None, alias="X-Gitlab-Token"),
    x_gitlab_event: Optional[str] = Header(None, alias="X-Gitlab-Event"),
) -> Dict[str, Any]:
    """
    Accept GitLab webhook events.

    Activates the full Lifecycle Guardian pipeline when a Merge Request is
    merged.  All other event types are acknowledged but ignored.

    Authentication: validates the X-Gitlab-Token header against
    GITLAB_WEBHOOK_SECRET if configured.

    The pipeline runs in the background; poll GET /runs/{run_id} for status.
    """
    # ── Auth ──────────────────────────────────────────────────────────────
    if settings.gitlab_webhook_secret:
        if not x_gitlab_token or x_gitlab_token != settings.gitlab_webhook_secret:
            logger.warning(
                "webhook_auth_failed",
                token_provided=bool(x_gitlab_token),
                event=x_gitlab_event,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing X-Gitlab-Token header.",
            )

    # ── Event filter ──────────────────────────────────────────────────────
    object_kind = event_payload.get("object_kind")
    if object_kind != "merge_request":
        logger.debug(
            "webhook_ignored",
            reason="not_merge_request",
            object_kind=object_kind,
        )
        return {"status": "ignored", "reason": f"Unsupported event type: '{object_kind}'."}

    # ── Parse ─────────────────────────────────────────────────────────────
    try:
        mr_event = MergeRequestEvent.model_validate(event_payload)
    except Exception as exc:
        logger.error("webhook_parse_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Payload parse error: {exc}",
        )

    # ── Gate: only act on merged MRs ──────────────────────────────────────
    if not mr_event.is_merged:
        action = mr_event.object_attributes.action
        state = mr_event.object_attributes.state
        logger.info(
            "webhook_ignored",
            reason="mr_not_merged",
            action=action,
            state=state,
            mr_iid=mr_event.object_attributes.iid,
        )
        return {
            "status": "ignored",
            "reason": f"Merge request action='{action}' state='{state}' is not a merge event.",
        }

    # ── Build context & queue background pipeline ─────────────────────────
    context = orchestrator.build_context_from_event(mr_event)

    # Register immediately so GET /runs/{run_id} returns "running" straight away
    _runs[context.run_id] = context

    background_tasks.add_task(_run_lifecycle, context)

    logger.info(
        "pipeline_queued",
        run_id=context.run_id,
        project=mr_event.project.name,
        mr_iid=mr_event.object_attributes.iid,
        branch=mr_event.object_attributes.target_branch,
        author=mr_event.user.username,
    )

    return {
        "status": "accepted",
        "run_id": context.run_id,
        "project": mr_event.project.name,
        "mr_iid": mr_event.object_attributes.iid,
        "message": (
            "Lifecycle Guardian pipeline activated. "
            f"Poll GET /runs/{context.run_id} for real-time status."
        ),
    }


@app.post("/runs/manual", tags=["Runs"], status_code=202)
async def trigger_manual_run(
    req: ManualRunRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """
    Manually trigger a Lifecycle Guardian pipeline run.

    Useful for development, re-runs, and ad-hoc DevSecOps audits without
    waiting for a real GitLab merge event.
    """
    run_id = f"lg-manual-{uuid.uuid4().hex[:8]}"
    context = LifecycleContext(
        run_id=run_id,
        project_id=req.project_id,
        project_name=req.project_name,
        mr_iid=req.mr_iid,
        mr_title=req.mr_title,
        target_branch=req.target_branch,
        source_branch=req.source_branch,
        merge_commit_sha=req.merge_commit_sha,
        author_username=req.author_username,
    )

    _runs[run_id] = context
    background_tasks.add_task(_run_lifecycle, context)

    logger.info(
        "manual_run_queued",
        run_id=run_id,
        project=req.project_name,
        mr_iid=req.mr_iid,
    )

    return {
        "status": "accepted",
        "run_id": run_id,
        "message": (
            f"Manual pipeline run initiated for MR !{req.mr_iid}. "
            f"Poll GET /runs/{run_id} for real-time status."
        ),
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=(settings.app_env == "development"),
        log_level=settings.log_level.lower(),
    )
