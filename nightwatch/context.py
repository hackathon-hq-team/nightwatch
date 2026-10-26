"""Evidence collection for the model.

Every fragment gets an id (E1, E2, …) so hypotheses and patches can cite a specific
log or diff line. Order is by importance; whatever does not fit the token budget
is truncated or dropped.

Sources: stack trace and request data from the alert, logs, the diff between the last
good and the broken version, files from the stack trace (a window around the line),
similar past incidents from history, and (optionally) known library issues from the web.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .history import HistoryStore, summarize_for_memory
from .schemas import Alert, Evidence
from .web_search import DEPENDENCY_FILES, WebSearch, build_query, detect_library, format_results, parse_pins

SECRET_HINTS = (".env", "secret", "credential", "id_rsa", ".pem", ".key")


class RepoView:
    """Read the demo project's repository through git, without a checkout."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _git(self, *args: str) -> str:
        res = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
        if res.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {res.stderr.strip()}")
        return res.stdout

    def show(self, commit: str, file: str) -> str | None:
        try:
            return self._git("show", f"{commit}:{file}")
        except RuntimeError:
            return None

    def diff(self, base: str, head: str) -> str:
        return self._git("diff", "--unified=5", base, head, "--", ".", ":(exclude)tests/nightwatch")

    def parent(self, commit: str) -> str | None:
        try:
            return self._git("rev-parse", f"{commit}~1").strip()
        except RuntimeError:
            return None

    def log(self, base: str | None, head: str, n: int = 10) -> str:
        rng = f"{base}..{head}" if base else head
        return self._git("log", "--format=%h %an %ad %s", "--date=short", f"-n{n}", rng)

    def ls(self, commit: str) -> list[str]:
        return self._git("ls-tree", "-r", "--name-only", commit).splitlines()


def _window(text: str, line: int | None, radius: int = 40) -> tuple[str, bool]:
    lines = text.splitlines()
    if line is None or len(lines) <= radius * 3:
        return "\n".join(f"{i + 1:4d}  {ln}" for i, ln in enumerate(lines)), False
    lo, hi = max(0, line - 1 - radius), min(len(lines), line + radius)
    return "\n".join(f"{i + 1:4d}  {lines[i]}" for i in range(lo, hi)), True


def _is_secret(path: str) -> bool:
    low = path.lower()
    return any(h in low for h in SECRET_HINTS)


class ContextBuilder:
    def __init__(self, repo: RepoView, history: HistoryStore | None = None, token_budget: int = 150_000,
                 web: WebSearch | None = None):
        self.repo = repo
        self.history = history
        self.budget = token_budget
        self.web = web
        self.notes: list[str] = []  # what happened during the last build(), for incident events

    def build(self, alert: Alert, exclude_incident: str | None = None) -> tuple[list[Evidence], list[str]]:
        """Return (evidence, ids of similar incidents)."""
        items: list[Evidence] = []
        self.notes = []

        def add(kind, source, content, truncated=False, url=""):
            if content and content.strip():
                items.append(Evidence(id=f"E{len(items) + 1}", kind=kind, source=source,
                                      content=content.strip(), truncated=truncated, url=url))

        # 1. Stack trace
        if alert.stacktrace:
            st = "\n".join(f'  File "{f.file}", line {f.line}, in {f.function}' + ("" if f.in_app else "  [library]")
                           for f in alert.stacktrace)
            add("stacktrace", "alert", f"{alert.exception_type}: {alert.message}\n{st}")
        else:
            add("stacktrace", "alert", f"{alert.exception_type}: {alert.message}")

        # 2. Request data
        if alert.request:
            add("request", "sentry", json.dumps(alert.request, ensure_ascii=False, indent=2)[:4000])

        # 3. Logs
        if alert.logs:
            logs = alert.logs[-12000:]
            add("log", "alert", logs, truncated=len(alert.logs) > len(logs))

        # 4. Changes between the good and the broken version
        base = alert.previous_good_commit or self.repo.parent(alert.commit)
        full_diff = ""
        if base:
            try:
                full_diff = self.repo.diff(base, alert.commit)
                add("diff", f"recent changes {base[:8]}..{alert.commit[:8]} (may be unrelated to the alert)",
                    self.repo.log(base, alert.commit) + "\n" + full_diff[:40000])
            except RuntimeError:
                pass

        # 5. Files from the stack trace (error site first), no duplicates
        seen = set()
        for f in reversed([f for f in alert.stacktrace if f.in_app]):
            if f.file in seen or _is_secret(f.file):
                continue
            seen.add(f.file)
            text = self.repo.show(alert.commit, f.file)
            if text is None:
                continue
            body, cut = _window(text, f.line)
            add("file", f"{f.file}@{alert.commit[:8]} (around line {f.line}, function {f.function})", body, cut)

        # 6. Known library issues from the web (only when the error points at a library)
        if self.web:
            self._web_evidence(alert, full_diff, add)

        # 7. Memory: similar past incidents
        similar_ids: list[str] = []
        if self.history:
            for score, inc in self.history.find_similar(alert, exclude=exclude_incident):
                similar_ids.append(inc.id)
                add("memory", f"{inc.id} (similarity {score})", summarize_for_memory(inc))

        return self._fit(items), similar_ids

    def _web_evidence(self, alert: Alert, full_diff: str, add) -> None:
        pins: dict[str, str] = {}
        for name in DEPENDENCY_FILES:
            text = self.repo.show(alert.commit, name)
            if text:
                pins.update(parse_pins(text))
        hint = detect_library(alert, pins, full_diff)
        if hint is None:
            return  # an error in our own code: searching the web would only add noise
        query = build_query(hint, alert)
        try:
            results = self.web.search(query)
        except Exception as e:  # noqa: BLE001 — the web is optional, the investigation goes on
            self.notes.append(f"Web search failed ({type(e).__name__}); continuing without it")
            return
        self.notes.append(f"Web search ({hint.reason}): \"{query}\" — {len(results)} results")
        # One fragment per page, so the model can cite the exact source it relied on.
        for r in results:
            add("web", f"web, EXTERNAL AND UNVERIFIED, may not match this version: {r.title.strip()[:150]}",
                format_results([r]), url=r.url)

    def _fit(self, items: list[Evidence]) -> list[Evidence]:
        """Fit the budget: earlier items are kept whole, the one that overflows is truncated, the rest dropped."""
        total = sum(e.approx_tokens for e in items)
        if total <= self.budget:
            return items
        out, used = [], 0
        for e in items:
            left = self.budget - used
            if e.approx_tokens <= left:
                out.append(e)
                used += e.approx_tokens
            elif left > 500:
                chars = (left - 16) * 4
                out.append(e.model_copy(update={"content": e.content[:chars], "truncated": True}))
                used = self.budget
            # anything else does not fit
        return out


def render(evidence: list[Evidence]) -> str:
    parts = []
    for e in evidence:
        note = " (truncated)" if e.truncated else ""
        parts.append(f"### [{e.id}] {e.kind}: {e.source}{note}\n{e.content}")
    return "\n\n".join(parts)
