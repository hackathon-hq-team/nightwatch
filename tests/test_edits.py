"""Patch edits → exact unified diff, and the pipeline using them."""
from __future__ import annotations

import subprocess

import pytest
from test_pipeline import INVESTIGATION, REPORT, make

from nightwatch.edits import Edit, EditError, PatchReply, edits_to_diff, to_proposal
from nightwatch.schemas import IncidentState

PLUGIN = (
    '"""Select a compatible plugin release.\r\n\r\n"""\r\n'
    "from __future__ import annotations\r\n\r\n"
    "from packaging.version import Version, parse\r\n\r\n\r\n"
    "def _parse_release(label: str) -> Version:\r\n"
    "    # Parse one registry label before applying release-selection policy.\r\n"
    "    return parse(label)\r\n"
)


def apply_with_git(tmp_path, name, before, diff):
    """The diff must apply with plain `git apply` — no recount, no fuzz."""
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(before.replace("\r\n", "\n").encode())
    (tmp_path / "fix.patch").write_text(diff, encoding="utf-8", newline="\n")
    res = subprocess.run(["git", "apply", "--check", "-v", "fix.patch"], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr
    subprocess.run(["git", "apply", "fix.patch"], cwd=tmp_path, check=True)
    return path.read_text()


def test_live_failure_case_now_applies(tmp_path):
    """24 Oct live run: Super and Ultra wrote the right fix, but their diffs had an invented blank line."""
    edits = [
        Edit(file="app/plugin_binding.py", old="from packaging.version import Version, parse",
             new="import re\n\nfrom packaging.version import Version, parse"),
        Edit(file="app/plugin_binding.py",
             old="    # Parse one registry label before applying release-selection policy.\n    return parse(label)",
             new="    # Parse one registry label before applying release-selection policy.\n"
                 "    label = re.sub(r\"-(stable|final)$\", \"\", label)\n    return parse(label)"),
    ]
    diff = edits_to_diff(edits, lambda p: PLUGIN if p == "app/plugin_binding.py" else None)
    after = apply_with_git(tmp_path, "app/plugin_binding.py", PLUGIN, diff)
    assert "import re\n\nfrom packaging" in after and 're.sub(r"-(stable|final)$"' in after


def test_trailing_whitespace_and_indent_lines_tolerated():
    src = "def f():\n    x = 1   \n    return x\n"
    diff = edits_to_diff([Edit(file="a.py", old="    x = 1\n    return x\n", new="    x = 2\n    return x\n")],
                         lambda p: src)
    assert "-    x = 1   \n+    x = 2\n" in diff


def test_ambiguous_old_is_rejected_with_a_reason():
    src = "a = 1\nb = 2\na = 1\n"
    with pytest.raises(EditError, match="matches 2 places"):
        edits_to_diff([Edit(file="m.py", old="a = 1", new="a = 3")], lambda p: src)


def test_old_not_in_file_is_rejected_with_a_reason():
    with pytest.raises(EditError, match="not found in m.py"):
        edits_to_diff([Edit(file="m.py", old="\nfrom x import y", new="")], lambda p: '"""doc"""\nfrom x import z\n')


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.py", ""])
def test_unsafe_paths_rejected(path):
    with pytest.raises(EditError, match="invalid path"):
        edits_to_diff([Edit(file=path, old="a", new="b")], lambda p: "a\n")


def test_missing_file_and_new_file():
    with pytest.raises(EditError, match="does not exist"):
        edits_to_diff([Edit(file="nope.py", old="a", new="b")], lambda p: None)
    diff = edits_to_diff([Edit(file="app/new.py", old="", new="X = 1\n")], lambda p: None)
    assert diff.startswith("--- /dev/null\n+++ b/app/new.py\n")


def test_no_newline_at_end_of_file(tmp_path):
    src = "a = 1\nb = 2"
    diff = edits_to_diff([Edit(file="m.py", old="b = 2", new="b = 3")], lambda p: src)
    assert "\\ No newline at end of file" in diff
    (tmp_path / "m.py").write_text(src)
    (tmp_path / "p.patch").write_text(diff)
    subprocess.run(["git", "apply", "p.patch"], cwd=tmp_path, check=True)
    assert (tmp_path / "m.py").read_text() == "a = 1\nb = 3"


def test_noop_edits_rejected():
    with pytest.raises(EditError, match="do not change"):
        edits_to_diff([Edit(file="m.py", old="a = 1", new="a = 1")], lambda p: "a = 1\n")


def test_raw_diff_still_accepted():
    reply = PatchReply(diff="--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n", explanation="e")
    assert to_proposal(reply, lambda p: None).diff == reply.diff


EDIT_FIX = {"edits": [{"file": "app/calc.py", "old": '    """Mean value; 0 for an empty list."""\n',
                       "new": '    """Mean value; 0 for an empty list."""\n    if not items:\n        return 0\n'}],
            "explanation": "restore empty-list guard", "evidence_ids": ["E4"]}


def test_pipeline_verifies_patch_built_from_edits(demo_repo, alert, tmp_path):
    pipe, _ = make(demo_repo, tmp_path, {"investigate": [INVESTIGATION], "patch": [EDIT_FIX]})
    inc = pipe.run(alert)
    assert inc.state == IncidentState.verified, inc.events
    assert "+    if not items:" in inc.final_patch.diff


def test_mismatched_edit_is_a_rejected_attempt_with_feedback(demo_repo, alert, tmp_path):
    wrong = {**EDIT_FIX, "edits": [{**EDIT_FIX["edits"][0], "old": "def average(items, strict):\n"}]}
    pipe, llm = make(demo_repo, tmp_path, {"investigate": [INVESTIGATION], "patch": [wrong, EDIT_FIX]})
    inc = pipe.run(alert)
    assert inc.state == IncidentState.verified
    assert inc.attempts[0].verdict.checks == {"edits_apply": False} and inc.attempts[0].run is None
    assert "not found in app/calc.py" in llm.calls[-1][1]  # the reason goes back to the model


def test_edit_touching_tests_is_rejected(demo_repo, alert, tmp_path):
    bad = {"edits": [{"file": "tests/test_calc.py", "old": "def test", "new": "def _skip"}], "explanation": "x"}
    pipe, _ = make(demo_repo, tmp_path, {"investigate": [INVESTIGATION], "patch": [bad, bad, bad], "report": [REPORT]})
    inc = pipe.run(alert)
    assert inc.state == IncidentState.reported
    assert all(a.run is None and not a.verdict.ok for a in inc.attempts)
