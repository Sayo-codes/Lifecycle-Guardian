"""Multi-agent orchestrator — the central nervous system of Lifecycle Guardian.

Execution order (fixed pipeline):
    1. SecurityAgent   → populates context.security_report
    2. VerificationAgent → populates context.verification_report
    3. ReleaseAgent    → reads security + verification reports before deciding
    4. MonitoringAgent → populates context.monitoring_data
    5. GovernanceAgent → always runs; seals audit, generates final Markdown report

Gating rules:
    • If SecurityAgent blocks  → Verification, Release, Monitor are SKIPPED.
    • If VerificationAgent fails → Release is SKIPPED (Monitor still runs).
    • GovernanceAgent ALWAYS runs to produce the executive report.

Context propagation:
    • Each agent writes structured data into the shared LifecycleContext fields
      (security_report, verification_report, release_info, monitoring_data).
    • Downstream agents read those fields to make their decisions.

Final report:
    • After all agents have run, the Orchestrator compiles a clean Markdown
      report and stores it in context.final_report.
    • The Orchestrator also determines context.pipeline_status (PASSED / BLOCKED).
"""

import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List, Optional

from src.core.events import MergeRequestEvent
from src.core.state import (
    AgentResult,
    AgentStatus,
    LifecycleContext,
    LifecycleStage,
    PipelineStatus,
)
from src.agents.base import BaseAgent
from src.agents.security_agent import SecurityAgent
from src.agents.verification_agent import VerificationAgent
from src.agents.release_agent import ReleaseAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.governance_agent import GovernanceAgent
from src.utils.logger import logger


# ---------------------------------------------------------------------------
# Stage descriptor — binds a stage enum value to an agent instance and
# carries a boolean flag indicating whether a block halts subsequent stages.
# ---------------------------------------------------------------------------
@dataclass
class StageConfig:
    agent: BaseAgent
    is_gate: bool = False         # True → a BLOCKED/FAILED result aborts downstream stages
    always_run: bool = False      # True → runs even when the pipeline is aborted (Govern)


class LifecycleOrchestrator:
    """Coordinates the post-merge DevSecOps pipeline across five ordered stages."""

    def __init__(self, stages: Optional[List[StageConfig]] = None):
        self.stages: List[StageConfig] = stages or [
            StageConfig(SecurityAgent(),     is_gate=True),
            StageConfig(VerificationAgent(), is_gate=True),
            StageConfig(ReleaseAgent(),      is_gate=False),
            StageConfig(MonitoringAgent(),   is_gate=False),
            StageConfig(GovernanceAgent(),   always_run=True),
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_context_from_event(self, event: MergeRequestEvent) -> LifecycleContext:
        """Create a fresh LifecycleContext from a GitLab Merge Request event."""
        attr = event.object_attributes
        return LifecycleContext(
            run_id=f"lg-{uuid.uuid4().hex[:8]}",
            project_id=event.project.id,
            project_name=event.project.name,
            mr_iid=attr.iid,
            mr_title=attr.title,
            target_branch=attr.target_branch,
            source_branch=attr.source_branch,
            merge_commit_sha=attr.merge_commit_sha,
            author_username=event.user.username,
        )

    async def execute_lifecycle(self, context: LifecycleContext) -> LifecycleContext:
        """
        Run all stages in order, propagating context between agents.

        Returns the completed LifecycleContext which includes:
            - Individual AgentResult objects (context.results)
            - Populated stage-specific data fields
            - context.pipeline_status  (PASSED / BLOCKED / PARTIAL)
            - context.final_report     (clean Markdown summary)
        """
        logger.info(
            "pipeline_started",
            run_id=context.run_id,
            project=context.project_name,
            mr=f"!{context.mr_iid}",
            branch=context.target_branch,
        )

        for stage_cfg in self.stages:
            agent = stage_cfg.agent
            stage_name = agent.stage.value

            # ---- Decide whether to skip this agent ----
            if context.aborted and not stage_cfg.always_run:
                logger.warning(
                    "stage_skipped",
                    agent=agent.name,
                    stage=stage_name,
                    reason=context.abort_reason,
                )
                context.add_result(
                    AgentResult(
                        agent_name=agent.name,
                        stage=agent.stage,
                        status=AgentStatus.SKIPPED,
                        summary=(
                            f"Skipped — pipeline was blocked before reaching {stage_name}: "
                            f"{context.abort_reason}"
                        ),
                    )
                )
                continue

            # ---- Run the agent ----
            logger.info("stage_running", agent=agent.name, stage=stage_name, run_id=context.run_id)
            result = await agent.run(context)
            context.add_result(result)

            logger.info(
                "stage_completed",
                agent=agent.name,
                stage=stage_name,
                status=result.status.value,
                duration_s=result.execution_time_seconds,
                blocking=result.is_blocking,
            )

            # ---- Gate evaluation — abort pipeline if gate fails ----
            if stage_cfg.is_gate and result.is_blocking:
                logger.warning(
                    "gate_triggered",
                    agent=agent.name,
                    stage=stage_name,
                    summary=result.summary,
                )
                # context.aborted is already True (set inside add_result)
                # Continue looping so remaining stages can be marked SKIPPED
                # and GovernanceAgent can still run.

        # ---- Finalise ----
        context.completed_at = datetime.now(timezone.utc)
        context.pipeline_status = self._determine_pipeline_status(context)
        context.final_report = self._generate_final_report(context)
        context.final_recommendation = self._generate_recommendation(context)

        logger.info(
            "pipeline_completed",
            run_id=context.run_id,
            status=context.pipeline_status.value,
            duration_s=context.total_duration_seconds,
            stages_run=len(context.results),
        )

        return context

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _determine_pipeline_status(self, context: LifecycleContext) -> PipelineStatus:
        """Compute the overall run status from individual agent results."""
        if context.has_blockers:
            return PipelineStatus.BLOCKED
        if any(r.status == AgentStatus.WARNING for r in context.results):
            return PipelineStatus.PARTIAL
        return PipelineStatus.PASSED

    def _generate_recommendation(self, context: LifecycleContext) -> str:
        """Produce a single, actionable final recommendation sentence."""
        status = context.pipeline_status

        if status == PipelineStatus.PASSED:
            rel = context.release_info
            tag = rel.get("tag_name", "latest")
            return (
                f"All DevSecOps gates cleared. Release {tag} is live on "
                f"`{context.target_branch}`. Monitor post-deploy SLOs for 30 minutes."
            )

        if status == PipelineStatus.BLOCKED:
            blocker = context.abort_reason or "unknown blocker"
            return (
                f"Pipeline is BLOCKED. Resolve the following before re-merging: {blocker}"
            )

        # PARTIAL
        warnings = [
            r.agent_name for r in context.results if r.status == AgentStatus.WARNING
        ]
        return (
            f"Pipeline completed with warnings from: {', '.join(warnings)}. "
            f"Review recommendations from each flagged agent before next release."
        )

    def _generate_final_report(self, context: LifecycleContext) -> str:
        """
        Compile a polished, professional Markdown report with:
          - Executive summary banner
          - Run metadata header
          - At-a-glance pipeline scorecard
          - Per-stage detailed findings
          - Compliance verification table
          - Final recommendation
        """
        status = self._determine_pipeline_status(context)
        rec = self._generate_recommendation(context)

        # ── Status banner ────────────────────────────────────────────────
        banner = {
            PipelineStatus.PASSED:  (
                "```\n"
                "╔══════════════════════════════════════════════════╗\n"
                "║   ✅  LIFECYCLE GUARDIAN — PIPELINE PASSED       ║\n"
                "╚══════════════════════════════════════════════════╝\n"
                "```"
            ),
            PipelineStatus.BLOCKED: (
                "```\n"
                "╔══════════════════════════════════════════════════╗\n"
                "║   🛑  LIFECYCLE GUARDIAN — PIPELINE BLOCKED      ║\n"
                "╚══════════════════════════════════════════════════╝\n"
                "```"
            ),
            PipelineStatus.PARTIAL: (
                "```\n"
                "╔══════════════════════════════════════════════════╗\n"
                "║   ⚠️  LIFECYCLE GUARDIAN — PIPELINE PARTIAL      ║\n"
                "╚══════════════════════════════════════════════════╝\n"
                "```"
            ),
        }[status]

        # ── Executive summary text ────────────────────────────────────────
        gov_res = context.get_result(LifecycleStage.GOVERN)
        exec_summary = (
            gov_res.details.get("executive_summary", "")
            if gov_res and gov_res.details
            else rec
        )

        # ── Per-stage scorecard rows ──────────────────────────────────────
        scorecard_rows: List[str] = []
        for res in context.results:
            dur_str = f"{res.execution_time_seconds:.2f}s"
            scorecard_rows.append(
                f"| {res.status_icon} | **{res.stage.value}** | `{res.agent_name}` "
                f"| `{res.status.value.upper()}` | {dur_str} |"
            )

        # ── Elapsed / completed ───────────────────────────────────────────
        completed_str = (
            context.completed_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            if context.completed_at else "N/A"
        )

        lines: List[str] = []

        # ════════════════════════════════════════════════
        # HEADER
        # ════════════════════════════════════════════════
        lines += [
            "# 🛡️ Lifecycle Guardian — Post-Merge DevSecOps Report",
            "",
            banner,
            "",
            "---",
        ]

        # ════════════════════════════════════════════════
        # RUN METADATA
        # ════════════════════════════════════════════════
        lines += [
            "",
            "## 📋 Run Metadata",
            "",
            "| Field | Value |",
            "| :--- | :--- |",
            f"| **Run ID** | `{context.run_id}` |",
            f"| **Project** | `{context.project_name}` |",
            f"| **Merge Request** | !{context.mr_iid} — *{context.mr_title}* |",
            f"| **Author** | @{context.author_username} |",
            f"| **Source Branch** | `{context.source_branch}` |",
            f"| **Target Branch** | `{context.target_branch}` |",
            f"| **Commit SHA** | `{context.merge_commit_sha or 'HEAD'}` |",
            f"| **Completed** | {completed_str} |",
            f"| **Pipeline Duration** | {context.total_duration_seconds}s |",
            f"| **Overall Status** | {context.get_result(LifecycleStage.SECURE).status_icon if context.get_result(LifecycleStage.SECURE) else '❓'} **{status.value}** |",
            "",
            "---",
        ]

        # ════════════════════════════════════════════════
        # EXECUTIVE SUMMARY
        # ════════════════════════════════════════════════
        lines += [
            "",
            "## 🧭 Executive Summary",
            "",
            f"> {exec_summary}",
            "",
            "---",
        ]

        # ════════════════════════════════════════════════
        # PIPELINE SCORECARD
        # ════════════════════════════════════════════════
        lines += [
            "",
            "## 🚦 Pipeline Scorecard",
            "",
            "| | Stage | Agent | Status | Duration |",
            "| :---: | :--- | :--- | :---: | ---: |",
        ]
        lines += scorecard_rows
        lines += [""]

        # Quick legend
        lines += [
            "> ✅ Passed · ⚠️ Warning · 🛑 Blocked · ❌ Failed · ⏭️ Skipped",
            "",
            "---",
        ]

        # ════════════════════════════════════════════════
        # STAGE-BY-STAGE FINDINGS
        # ════════════════════════════════════════════════
        lines += [
            "",
            "## 🔍 Stage-by-Stage Findings",
        ]

        stage_icons = {
            LifecycleStage.SECURE:  "🔒",
            LifecycleStage.VERIFY:  "🧪",
            LifecycleStage.RELEASE: "🚀",
            LifecycleStage.MONITOR: "📈",
            LifecycleStage.GOVERN:  "🏛️",
        }

        for i, res in enumerate(context.results, start=1):
            s_icon = stage_icons.get(res.stage, "•")
            lines += [
                "",
                f"### {s_icon} Stage {i}: {res.stage.value}",
                "",
                f"**Agent**: `{res.agent_name}`  |  "
                f"**Status**: {res.status_icon} `{res.status.value.upper()}`  |  "
                f"**Duration**: {res.execution_time_seconds:.2f}s",
                "",
                f"> {res.summary}",
                "",
            ]

            # --- Stage-specific rich detail ---

            if res.stage == LifecycleStage.SECURE and res.details:
                d = res.details
                fsum = d.get("findings_summary", {})
                gate = "PASS ✅" if d.get("decision") == "pass" else "BLOCK 🛑"
                lines += [
                    "**Security Analysis:**",
                    "",
                    f"| Metric | Value |",
                    f"| :--- | :--- |",
                    f"| Risk Score | **{d.get('risk_score', 0)}/100** ({d.get('risk_level', 'N/A')}) |",
                    f"| Gate Decision | {gate} |",
                    f"| Policy | `{d.get('policy_evaluation', {}).get('policy', 'N/A').upper()}` |",
                    f"| Critical CVEs | `{fsum.get('critical', 0)}` |",
                    f"| High CVEs | `{fsum.get('high', 0)}` |",
                    f"| Medium | `{fsum.get('medium', 0)}` |",
                    f"| Low | `{fsum.get('low', 0)}` |",
                    "",
                ]
                # List critical issues
                critical = d.get("critical_issues", [])
                if critical:
                    lines += ["**⚠️ Critical Issues Requiring Triage:**", ""]
                    for issue in critical:
                        lines.append(
                            f"- **[{issue.get('severity')}] {issue.get('id')}** — "
                            f"{issue.get('title')}  \n"
                            f"  📍 `{issue.get('location')}` (CVSS: {issue.get('cvss_score')})  \n"
                            f"  💊 *Fix*: {issue.get('remediation')}"
                        )
                    lines.append("")

            elif res.stage == LifecycleStage.VERIFY and res.details:
                d = res.details
                suites = d.get("test_suites", {})
                cov = d.get("coverage", {})
                cov_delta = cov.get("coverage_delta", 0)
                delta_str = f"+{cov_delta}%" if cov_delta >= 0 else f"{cov_delta}%"
                lines += [
                    "**Verification Results:**",
                    "",
                    "| Suite | Passed | Failed | Skipped | Duration |",
                    "| :--- | ---: | ---: | ---: | ---: |",
                ]
                for suite_name, suite in suites.items():
                    if isinstance(suite, dict) and "total" in suite:
                        lines.append(
                            f"| {suite_name.replace('_', ' ').title()} "
                            f"| {suite.get('passed', '–')} "
                            f"| {suite.get('failed', '–')} "
                            f"| {suite.get('skipped', '–')} "
                            f"| {suite.get('duration', '–')} |"
                        )
                lines += [
                    "",
                    f"**Code Coverage**: `{cov.get('coverage_percent', 'N/A')}%` ({delta_str})  "
                    f"**Threshold**: `{cov.get('threshold_percent', 85)}%`  "
                    f"{'✅ Met' if cov.get('meets_threshold') else '❌ Below threshold'}",
                    "",
                ]

            elif res.stage == LifecycleStage.RELEASE and res.details:
                d = res.details
                if d.get("safe_to_release"):
                    lines += [
                        "**Release Details:**",
                        "",
                        f"| Field | Value |",
                        f"| :--- | :--- |",
                        f"| Tag | `{d.get('tag_name')}` |",
                        f"| Version Bump | `{d.get('bump_type', 'patch').upper()}` "
                        f"({d.get('current_version')} → {d.get('suggested_version')}) |",
                        f"| Published | {'✅ Yes' if d.get('release_published') else '⚠️ Failed'} |",
                        f"| Target Commit | `{context.merge_commit_sha or 'HEAD'}` |",
                        "",
                    ]
                else:
                    blockers = d.get("blockers", [])
                    lines += [
                        "**Release Withheld — Blocking Conditions:**", ""
                    ]
                    for b in blockers:
                        lines.append(f"- 🛑 {b}")
                    lines.append("")

            elif res.stage == LifecycleStage.MONITOR and res.details:
                d = res.details
                tel = d.get("telemetry_baseline", {})
                lines += [
                    "**Post-Deploy Telemetry Baseline:**",
                    "",
                    f"| Metric | Value | SLO |",
                    f"| :--- | :--- | :--- |",
                    f"| Error Rate | `{tel.get('error_rate_percent', 'N/A')}%` | `< 0.05%` |",
                    f"| P95 Latency | `{tel.get('latency_p95_ms', 'N/A')}ms` | `< 200ms` |",
                    f"| P99 Latency | `{tel.get('latency_p99_ms', 'N/A')}ms` | `< 400ms` |",
                    f"| Active Alerts | `{tel.get('active_alerts_count', 0)}` | `0` |",
                    f"| Health Score | `{tel.get('health_score', 'N/A')}` | `> 99.5` |",
                    "",
                    "**Recommended Monitoring Focus Areas:**", "",
                ]
                for m in d.get("recommended_monitors", []):
                    lines.append(f"- **{m.get('area')}**: {m.get('focus')}")
                lines.append("")

                alerts = d.get("suggested_alerts", [])
                if alerts:
                    lines += ["**Active Alert Rules:**", ""]
                    for a in alerts:
                        lines.append(
                            f"- `{a.get('alert_name')}` [{a.get('severity')}]  \n"
                            f"  📐 *Trigger*: {a.get('condition')}  \n"
                            f"  ⚡ *Action*: {a.get('action')}"
                        )
                    lines.append("")

            elif res.stage == LifecycleStage.GOVERN and res.details:
                d = res.details
                lines += [
                    f"**Audit Trail ID**: `{d.get('audit_trail_id', context.run_id)}`  ",
                    f"**All Compliant**: {'✅ Yes' if d.get('all_compliant') else '❌ No'}",
                    "",
                ]

            # Recommendations (all stages)
            if res.recommendations and res.status != AgentStatus.SKIPPED:
                lines += ["**Recommendations:**", ""]
                for rec_item in res.recommendations:
                    lines.append(f"- {rec_item}")
                lines.append("")

        # ════════════════════════════════════════════════
        # COMPLIANCE VERIFICATION TABLE
        # ════════════════════════════════════════════════
        if gov_res and gov_res.details.get("compliance_notes"):
            lines += [
                "---",
                "",
                "## 📜 Compliance Verification",
                "",
                "| Framework | Control | Status | Evidence |",
                "| :--- | :--- | :---: | :--- |",
            ]
            for note in gov_res.details["compliance_notes"]:
                c_icon = "✅" if note["status"].lower() == "compliant" else "❌"
                lines.append(
                    f"| **{note['framework']}** | `{note['control']}` "
                    f"| {c_icon} {note['status']} | {note['evidence']} |"
                )
            lines += [""]

        # ════════════════════════════════════════════════
        # FINAL RECOMMENDATION
        # ════════════════════════════════════════════════
        rec = self._generate_recommendation(context)
        recommendation_icon = {
            PipelineStatus.PASSED:  "🟢",
            PipelineStatus.BLOCKED: "🔴",
            PipelineStatus.PARTIAL: "🟡",
        }[status]

        lines += [
            "---",
            "",
            "## 🎯 Final Recommendation",
            "",
            f"{recommendation_icon} **{status.value}** — {rec}",
            "",
            "---",
            "",
            f"*🤖 Generated autonomously by **Lifecycle Guardian** · "
            f"Run `{context.run_id}` · {completed_str} · "
            f"Total pipeline time: `{context.total_duration_seconds}s`*",
        ]

        return "\n".join(lines)

