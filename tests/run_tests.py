"""Standalone test runner for Lifecycle Guardian — no pytest required.

Run with:
    python tests/run_tests.py

All 15 tests should pass. Uses plain factory functions (make_clean_context /
make_vulnerable_context) so it is not affected by pytest fixture restrictions.
"""

import asyncio
import inspect
import os
import sys

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import tests.test_agents as t


async def main() -> None:
    print("=" * 50)
    print("Lifecycle Guardian — Test Suite")
    print("=" * 50)

    # Collect test functions (exclude helpers / fixtures / factory functions)
    test_funcs = sorted(
        [
            getattr(t, name)
            for name in dir(t)
            if name.startswith("test_") and callable(getattr(t, name))
        ],
        key=lambda f: f.__name__,
    )

    passed = 0
    failed = 0

    for func in test_funcs:
        name = func.__name__
        sig = inspect.signature(func)
        kwargs = {}

        # Resolve fixtures via plain factory functions — avoids pytest internals
        if "clean_context" in sig.parameters:
            kwargs["clean_context"] = t.make_clean_context()
        if "vulnerable_context" in sig.parameters:
            kwargs["vulnerable_context"] = t.make_vulnerable_context()

        try:
            if inspect.iscoroutinefunction(func):
                await func(**kwargs)
            else:
                func(**kwargs)
            print(f"  [PASS] {name}")
            passed += 1
        except Exception as exc:
            print(f"  [FAIL] {name}: {type(exc).__name__} — {exc}")
            failed += 1

    print("=" * 50)
    print(f"Results: {passed} passed, {failed} failed.")
    print("=" * 50)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
