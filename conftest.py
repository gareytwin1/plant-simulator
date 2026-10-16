import os

# The live app opens its score store at import (T20-16). A test run must never
# write to the developer's file; tests/conftest.py gives each test its own.
os.environ["PLANT_SCORE_DB"] = ":memory:"


def pytest_report_teststatus(report, config):
    if report.when != "call":
        return None

    if report.passed:
        return "passed", "✓", "PASSED"

    if report.failed:
        return "failed", "✗", "FAILED"

    if report.skipped:
        return "skipped", "○", "SKIPPED"

    return None
