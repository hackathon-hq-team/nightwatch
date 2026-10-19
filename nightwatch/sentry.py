"""Преобразование события Sentry в Alert. Используется вебхуком (Тимур) и сценариями (Даник)."""
from __future__ import annotations

from .schemas import Alert, Frame


def _event(payload: dict) -> dict:
    # Вебхук issue alert: {"data": {"event": {...}}}; событие из API — сразу объект события.
    data = payload.get("data", payload)
    return data.get("event", data)


def _repo_path(path: str, code_root: str | None, markers: tuple[str, ...]) -> str:
    """Абсолютный путь в контейнере → путь в репозитории."""
    if code_root and path.startswith(code_root):
        return path[len(code_root):].lstrip("/")
    for m in markers:
        if f"/{m}" in path:
            return path[path.rindex(f"/{m}") + 1:]
    return path.lstrip("/")


def alert_from_sentry(payload: dict, code_root: str | None = None,
                      markers: tuple[str, ...] = ("app/", "src/")) -> Alert:
    """code_root — корень кода в контейнере (например /srv/app/), по умолчанию из NIGHTWATCH_CODE_ROOT."""
    import os
    code_root = code_root or os.getenv("NIGHTWATCH_CODE_ROOT")
    ev = _event(payload)
    values = (ev.get("exception") or {}).get("values") or []
    exc = values[-1] if values else {}
    frames = []
    for fr in ((exc.get("stacktrace") or {}).get("frames") or []):
        path = _repo_path(fr.get("filename") or fr.get("abs_path") or "?", code_root, markers)
        frames.append(Frame(file=path.lstrip("/"), function=fr.get("function") or "?",
                            line=fr.get("lineno"), in_app=bool(fr.get("in_app", True))))
    req = ev.get("request") or None
    if req:
        req = {k: req.get(k) for k in ("method", "url", "query_string", "data") if req.get(k) is not None}
    tags = dict(ev.get("tags") or []) if isinstance(ev.get("tags"), list) else (ev.get("tags") or {})
    commit = ev.get("release") or tags.get("commit") or tags.get("release")
    if not commit:
        raise ValueError("в событии Sentry нет release/commit — укажите release=<git sha> в Sentry SDK")
    return Alert(
        source="sentry",
        title=ev.get("title") or f"{exc.get('type', 'Error')}: {exc.get('value', '')}"[:200],
        exception_type=exc.get("type") or "Error",
        message=exc.get("value") or "",
        stacktrace=frames,
        request=req,
        commit=commit,
        previous_good_commit=tags.get("previous_release"),
        raw=payload,
    )
