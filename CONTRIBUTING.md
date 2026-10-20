# Contributing to Lifecycle Guardian

Thank you for your interest in contributing! This document keeps things simple.

## Getting Started

```bash
git clone <repo-url> lifecycle-guardian && cd lifecycle-guardian
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
python tests/run_tests.py   # confirm 15 tests pass before making changes
```

## Project Layout

| Path | Purpose |
|:-----|:--------|
| `demo.py` | Standalone demo — good place to test changes end-to-end |
| `src/agents/` | One file per DevSecOps stage — most feature work happens here |
| `src/core/orchestrator.py` | Pipeline runner and Markdown report generator |
| `src/core/state.py` | Shared data models (`LifecycleContext`, `AgentResult`) |
| `tests/test_agents.py` | All unit + integration tests |

## Adding or Modifying an Agent

1. Create or edit the agent file in `src/agents/`.
2. Subclass `BaseAgent`, implement `is_enabled()` and `execute()`.
3. Write findings back to the appropriate `context.*_report` field so downstream agents can read them.
4. Add or update tests in `tests/test_agents.py`.
5. Run `python tests/run_tests.py` — all tests must pass.

## Code Style

- Python 3.10+, type hints throughout.
- Keep agent `execute()` methods focused — one responsibility per agent.
- Preserve all existing docstrings and comments unless directly relevant to your change.
- No external test framework required; the standalone `tests/run_tests.py` runner is sufficient.

## Submitting Changes

1. Fork the repository and create a feature branch.
2. Make your changes with clear, descriptive commits.
3. Ensure `python tests/run_tests.py` reports **0 failures**.
4. Open a Merge Request with a description of what changed and why.

## Questions

Open an issue or start a discussion in the repository.
