"""Windows uses a legacy code page by default. Everything Nightwatch writes must be UTF-8 regardless,
otherwise non-ASCII model output (→, é, emoji) crashes the run or corrupts the history files."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = textwrap.dedent('''
    import json, sys
    from pathlib import Path
    from conftest import ScriptedLLM  # noqa: F401  (tests dir on sys.path)
    from nightwatch.history import HistoryStore
    from nightwatch.model_client import DailyBudget
    from nightwatch.schemas import Alert, Incident
    out = Path(sys.argv[1])
    store = HistoryStore(out / "history")
    inc = Incident(alert=Alert(title="naïve → crash ✓", exception_type="ValueError", message="é", commit="abc"))
    store.save(inc)
    assert store.load(inc.id).alert.title == "naïve → crash ✓"
    DailyBudget(out / "history", 1.0).add(0.01)
    print("ok")
''')


def test_history_is_utf8_under_a_non_utf8_locale(tmp_path):
    # Simulate a non-UTF-8 default encoding (like cp1252 on Windows) on any OS.
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
           "PYTHONIOENCODING": "utf-8", "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "tests")])}
    script = tmp_path / "check.py"
    script.write_text(SCRIPT, encoding="utf-8")  # Python source is always read as UTF-8
    res = subprocess.run([sys.executable, "-X", "utf8=0", str(script), str(tmp_path)], env=env,
                         capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    files = list((tmp_path / "history").glob("inc-*.json"))
    assert json.loads(files[0].read_bytes().decode("utf-8"))["alert"]["title"] == "naïve → crash ✓"


def test_history_save_retries_transient_windows_lock(tmp_path, monkeypatch):
    """WinError 5/32 while replacing the file (antivirus, a UI reader) must not fail the incident."""
    from pathlib import Path

    from nightwatch.history import HistoryStore
    from nightwatch.schemas import Alert, Incident

    store = HistoryStore(tmp_path)
    inc = Incident(alert=Alert(title="t", exception_type="E", commit="c"))
    real, calls = Path.replace, []

    def flaky(self, target):
        calls.append(1)
        if len(calls) < 3:
            err = PermissionError(13, "Access is denied")
            err.winerror = 5
            raise err
        return real(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    monkeypatch.setattr("nightwatch.history.time.sleep", lambda s: None)
    store.save(inc)
    assert len(calls) == 3 and store.load(inc.id).id == inc.id
