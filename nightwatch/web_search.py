"""Optional web search for known library issues (Tavily).

When it runs: only if the error is raised inside a third-party library, or the deploy changed
dependency versions. Errors in our own code are not searched — that would only add noise.

Privacy invariant: the query is built by code from the package name, its version, the exception
type and a scrubbed piece of the message. Logs, request data, source code, ids and numbers never
leave the service.

Results are evidence of kind "web": external and unverified. They can suggest a hypothesis, but a
patch is still accepted only after reproduction and verification.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from .schemas import Alert

TAVILY_URL = "https://api.tavily.com/search"
TRUSTED_DOMAINS = [
    "github.com", "stackoverflow.com", "pypi.org", "docs.python.org", "readthedocs.io",
    "fastapi.tiangolo.com", "docs.pydantic.dev", "docs.sqlalchemy.org", "www.starlette.io",
]
DEPENDENCY_FILES = ("requirements.txt", "requirements-prod.txt", "requirements/base.txt", "pyproject.toml")
# Import name → distribution name, for the common cases where they differ.
DIST_NAMES = {"yaml": "PyYAML", "sklearn": "scikit-learn", "PIL": "Pillow", "dateutil": "python-dateutil",
              "jwt": "PyJWT", "psycopg2": "psycopg2", "bs4": "beautifulsoup4", "dotenv": "python-dotenv"}
MAX_CONTENT_CHARS = 700


@dataclass
class WebResult:
    title: str
    url: str
    content: str


class WebSearch(Protocol):
    def search(self, query: str) -> list[WebResult]: ...


@dataclass
class LibraryHint:
    package: str
    version: str | None
    reason: str  # "error raised inside <pkg>" or "dependency changed in this deploy"


# --------------------------------------------------------------------------- when to search


def _package_from_path(path: str) -> str | None:
    """Top-level package of a library frame; None for the standard library or unknown paths."""
    p = path.replace("\\", "/")
    if "site-packages/" in p:
        p = p.split("site-packages/", 1)[1]
    elif "dist-packages/" in p:
        p = p.split("dist-packages/", 1)[1]
    elif PurePosixPath(p).is_absolute() or re.match(r"^[A-Za-z]:/", p):
        return None  # absolute path outside site-packages: interpreter or stdlib
    top = PurePosixPath(p).parts[0] if PurePosixPath(p).parts else ""
    top = top.removesuffix(".py")
    if not top.isidentifier() or top.startswith("_") or top in sys.stdlib_module_names:
        return None
    return DIST_NAMES.get(top, top)


_REQ_LINE = re.compile(r"^\s*[\"']?([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(==|>=|~=|<=|>|<)\s*([0-9][\w.]*)")


def parse_pins(text: str) -> dict[str, str]:
    """name (lower case) → version from requirements.txt or pyproject dependency lines."""
    pins = {}
    for line in text.splitlines():
        m = _REQ_LINE.match(line.strip().lstrip("+-"))
        if m:
            pins[m.group(1).lower()] = m.group(3)
    return pins


def changed_dependencies(diff: str) -> list[str]:
    """Packages whose version line was added or changed in a dependency file within `diff`."""
    changed, in_dep_file = [], False
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            in_dep_file = any(line.endswith("/" + f) for f in DEPENDENCY_FILES)
            continue
        if in_dep_file and line.startswith("+") and not line.startswith("+++"):
            for name in parse_pins(line[1:]):
                if name not in changed:
                    changed.append(name)
    return changed


def detect_library(alert: Alert, pins: dict[str, str], dependency_diff: str) -> LibraryHint | None:
    """Decide whether a web search is worth it, and for which package."""
    if alert.stacktrace and not alert.stacktrace[-1].in_app:
        package = _package_from_path(alert.stacktrace[-1].file)
        if package:
            return LibraryHint(package, pins.get(package.lower()), f"error raised inside {package}")
    changed = changed_dependencies(dependency_diff)
    if changed:
        package = changed[0]
        return LibraryHint(package, pins.get(package), "dependency changed in this deploy")
    return None


# --------------------------------------------------------------------------- query


_SCRUB = [
    (re.compile(r"https?://\S+"), " "),
    (re.compile(r"\S+@\S+\.\w+"), " "),
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}"), " "),  # uuid
    (re.compile(r"\b[0-9a-fA-F]{12,}\b"), " "),  # hashes, hex ids
    (re.compile(r"(['\"]).*?\1"), " "),  # quoted values are usually user data
    (re.compile(r"(/[\w.-]+){2,}"), " "),  # file system paths
    (re.compile(r"\b\d+(\.\d+)*\b"), " "),  # numbers, ids, sizes
    (re.compile(r"[^\w\s:.,()-]"), " "),
    (re.compile(r"\s+"), " "),
]


def scrub_message(message: str, limit: int = 100) -> str:
    text = message.splitlines()[0] if message else ""
    for pattern, repl in _SCRUB:
        text = pattern.sub(repl, text)
    return text.strip(" :.,-")[:limit].strip()


def build_query(hint: LibraryHint, alert: Alert) -> str:
    parts = [hint.package, hint.version or "", alert.exception_type, scrub_message(alert.message)]
    if hint.reason.startswith("dependency"):
        parts.append("breaking change")
    return " ".join(p for p in parts if p)


def format_results(results: list[WebResult]) -> str:
    out = []
    for r in results:
        content = " ".join(r.content.split())[:MAX_CONTENT_CHARS]
        out.append(f"- {r.title.strip()[:150]}\n  {r.url}\n  {content}")
    return "\n".join(out)


# --------------------------------------------------------------------------- Tavily


class TavilySearch:
    """Tavily /search, basic depth (1 credit per call), trusted domains only. Standard library HTTP."""

    def __init__(self, api_key: str, max_results: int = 4, timeout_s: float = 20.0,
                 domains: list[str] | None = None):
        if not api_key:
            raise ValueError("TAVILY_API_KEY is not set")
        self.api_key = api_key
        self.max_results = max_results
        self.timeout = timeout_s
        self.domains = domains or TRUSTED_DOMAINS

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(TAVILY_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
                                     headers={"Authorization": f"Bearer {self.api_key}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # fixed https URL, not user input
            return json.loads(resp.read().decode("utf-8"))

    def search(self, query: str) -> list[WebResult]:
        data = self._post({"query": query, "search_depth": "basic", "max_results": self.max_results,
                           "chunks_per_source": 2, "include_domains": self.domains, "include_answer": False,
                           "include_raw_content": False})
        return [WebResult(title=r.get("title", ""), url=r.get("url", ""), content=r.get("content", ""))
                for r in data.get("results", []) if r.get("url")]


class CachedSearch:
    """Same query → same results without spending a credit. Cache file is shared across runs."""

    def __init__(self, inner: WebSearch, path: Path):
        self.inner = inner
        self.path = Path(path)

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def search(self, query: str) -> list[WebResult]:
        cache = self._load()
        if query in cache:
            return [WebResult(**r) for r in cache[query]]
        results = self.inner.search(query)
        cache[query] = [r.__dict__ for r in results]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)
        return results


def from_settings(settings) -> WebSearch | None:
    """The configured search, or None when it is off or has no key."""
    if not settings.web_search or not settings.tavily_api_key:
        return None
    return CachedSearch(TavilySearch(settings.tavily_api_key), Path(settings.history_dir) / "_web_cache.json")
