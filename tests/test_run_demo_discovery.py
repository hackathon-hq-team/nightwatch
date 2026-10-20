"""Scenario discovery and expected outcomes in scripts/run_demo.py."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from nightwatch.schemas import IncidentState

_spec = importlib.util.spec_from_file_location("run_demo_disc", Path(__file__).parents[1] / "scripts" / "run_demo.py")
run_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_demo)


def test_discover_puts_known_scenarios_first(tmp_path):
    for name in ["zeta", "insufficient-evidence", "alpha", "empty-sample"]:
        (tmp_path / f"{name}.json").write_text("{}", encoding="utf-8")
    assert run_demo.discover(tmp_path) == ["empty-sample", "insufficient-evidence", "alpha", "zeta"]


def test_expected_from_file_overrides_table():
    assert run_demo.expected_outcome("empty-sample", {"expected": "reported"}) == IncidentState.reported
    assert run_demo.expected_outcome("empty-sample", {}) == IncidentState.verified
    assert run_demo.expected_outcome("new-bug", {"expected": "verified"}) == IncidentState.verified
    assert run_demo.expected_outcome("new-bug", json.loads("{}")) is None


def test_verdict_labels():
    assert [run_demo._verdict(x) for x in (True, False, None)] == ["PASS", "FAIL", "N/A"]
