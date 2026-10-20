"""pytest plugin: writes test results to JSON, including the exception signature.

Usage: pytest -p nightwatch.pytest_plugin, report path in NIGHTWATCH_REPORT.
Timur can reuse it inside the container (depends on pytest only).
"""
import json
import os

import pytest

_results = {"passed": [], "failures": []}


def _is_test_or_lib(path: str) -> bool:
    p = path.replace("\\", "/")
    name = p.split("/")[-1]
    return (p.startswith("tests/") or "/tests/" in p or name.startswith("test_") or name == "conftest.py"
            or "site-packages" in p or "/_pytest/" in p or "/pluggy/" in p or not p.endswith(".py")
            or os.path.isabs(p))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if call.excinfo is not None:
        rep._nw_excinfo = call.excinfo


def pytest_runtest_logreport(report):
    if report.when == "call" and report.passed:
        _results["passed"].append(report.nodeid)
        return
    if not report.failed:
        return
    entry = {"nodeid": report.nodeid, "exception_type": "Error", "message": "", "file": None, "function": None}
    excinfo = getattr(report, "_nw_excinfo", None)
    if excinfo is not None:
        entry["exception_type"] = excinfo.type.__name__
        entry["message"] = str(excinfo.value)[:500]
        root = os.getcwd()
        for tb in reversed(excinfo.traceback):
            path = str(tb.path)
            rel = os.path.relpath(path, root) if path.startswith(root) else path
            rel = rel.replace("\\", "/")
            if not _is_test_or_lib(rel):
                entry["file"] = rel
                entry["function"] = tb.name
                break
    else:
        entry["message"] = str(report.longrepr)[-500:]
    if report.when != "call":
        entry["nodeid"] += f" [{report.when}]"
    _results["failures"].append(entry)


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get("NIGHTWATCH_REPORT")
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_results, f)
