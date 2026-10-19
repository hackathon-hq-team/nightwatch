"""История запусков: один JSON на инцидент в history/. Её читают UI (Тимур) и eval.py (Даник).

Память: find_similar ищет прошлые закрытые инциденты с похожей сигнатурой ошибки.
Найденное — только подсказка; новый фикс всё равно проходит воспроизведение и проверку.
"""
from __future__ import annotations

import re
from pathlib import Path

from .schemas import Alert, Incident, IncidentState

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "")}


class HistoryStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, incident_id: str) -> Path:
        return self.root / f"{incident_id}.json"

    def save(self, incident: Incident) -> None:
        tmp = self.path(incident.id).with_suffix(".tmp")
        tmp.write_text(incident.model_dump_json(indent=2))
        tmp.replace(self.path(incident.id))  # атомарно: UI не прочитает полфайла

    def load(self, incident_id: str) -> Incident:
        return Incident.model_validate_json(self.path(incident_id).read_text())

    def all(self) -> list[Incident]:
        out = []
        for p in sorted(self.root.glob("inc-*.json")):
            try:
                out.append(Incident.model_validate_json(p.read_text()))
            except Exception:  # битый файл не должен ронять UI
                continue
        return sorted(out, key=lambda i: i.created_at, reverse=True)

    def find_similar(self, alert: Alert, k: int = 3, exclude: str | None = None) -> list[tuple[float, Incident]]:
        """Похожесть: тип исключения (0.4) + место ошибки (0.3) + слова сообщения (0.3).
        Учитываем только закрытые инциденты — verified или reported."""
        sig = alert.signature()
        msg = _tokens(alert.message)
        scored = []
        for inc in self.all():
            if inc.id == exclude or inc.state not in (IncidentState.verified, IncidentState.reported):
                continue
            other = inc.alert.signature()
            score = 0.0
            if other.exception_type == sig.exception_type:
                score += 0.4
            if sig.file and other.file == sig.file:
                score += 0.15
            if sig.function and other.function == sig.function:
                score += 0.15
            om = _tokens(inc.alert.message)
            if msg and om:
                score += 0.3 * len(msg & om) / len(msg | om)
            if score >= 0.5:
                scored.append((round(score, 3), inc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:k]


def summarize_for_memory(inc: Incident) -> str:
    """Короткая выжимка прошлого инцидента для контекста модели."""
    lines = [f"Инцидент {inc.id} ({inc.state.value}): {inc.alert.exception_type}: {inc.alert.message[:200]}"]
    sig = inc.alert.signature()
    if sig.file:
        lines.append(f"Место: {sig.file}:{sig.function}")
    if inc.investigation:
        best = max(inc.investigation.hypotheses, key=lambda h: h.confidence)
        lines.append(f"Причина (гипотеза модели): {best.summary}")
    if inc.state == IncidentState.verified and inc.final_patch:
        lines.append(f"Проверенный фикс: {inc.final_patch.explanation}")
        lines.append(inc.final_patch.diff[:1500])
    if inc.rejected_hypotheses:
        lines.append("Не подтвердились: " + "; ".join(inc.rejected_hypotheses[:5]))
    return "\n".join(lines)
