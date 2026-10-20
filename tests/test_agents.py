import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pytest
except ImportError:
    class _MockPytest:
        @staticmethod
        def fixture(func):
            return func
        class mark:
            @staticmethod
            def asyncio(func):
                return func
    pytest = _MockPytest()

from src.core.state import LifecycleContext, LifecycleStage, AgentStatus, PipelineStatus
from src.core.orchestrator import LifecycleOrchestrator
from src.agents.security_agent import SecurityAgent
from src.agents.verification_agent import VerificationAgent
from src.agents.release_agent import ReleaseAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.governance_agent import GovernanceAgent


@pytest.fixture
def clean_context() -> LifecycleContext:

    return LifecycleContext(
        run_id="lg-test-101",
        project_id=101,
        project_name="devsecops-demo",
        mr_iid=42,
        mr_title="feat(auth): Add secure JWT token rotation",
        target_branch="main",
        source_branch="feature/jwt-rotation",
        merge_commit_sha="a1b2c3d4e5f67890",
        author_username="lead-dev",
    )


@pytest.fixture
def vulnerable_context() -> LifecycleContext:
    return LifecycleContext(
        run_id="lg-test-vulnerable",
        project_id=102,
        project_name="vulnerable-demo",
        mr_iid=77,
        mr_title="fix: patch vulnerable dependency cve-test",
        target_branch="main",
        source_branch="fix/cve-test",
        merge_commit_sha="c3d4e5f6a1b2",
        author_username="dev-security",
    )


# Plain factory functions used by the standalone runner (run_tests.py).
# These are identical to the fixtures above but callable without pytest.
def make_clean_context() -> LifecycleContext:
    return LifecycleContext(
        run_id="lg-test-101",
        project_id=101,
        project_name="devsecops-demo",
        mr_iid=42,
        mr_title="feat(auth): Add secure JWT token rotation",
        target_branch="main",
        source_branch="feature/jwt-rotation",
        merge_commit_sha="a1b2c3d4e5f67890",
        author_username="lead-dev",
    )


def make_vulnerable_context() -> LifecycleContext:
    return LifecycleContext(
        run_id="lg-test-vulnerable",
        project_id=102,
        project_name="vulnerable-demo",
        mr_iid=77,
        mr_title="fix: patch vulnerable dependency cve-test",
        target_branch="main",
        source_branch="fix/cve-test",
        merge_commit_sha="c3d4e5f6a1b2",
        author_username="dev-security",
    )



# --- 1. SecurityAgent Tests ---

@pytest.mark.asyncio
async def test_security_agent_normal_flow(clean_context):
    agent = SecurityAgent()
    result = await agent.run(clean_context)

    assert result.status == AgentStatus.PASSED
    assert result.stage == LifecycleStage.SECURE
    assert "Security Gate PASSED" in result.summary

    # Verify structured output
    details = result.details
    assert details["decision"] == "pass"
    assert isinstance(details["risk_score"], float)
    assert details["risk_score"] < 35.0  # Clean/Low risk
    assert details["risk_level"] in ["Low", "Medium"]
    assert len(details["critical_issues"]) == 0
    assert "findings_summary" in details
    assert "policy_evaluation" in details
    assert details["policy_evaluation"]["gate_passed"] is True


@pytest.mark.asyncio
async def test_security_agent_blocks_on_critical_vulnerability(vulnerable_context):
    agent = SecurityAgent()
    result = await agent.run(vulnerable_context)

    assert result.status == AgentStatus.BLOCKED
    assert result.stage == LifecycleStage.SECURE
    assert "Security Gate BLOCKED" in result.summary

    details = result.details
    assert details["decision"] == "block"
    assert details["risk_score"] >= 35.0
    assert len(details["critical_issues"]) > 0
    assert any("CVE" in issue["id"] or "SAST" in issue["id"] for issue in details["critical_issues"])
    assert details["policy_evaluation"]["gate_passed"] is False


# --- 2. VerificationAgent Tests ---

@pytest.mark.asyncio
async def test_verification_agent_passes_clean_pipeline(clean_context):
    agent = VerificationAgent()
    result = await agent.run(clean_context)

    assert result.status == AgentStatus.PASSED
    assert result.stage == LifecycleStage.VERIFY
    assert "Verification PASSED" in result.summary

    details = result.details
    assert details["decision"] == "pass"
    assert details["overall_status"] == "passed"
    assert details["total_tests"] > 0
    assert details["failed_tests"] == 0
    assert details["staging_smoke_verified"] is True
    assert details["coverage"]["coverage_percent"] >= 85.0


@pytest.mark.asyncio
async def test_verification_agent_fails_on_broken_tests():
    broken_context = LifecycleContext(
        run_id="lg-broken-test",
        project_id=103,
        project_name="qa-demo",
        mr_iid=99,
        mr_title="refactor: broken-test in staging",
        target_branch="main",
        source_branch="refactor/qa",
        author_username="test-user",
    )
    agent = VerificationAgent()
    result = await agent.run(broken_context)

    assert result.status == AgentStatus.FAILED
    assert result.stage == LifecycleStage.VERIFY
    assert "Verification FAILED" in result.summary

    details = result.details
    assert details["decision"] == "fail"
    assert details["failed_tests"] > 0
    assert details["coverage"]["meets_threshold"] is False


# --- 3. ReleaseAgent Tests ---

@pytest.mark.asyncio
async def test_release_agent_safe_release(clean_context):
    # Simulate prior stages passing
    sec_agent = SecurityAgent()
    ver_agent = VerificationAgent()
    await sec_agent.run(clean_context)
    await ver_agent.run(clean_context)

    release_agent = ReleaseAgent()
    result = await release_agent.run(clean_context)

    assert result.status in [AgentStatus.PASSED, AgentStatus.WARNING]
    assert result.stage == LifecycleStage.RELEASE

    details = result.details
    assert details["safe_to_release"] is True
    assert details["decision"] == "release"
    assert details["bump_type"] == "minor"  # 'feat' in mr_title
    assert details["suggested_version"] == "1.3.0"
    assert details["tag_name"] == "v1.3.0"
    assert "release_notes" in result.artifacts
    assert "Release v1.3.0" in result.artifacts["release_notes"]


@pytest.mark.asyncio
async def test_release_agent_withholds_when_blocked(clean_context):
    # Mark context as aborted due to blocker
    clean_context.aborted = True
    clean_context.abort_reason = "Security gate blocked: Critical CVE detected."

    release_agent = ReleaseAgent()
    result = await release_agent.run(clean_context)

    assert result.status == AgentStatus.BLOCKED
    assert result.stage == LifecycleStage.RELEASE

    details = result.details
    assert details["safe_to_release"] is False
    assert details["decision"] == "withhold"
    assert len(details["blockers"]) > 0


# --- 4. MonitoringAgent Tests ---

@pytest.mark.asyncio
async def test_monitoring_agent_suggests_metrics_and_alerts(clean_context):
    agent = MonitoringAgent()
    result = await agent.run(clean_context)

    assert result.status == AgentStatus.PASSED
    assert result.stage == LifecycleStage.MONITOR
    assert "Monitoring Healthy" in result.summary

    details = result.details
    assert len(details["recommended_monitors"]) >= 2
    assert any("Authentication" in m["area"] for m in details["recommended_monitors"])

    # Key metrics verification
    metrics = details["key_metrics"]
    assert len(metrics) >= 3
    assert any("http_request_error_rate" in m["name"] for m in metrics)
    assert any("target_slo" in m for m in metrics)

    # Suggested alerts verification
    alerts = details["suggested_alerts"]
    assert len(alerts) >= 2
    assert any(a["severity"] == "P1 - Critical" for a in alerts)
    assert any("rollback" in a["action"].lower() for a in alerts)


# --- 5. GovernanceAgent Tests ---

@pytest.mark.asyncio
async def test_governance_agent_executive_summary_and_compliance(clean_context):
    # Populate stages
    await SecurityAgent().run(clean_context)
    await VerificationAgent().run(clean_context)
    await ReleaseAgent().run(clean_context)
    await MonitoringAgent().run(clean_context)

    gov_agent = GovernanceAgent()
    result = await gov_agent.run(clean_context)

    assert result.status == AgentStatus.PASSED
    assert result.stage == LifecycleStage.GOVERN

    details = result.details
    assert "executive_summary" in details
    assert len(details["executive_summary"]) > 50
    assert "Merge Request !42" in details["executive_summary"]

    compliance_notes = details["compliance_notes"]
    assert len(compliance_notes) >= 4
    frameworks = [item["framework"] for item in compliance_notes]
    assert "SOC 2 Type II" in frameworks
    assert "ISO/IEC 27001" in frameworks
    assert "OWASP Top 10" in frameworks

    assert "executive_report" in result.artifacts
    assert "# 🛡️ Lifecycle Guardian - Executive DevSecOps Audit" in result.artifacts["executive_report"]


# --- 6. Orchestrator End-to-End Tests ---

@pytest.mark.asyncio
async def test_orchestrator_pipeline_order(clean_context):
    """Agents must complete in: Secure → Verify → Release → Monitor → Govern."""
    orchestrator = LifecycleOrchestrator()
    final_context = await orchestrator.execute_lifecycle(clean_context)

    assert len(final_context.results) == 5
    expected_order = [
        LifecycleStage.SECURE,
        LifecycleStage.VERIFY,
        LifecycleStage.RELEASE,
        LifecycleStage.MONITOR,
        LifecycleStage.GOVERN,
    ]
    for result, expected_stage in zip(final_context.results, expected_order):
        assert result.stage == expected_stage, (
            f"Expected stage {expected_stage.value} at position, got {result.stage.value}"
        )


@pytest.mark.asyncio
async def test_orchestrator_complete_lifecycle(clean_context):
    """All agents pass on clean input; context is fully populated."""
    orchestrator = LifecycleOrchestrator()
    final_context = await orchestrator.execute_lifecycle(clean_context)

    assert not final_context.aborted
    assert final_context.security_passed
    assert final_context.verification_passed

    # Pipeline-level outputs
    assert final_context.pipeline_status == PipelineStatus.PASSED
    assert final_context.final_report is not None
    assert final_context.final_recommendation is not None
    assert final_context.completed_at is not None

    # Verify context propagation — security data exists before release ran
    assert final_context.security_report.get("decision") == "pass"

    sec_res = final_context.get_result(LifecycleStage.SECURE)
    assert sec_res.details["decision"] == "pass"

    rel_res = final_context.get_result(LifecycleStage.RELEASE)
    assert rel_res.details["safe_to_release"] is True

    gov_res = final_context.get_result(LifecycleStage.GOVERN)
    assert gov_res.details["all_compliant"] is True


@pytest.mark.asyncio
async def test_orchestrator_final_report_structure(clean_context):
    """Final Markdown report must contain all required sections."""
    orchestrator = LifecycleOrchestrator()
    final_context = await orchestrator.execute_lifecycle(clean_context)

    report = final_context.final_report
    assert report is not None

    # Header
    assert "# 🛡️ Lifecycle Guardian" in report

    # Overall status banner
    assert "LIFECYCLE GUARDIAN" in report

    # Metadata section
    assert "## 📋 Run Metadata" in report

    # Executive summary
    assert "## 🧭 Executive Summary" in report

    # Scorecard section
    assert "## 🚦 Pipeline Scorecard" in report

    # All stage names appear in the report
    for stage in ["Secure", "Verify", "Release", "Monitor", "Govern"]:
        assert stage in report, f"Stage '{stage}' missing from final report"

    # Stage-by-stage findings
    assert "## 🔍 Stage-by-Stage Findings" in report

    # Final recommendation
    assert "## 🎯 Final Recommendation" in report
    assert final_context.final_recommendation in report



@pytest.mark.asyncio
async def test_orchestrator_security_gate_blocks_pipeline(vulnerable_context):
    """When Security blocks: Verify / Release / Monitor are SKIPPED; Govern still runs."""
    orchestrator = LifecycleOrchestrator()
    final_context = await orchestrator.execute_lifecycle(vulnerable_context)

    assert final_context.aborted is True
    assert final_context.pipeline_status == PipelineStatus.BLOCKED

    sec_res = final_context.get_result(LifecycleStage.SECURE)
    assert sec_res.status == AgentStatus.BLOCKED

    # Verify, Release, Monitor should all be skipped
    for skipped_stage in [LifecycleStage.VERIFY, LifecycleStage.RELEASE, LifecycleStage.MONITOR]:
        res = final_context.get_result(skipped_stage)
        assert res is not None, f"{skipped_stage.value} result missing"
        assert res.status == AgentStatus.SKIPPED, (
            f"{skipped_stage.value} should be SKIPPED, got {res.status.value}"
        )

    # Governance always runs — even on blocked pipelines
    gov_res = final_context.get_result(LifecycleStage.GOVERN)
    assert gov_res is not None
    assert gov_res.status in [AgentStatus.PASSED, AgentStatus.WARNING]

    # Final report must still be generated and mention the block
    assert final_context.final_report is not None
    assert "BLOCKED" in final_context.final_report
    assert "failed automated DevSecOps validation" in gov_res.details["executive_summary"]


@pytest.mark.asyncio
async def test_orchestrator_context_propagation(clean_context):
    """Security and Verification data must be available to Release agent."""
    orchestrator = LifecycleOrchestrator()
    final_context = await orchestrator.execute_lifecycle(clean_context)

    # Security wrote security_report; Release read it for release notes
    assert final_context.security_report != {}
    assert "risk_score" in final_context.security_report

    # Verification wrote verification_report; Release read it
    assert final_context.verification_report != {}
    assert "coverage" in final_context.verification_report

    # Release used both to make its decision
    rel = final_context.release_info
    assert rel.get("safe_to_release") is True
    assert rel.get("tag_name") is not None

    # Release notes reference the security risk score
    release_notes = final_context.get_result(LifecycleStage.RELEASE)
    assert release_notes is not None
    assert "Risk Score" in release_notes.artifacts.get("release_notes", "")


@pytest.mark.asyncio
async def test_orchestrator_final_recommendation_on_pass(clean_context):
    """Recommendation on PASSED run must mention the release tag."""
    orchestrator = LifecycleOrchestrator()
    final_context = await orchestrator.execute_lifecycle(clean_context)

    assert final_context.pipeline_status == PipelineStatus.PASSED
    rec = final_context.final_recommendation
    assert rec is not None
    assert "live" in rec.lower() or "cleared" in rec.lower()


@pytest.mark.asyncio
async def test_orchestrator_final_recommendation_on_block(vulnerable_context):
    """Recommendation on BLOCKED run must reference the blocker cause."""
    orchestrator = LifecycleOrchestrator()
    final_context = await orchestrator.execute_lifecycle(vulnerable_context)

    assert final_context.pipeline_status == PipelineStatus.BLOCKED
    rec = final_context.final_recommendation
    assert rec is not None
    assert "BLOCKED" in rec

