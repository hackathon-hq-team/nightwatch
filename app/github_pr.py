"""Prepare and publish a reverified patch using the operator's GitHub CLI login.

No model code runs on the host. Publishing never merges or updates the base branch.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote, urlencode

from nightwatch.docker_runner import Runner, safe_path
from nightwatch.pipeline import check_diff, diff_files
from nightwatch.schemas import Incident
from nightwatch.web_search import references_markdown
from verifier import Verifier


class GitHubAPI:
    def request(self, endpoint, payload=None):
        command = ["gh", "api", "--hostname", "github.com", endpoint,
                   "--method", "POST" if payload is not None else "GET"]
        if payload is not None:
            command += ["--input", "-"]
        result = subprocess.run(command, input=json.dumps(payload).encode() if payload is not None else None,
                                capture_output=True, timeout=45)
        if result.returncode:
            # gh's stderr can contain private response details; don't display it.
            raise RuntimeError("GitHub request failed. Check gh auth status and repository permissions.")
        return json.loads(result.stdout)


def validate_incident(incident, metadata):
    if metadata.get("kind") != "repository":
        raise ValueError("Simulated alerts use private fixture commits and cannot be published to GitHub.")
    if (incident.state.value != "verified" or incident.error or not incident.final_patch
            or not incident.investigation or not incident.investigation.repro_test
            or not incident.repro_verdict or not incident.repro_verdict.ok):
        raise ValueError("Only completed, reproduced and verified incidents can create a Draft PR.")
    if metadata.get("acceptance") and not metadata["acceptance"].get("ok"):
        raise ValueError("Independent acceptance has not passed.")
    if not re.fullmatch(r"inc-[0-9a-f]{8}", incident.id):
        raise ValueError("Unsupported incident identifier")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", metadata.get("github_repo", "")):
        raise ValueError("Configure NIGHTWATCH_GITHUB_REPO as owner/repository.")
    if check_diff(incident.final_patch.diff, 200):
        raise ValueError("Patch failed the publication scope guard.")
    files = diff_files(incident.final_patch.diff)
    for name in files:
        safe_path(name)
        if (not name.endswith(".py") or name.startswith(".") or name.endswith("conftest.py")
                or ("/" in name and not name.startswith(("app/", "src/")))):
            raise ValueError("Automatic publication only supports application Python files.")
    return files


def reverify(incident: Incident, metadata, runner=None):
    changed_paths = validate_incident(incident, metadata)
    runner = runner or Runner(Path(metadata["repository"]))
    test = incident.investigation.repro_test
    tests = {test.path: test.code}
    baseline = runner._snapshot(incident.alert.commit, {})
    original_hash = runner.tests_hash(incident.alert.commit, {})
    original = runner.run(incident.alert.commit, {})
    if (original.status != "passed" or original.exit_code != 0 or original.failures or not original.passed
            or original.tests_hash != original_hash or len(original.passed) != len(set(original.passed))):
        raise ValueError("Original test suite is not green and intact; no GitHub write was made.")
    frozen = runner.tests_hash(incident.alert.commit, tests)
    broken = runner.run(incident.alert.commit, tests, select=[test.path])
    good = (runner.run(incident.alert.previous_good_commit, tests, select=[test.path])
            if incident.alert.previous_good_commit else None)
    verifier = Verifier()
    if not verifier.check_reproduction(incident.alert, test, broken, good).ok:
        raise ValueError("Reproduction recheck failed; no GitHub write was made.")
    after = runner.run(incident.alert.commit, tests, patch=incident.final_patch.diff)
    if not verifier.check_patch(incident.alert, test, after, frozen).ok or runner.last_files is None:
        raise ValueError("Patch recheck failed; no GitHub write was made.")
    if not set(original.passed).issubset(after.passed) or len(after.passed) != len(set(after.passed)):
        raise ValueError("Original passing tests disappeared or were skipped after the patch.")
    candidates = runner.last_files
    changed = {path: candidates.get(path) for path in baseline.keys() | candidates.keys()
               if baseline.get(path) != candidates.get(path)}
    allowed = changed_paths | set(tests) | {"tests/nightwatch/__init__.py"}
    if not changed or not set(changed).issubset(allowed):
        raise ValueError("Executed snapshot contains unexpected file changes.")
    for path in tests:
        if candidates.get(path) != tests[path].encode():
            raise ValueError("Reproduction test changed during verification.")
    return changed, after


def body_for(incident, run):
    references = references_markdown(incident)
    references = references + "\n\n" if references else ""
    return (f"Nightwatch reproduced `{incident.alert.exception_type}` at commit `{incident.alert.commit}` "
            "and verified this patch in an isolated Docker container.\n\n"
            f"Incident: `{incident.id}`\n\n"
            f"{incident.final_patch.explanation}\n\n"
            f"{references}"
            f"Validation: reproduction matched the alert; {len(run.passed)} tests passed; "
            "the frozen test hash is unchanged. The reproduction test is included in this PR.\n\n"
            f"Model usage: {incident.usage.tokens} tokens, estimated ${incident.usage.cost_usd:.4f}.\n\n"
            "This patch has not been deployed. A human must review, merge and deploy it.")


def publish(incident, metadata, api=None, runner=None):
    validate_incident(incident, metadata)
    api = api or GitHubAPI()
    repository = metadata["github_repo"]
    base = metadata.get("base_branch", "main")
    if not base or base.startswith("-") or ".." in base:
        raise ValueError("Invalid GitHub base branch")
    branch = "codex/nightwatch-" + incident.id
    prefix = f"repos/{repository}"
    query = urlencode({"state": "all", "head": repository.split("/")[0] + ":" + branch})
    existing = api.request(prefix + "/pulls?" + query)
    if existing:
        return {"url": existing[0]["html_url"], "number": existing[0]["number"], "existing": True,
                "branch": branch}
    base_commit = api.request(prefix + "/commits/" + quote(base, safe=""))
    if base_commit["sha"] != incident.alert.commit:
        raise ValueError("The target branch moved or differs from the tested release. Re-investigate its current commit.")
    # Repeat reproduction and the full suite immediately before any GitHub writes.
    changed, result = reverify(incident, metadata, runner)
    if api.request(prefix + "/commits/" + quote(base, safe=""))["sha"] != incident.alert.commit:
        raise ValueError("The target branch moved during verification; re-investigate its current commit.")
    entries = []
    local_repo = Path(metadata["repository"])
    modes = subprocess.run(["git", "-C", str(local_repo), "-c", "core.quotepath=false", "ls-tree", "-r", incident.alert.commit],
                           capture_output=True, text=True, check=True, timeout=10)
    mode_map = {line.split("\t", 1)[1]: line.split(" ", 1)[0] for line in modes.stdout.splitlines()}
    for path, data in sorted(changed.items()):
        entry = {"path": path, "mode": mode_map.get(path, "100644"), "type": "blob"}
        if entry["mode"] not in ("100644", "100755"):
            raise ValueError("Links and special files cannot be published automatically.")
        if data is None:
            entry["sha"] = None
        else:
            entry["content"] = data.decode("utf-8")
        entries.append(entry)
    tree = api.request(prefix + "/git/trees", {"base_tree": base_commit["commit"]["tree"]["sha"], "tree": entries})
    commit = api.request(prefix + "/git/commits", {
        "message": f"Fix {incident.alert.exception_type} ({incident.id})", "tree": tree["sha"],
        "parents": [incident.alert.commit],
    })
    # A deterministic branch makes retry after a network interruption idempotent.
    try:
        ref = api.request(prefix + "/git/ref/heads/" + quote(branch, safe="/"))
    except RuntimeError:
        ref = None
    if ref:
        if ref["object"]["sha"] != commit["sha"]:
            # Commit timestamps make SHA identity unsuitable on retry; verify the
            # existing branch has exactly the same tree and original parent.
            prior = api.request(prefix + "/git/commits/" + ref["object"]["sha"])
            if prior["tree"]["sha"] != tree["sha"] or [p["sha"] for p in prior["parents"]] != [incident.alert.commit]:
                raise ValueError("The incident branch already exists with different content; refusing to overwrite it.")
    else:
        api.request(prefix + "/git/refs", {"ref": "refs/heads/" + branch, "sha": commit["sha"]})
    created = api.request(prefix + "/pulls", {
        "title": f"Fix {incident.alert.exception_type}: {incident.alert.title[:120]}",
        "head": branch, "base": base, "body": body_for(incident, result), "draft": True,
    })
    return {"url": created["html_url"], "number": created["number"], "existing": False, "branch": branch}
