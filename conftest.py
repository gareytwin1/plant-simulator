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



