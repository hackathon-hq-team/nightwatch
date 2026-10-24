"""Patch as search/replace edits, turned into an exact unified diff by code.

Models are good at saying "replace this code with that code" and bad at writing unified diffs
line-perfectly: a single invented context line (a blank line that is not in the file) makes
`git apply` reject a correct fix. So the model returns edits — the exact current text and its
replacement — and Nightwatch builds the diff from the real file content. Runner, verifier and UI
keep working with a unified diff as before.
"""
from __future__ import annotations

import difflib

from pydantic import BaseModel, Field

from .schemas import PatchProposal


class Edit(BaseModel):
    file: str  # path relative to the repository root
    old: str  # exact current text to replace, copied from the file (empty only for a new file)
    new: str  # replacement text


class PatchReply(BaseModel):
    """What the patch prompt asks for. `diff` is accepted for compatibility with scripted replies."""

    edits: list[Edit] = Field(default_factory=list)
    diff: str = ""
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)


class EditError(ValueError):
    """An edit that cannot be applied; the message goes back to the model."""


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n")


def _locate(content: str, old: str, path: str) -> tuple[int, int]:
    """(start, end) of `old` in `content`: exact match first, then ignoring trailing whitespace."""
    count = content.count(old)
    if count == 1:
        start = content.index(old)
        return start, start + len(old)
    if count > 1:
        raise EditError(f"`old` text matches {count} places in {path}; include more surrounding lines")

    # Tolerate trailing spaces and a missing/extra final newline: compare line by line, rstripped.
    want = [ln.rstrip() for ln in old.strip("\n").split("\n")]
    lines = content.split("\n")
    hits = [i for i in range(len(lines) - len(want) + 1)
            if [ln.rstrip() for ln in lines[i:i + len(want)]] == want]
    if len(hits) > 1:
        raise EditError(f"`old` text matches {len(hits)} places in {path}; include more surrounding lines")
    if not hits:
        raise EditError(f"`old` text not found in {path}; copy it exactly from the current file content "
                        "(no line numbers), including indentation")
    offsets = [0]
    for ln in lines:
        offsets.append(offsets[-1] + len(ln) + 1)
    start = offsets[hits[0]]
    end = offsets[hits[0] + len(want)] - 1  # up to the end of the last matched line, without its newline
    return start, end


def _unified(path: str, before: str | None, after: str) -> str:
    a = [] if before is None else before.splitlines(keepends=True)
    b = after.splitlines(keepends=True)
    out = []
    for line in difflib.unified_diff(a, b, fromfile="/dev/null" if before is None else f"a/{path}",
                                     tofile=f"b/{path}", n=3):
        out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(out)


def edits_to_diff(edits: list[Edit], read_file) -> str:
    """Apply edits to the current files (read_file(path) -> text | None) and return a unified diff."""
    if not edits:
        raise EditError("no edits")
    originals: dict[str, str | None] = {}
    current: dict[str, str] = {}
    for i, e in enumerate(edits, 1):
        path = e.file.strip().replace("\\", "/").removeprefix("./")
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise EditError(f"edit {i}: invalid path {e.file!r}")
        if path not in current:
            text = read_file(path)
            originals[path] = None if text is None else _lf(text)
            current[path] = originals[path] or ""
        content = current[path]
        old, new = _lf(e.old), _lf(e.new)
        if not old:
            if originals[path] is not None or content:
                raise EditError(f"edit {i}: empty `old` is only for creating a new file, {path} exists")
            current[path] = new
            continue
        if originals[path] is None:
            raise EditError(f"edit {i}: {path} does not exist")
        try:
            start, end = _locate(content, old, path)
        except EditError as err:
            raise EditError(f"edit {i}: {err}") from None
        current[path] = content[:start] + new + content[end:]
    diff = "".join(_unified(p, originals[p], current[p]) for p in current if current[p] != (originals[p] or ""))
    if not diff:
        raise EditError("edits do not change any file")
    return diff


def to_proposal(reply: PatchReply, read_file) -> PatchProposal:
    """Edits (preferred) or a raw diff → the PatchProposal stored in the incident."""
    diff = edits_to_diff(reply.edits, read_file) if reply.edits else reply.diff
    return PatchProposal(diff=diff, explanation=reply.explanation, evidence_ids=reply.evidence_ids)
