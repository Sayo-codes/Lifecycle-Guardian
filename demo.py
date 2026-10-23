#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║                 LIFECYCLE GUARDIAN — DEMO MODE                   ║
║   Runs the full post-merge DevSecOps pipeline without GitLab.   ║
╚══════════════════════════════════════════════════════════════════╝

Usage:
    python demo.py              # Run the PASSED scenario
    python demo.py --blocked    # Run the BLOCKED (critical CVE) scenario
    python demo.py --output     # Save final report to report.md

No GitLab credentials or webhook required.
"""

import asyncio
import sys
import os
import argparse

# Force UTF-8 output on Windows so box-drawing characters and emoji render correctly
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Make sure src/ is importable from the project root
sys.path.insert(0, os.path.dirname(__file__))

from src.core.state import LifecycleContext
from src.core.orchestrator import LifecycleOrchestrator


# ──────────────────────────────────────────────────────────────────────────────
# ANSI colour helpers
# ──────────────────────────────────────────────────────────────────────────────

_COLOURS = {
    "reset":  "\033[0m",
    "bold":   "\033[1m",
    "dim":    "\033[2m",
    "green":  "\033[92m",
    "yellow": "\033[93m",
    "red":    "\033[91m",
    "cyan":   "\033[96m",
    "blue":   "\033[94m",
    "white":  "\033[97m",
}

def _c(text: str, *styles: str) -> str:
    """Wrap text with ANSI colour codes (skipped on non-TTY)."""
    if not sys.stdout.isatty():
        return text
    prefix = "".join(_COLOURS.get(s, "") for s in styles)
    return f"{prefix}{text}{_COLOURS['reset']}"


# ──────────────────────────────────────────────────────────────────────────────
# Demo data
# ──────────────────────────────────────────────────────────────────────────────

DEMO_CONTEXT_PASSED = LifecycleContext(
    run_id="lg-demo-passed",
    project_id=1001,
    project_name="payments-service",
    mr_iid=142,
    mr_title="feat(checkout): add Stripe SCA 3DS2 payment flow",
    target_branch="main",
    source_branch="feature/stripe-sca-3ds2",
    merge_commit_sha="a1b2c3d4e5f67890abcdef1234567890abcdef12",
    author_username="sarah.chen",
)

# Pre-inject two critical CVE findings so SecurityAgent blocks without needing
# any special MR title keywords — makes the demo self-contained.
_BLOCKED_FINDINGS = [
    {
        "id": "CVE-2024-21626",
        "tool": "Container Scanning (Trivy)",
        "title": "runc container breakout — host namespace escape via fd leak",
        "severity": "Critical",
        "location": "Dockerfile:3 — base image node:18-alpine",
        "cvss_score": 9.8,
        "description": (
            "runc < 1.1.12 leaks an internal file descriptor that allows a "
            "malicious container process to escape to the host namespace."
        ),
        "remediation": "Upgrade base image to node:18-alpine3.19 (runc >= 1.1.12).",
    },
    {
        "id": "SAST-CWE-89",
        "tool": "GitLab SAST (Semgrep)",
        "title": "SQL Injection — unparameterised query in UserRepository",
        "severity": "High",
        "location": "src/repositories/user_repository.js:204",
        "cvss_score": 8.6,
        "description": (
            "Direct string interpolation of user-controlled input into a raw "
            "database query allows attackers to execute arbitrary SQL."
        ),
        "remediation": "Replace string interpolation with parameterised prepared statements.",
    },
    {
        "id": "SECRET-001",
        "tool": "Secret Detection (Gitleaks)",
        "title": "Stripe live secret key committed to source",
        "severity": "Critical",
        "location": "config/stripe.js:8 — sk_live_xxxxxxxxxxxx",
        "cvss_score": 9.5,
        "description": "A live Stripe secret key was detected in the repository history.",
        "remediation": "Rotate the key immediately and move credentials to environment variables.",
    },
    {
        "id": "DEP-CVE-2023-26136",
        "tool": "Dependency Scanning (Gemnasium)",
        "title": "tough-cookie prototype pollution via ReGDoS",
        "severity": "Medium",
        "location": "package-lock.json — tough-cookie@2.5.0",
        "cvss_score": 5.6,
        "description": "tough-cookie < 4.1.3 is vulnerable to prototype pollution.",
        "remediation": "Upgrade tough-cookie to >= 4.1.3.",
    },
]

DEMO_CONTEXT_BLOCKED = LifecycleContext(
    run_id="lg-demo-blocked",
    project_id=1002,
    project_name="auth-service",
    mr_iid=77,
    mr_title="fix(deps): bump lodash and axios to latest",
    target_branch="main",
    source_branch="fix/dependency-updates",
    merge_commit_sha="deadbeef1234567890abcdef0987654321fedcba",
    author_username="alex.morgan",
    # Pre-load findings so SecurityAgent picks them up immediately
    security_report={"findings": _BLOCKED_FINDINGS},
)



# ──────────────────────────────────────────────────────────────────────────────
# Console output helpers
# ──────────────────────────────────────────────────────────────────────────────

def _print_header(scenario: str) -> None:
    print()
    print(_c("╔══════════════════════════════════════════════════════════════════╗", "cyan", "bold"))
    print(_c("║", "cyan", "bold") + _c("                 LIFECYCLE GUARDIAN — DEMO MODE                   ", "white", "bold") + _c("║", "cyan", "bold"))
    print(_c("║", "cyan", "bold") + _c(f"   Scenario: {scenario:<55}", "dim") + _c("║", "cyan", "bold"))
    print(_c("╚══════════════════════════════════════════════════════════════════╝", "cyan", "bold"))
    print()


def _print_mr_info(context: LifecycleContext) -> None:
    print(_c("  Merge Request Details", "bold"))
    print(f"     Project  : {_c(context.project_name, 'cyan')}")
    print(f"     MR       : !{context.mr_iid} — {_c(context.mr_title, 'white')}")
    print(f"     Author   : @{context.author_username}")
    print(f"     Branch   : {context.source_branch} → {_c(context.target_branch, 'green')}")
    print(f"     Commit   : {_c((context.merge_commit_sha or 'HEAD')[:12] + '...', 'dim')}")
    print()
    print(_c("  Starting pipeline…", "dim"))
    print()


def _print_stage_header(num: int, label: str, agent_name: str) -> None:
    print(
        _c(f"  [{num}/5]", "dim") +
        _c(f" {label}", "bold") +
        _c(f" via {agent_name}", "dim")
    )


def _print_stage_result(status: str, summary: str, duration: float) -> None:
    status_lower = status.lower()
    if status_lower == "passed":
        colour = "green"
    elif status_lower == "warning":
        colour = "yellow"
    elif status_lower in ("blocked", "failed"):
        colour = "red"
    else:
        colour = "dim"

    print(
        f"       {_c(status.upper(), colour, 'bold')}  "
        f"{_c(f'({duration:.2f}s)', 'dim')}"
    )
    summary_short = summary[:110] + "…" if len(summary) > 110 else summary
    print(f"          {_c(summary_short, 'dim')}")
    print()


def _print_final_result(pipeline_status: str, recommendation: str) -> None:
    print(_c("─" * 68, "dim"))
    print()
    if pipeline_status == "PASSED":
        print(_c("  PIPELINE PASSED", "green", "bold"))
    elif pipeline_status == "BLOCKED":
        print(_c("  PIPELINE BLOCKED", "red", "bold"))
    else:
        print(_c("  PIPELINE PARTIAL", "yellow", "bold"))
    print()
    words = recommendation.split()
    line, wrapped = "", []
    for word in words:
        if len(line) + len(word) + 1 > 64:
            wrapped.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        wrapped.append(line)
    for l in wrapped:
        print(f"  {_c(l, 'white')}")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# Stage display mapping
# ──────────────────────────────────────────────────────────────────────────────

STAGE_DISPLAY = {
    "Secure":  (1, "Secure",  "SecurityAgent"),
    "Verify":  (2, "Verify",  "VerificationAgent"),
    "Release": (3, "Release", "ReleaseAgent"),
    "Monitor": (4, "Monitor", "MonitoringAgent"),
    "Govern":  (5, "Govern",  "GovernanceAgent"),
}


# ──────────────────────────────────────────────────────────────────────────────
# Demo-aware orchestrator (adds live console output)
# ──────────────────────────────────────────────────────────────────────────────

class DemoOrchestrator(LifecycleOrchestrator):
    """Wraps LifecycleOrchestrator to add live console progress output."""

    async def execute_lifecycle(self, context: LifecycleContext) -> LifecycleContext:
        from datetime import datetime, timezone
        from src.core.state import AgentResult, AgentStatus, PipelineStatus

        _print_mr_info(context)

        for stage_cfg in self.stages:
            agent = stage_cfg.agent
            stage_name = agent.stage.value
            num, label, _ = STAGE_DISPLAY.get(stage_name, (0, stage_name, agent.name))

            _print_stage_header(num, label, agent.name)

            if context.aborted and not stage_cfg.always_run:
                print(
                    f"       {_c('SKIPPED', 'dim', 'bold')}  "
                    f"{_c('(pipeline blocked)', 'dim')}"
                )
                print()
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

            result = await agent.run(context)
            context.add_result(result)
            _print_stage_result(result.status.value, result.summary, result.execution_time_seconds)

            if stage_cfg.is_gate and result.is_blocking:
                print(_c("  Gate triggered — halting downstream stages.", "red"))
                print()

        context.completed_at = datetime.now(timezone.utc)
        context.pipeline_status = self._determine_pipeline_status(context)
        context.final_report = self._generate_final_report(context)
        context.final_recommendation = self._generate_recommendation(context)

        return context


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

async def run_demo(blocked: bool = False) -> None:
    scenario = "BLOCKED — Critical CVE detected" if blocked else "PASSED — Clean merge"
    context = DEMO_CONTEXT_BLOCKED if blocked else DEMO_CONTEXT_PASSED

    _print_header(scenario)

    orchestrator = DemoOrchestrator()
    final = await orchestrator.execute_lifecycle(context)

    _print_final_result(
        final.pipeline_status.value if final.pipeline_status else "UNKNOWN",
        final.final_recommendation or "",
    )

    # Always save the final Markdown report for easy review
    report_path = "report.md"
    if final.final_report:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(final.final_report)
        print(_c(f"  Full Markdown report saved → {_c(report_path, 'cyan', 'bold')}", "dim"))
        print()

    print(
        _c("  Run complete.  Pipeline duration: ", "dim") +
        _c(f"{final.total_duration_seconds}s", "cyan")
    )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lifecycle Guardian — Interactive Demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python demo.py                   Run the PASSED scenario (clean merge)
  python demo.py --blocked         Run the BLOCKED scenario (critical CVE found)
        """,
    )
    parser.add_argument(
        "--blocked",
        action="store_true",
        help="Simulate a blocked pipeline with a critical CVE",
    )
    args = parser.parse_args()
    asyncio.run(run_demo(blocked=args.blocked))


if __name__ == "__main__":
    main()
