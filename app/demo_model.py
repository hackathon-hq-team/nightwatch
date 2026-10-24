"""Explicit scripted demo responses. Never used by the live model client.

This demonstrates orchestration and real Docker verification, not model quality.
"""
from nightwatch.schemas import ModelCall


class DemoModel:
    def __init__(self, case=None, source=None):
        # Only the explicit scripted mode receives prepared evaluation data.
        self.case = case
        self.source = source

    def complete_json(self, step, system, user, schema, usage, max_tokens=4096, model=None):
        if self.case and step in ("investigate", "patch"):
            if step == "investigate":
                reply = {"hypotheses": [{"summary": "Prepared fixture diagnosis; not a live model result.",
                                         "confidence": 0.9, "evidence_ids": ["E1"]}],
                         "repro_test": {"path": "tests/nightwatch/test_scenario.py", "code": self.case["reproducer"],
                                        "expected": {"exception_type": self.case["exception_type"]}}}
            else:
                import difflib
                from evaluation.catalog import replace_once
                fixed = replace_once(self.source, self.case["good_replacements"])
                path = self.case["app_file"]
                reply = {"diff": "".join(difflib.unified_diff(self.source.splitlines(True), fixed.splitlines(True),
                                                             fromfile="a/" + path, tofile="b/" + path)),
                         "explanation": "Prepared fixture repair; not a live model result.", "evidence_ids": ["E1"]}
        elif step == "investigate" and "MemoryError" in user:
            reply = {
                "hypotheses": [{"summary": "Memory pressure may depend on production traffic or input size.",
                                "confidence": 0.2, "evidence_ids": ["E1"],
                                "would_refute": "Memory stays flat while the error repeats."}],
                "repro_test": None,
                "not_reproducible_reason": "No stack trace, workload or failing input was provided.",
            }
        elif step == "investigate":
            reply = {
                "hypotheses": [{"summary": "average() divides by the length of an empty sample.",
                                "confidence": 0.95, "evidence_ids": ["E1"],
                                "would_refute": "The empty sample passes on the broken commit."}],
                "repro_test": {
                    "path": "tests/nightwatch/test_empty_sample.py",
                    "code": "from app.calc import average\n\ndef test_empty_sample():\n    assert average([]) == 0.0\n",
                    "expected": {"exception_type": "ZeroDivisionError", "message_contains": "division by zero",
                                 "file": "app/calc.py", "function": "average"},
                },
            }
        elif step == "patch":
            reply = {
                "diff": '--- a/app/calc.py\n+++ b/app/calc.py\n@@ -8,1 +8,3 @@\n'
                        '+    if not items:\n+        return 0.0\n     return sum(items) / len(items)\n',
                "explanation": "Return the documented empty-sample value before dividing by the sample count.",
                "evidence_ids": ["E1"],
            }
        elif step == "report":
            reply = {
                "facts": ["The alert reports MemoryError without a stack trace or failing input [E1]."],
                "hypotheses": [{"summary": "Production load may exceed available memory.", "confidence": 0.2,
                                "evidence_ids": ["E1"],
                                "would_refute": "Memory remains below its limit while errors occur."}],
                "next_steps": ["Capture the failing request and a full stack trace.",
                               "Record memory usage, container limits and traffic around the incident.",
                               "Build a deterministic reproducer from the captured input."],
            }
        else:
            raise ValueError(f"Unsupported scripted demo step: {step}")
        usage.calls.append(ModelCall(step=step, model="scripted-demo", prompt_tokens=0,
                                     completion_tokens=0, cost_usd=0, duration_s=0))
        return schema.model_validate(reply)
