"""Клиент Nemotron через Nebius Token Factory (OpenAI-совместимый API).

Что делает сверх обычного вызова:
- просит JSON и валидирует его по Pydantic-схеме; при ошибке — один повтор
  с текстом ошибки валидации;
- вырезает <think>…</think> и обёртки ```json;
- считает токены и стоимость каждого вызова (Incident.usage);
- соблюдает бюджет инцидента и дневной бюджет (BudgetExceeded).
"""
from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .config import Settings
from .schemas import ModelCall, Usage

T = TypeVar("T", bound=BaseModel)


class BudgetExceeded(RuntimeError):
    pass


class ModelError(RuntimeError):
    pass


class LLM(Protocol):
    """То, что нужно pipeline. Тесты подставляют ScriptedLLM."""

    def complete_json(self, step: str, system: str, user: str, schema: type[T], usage: Usage,
                      max_tokens: int = 4096) -> T: ...


# --------------------------------------------------------------------------- JSON extraction

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    """Достаёт первый JSON-объект из ответа модели."""
    text = _THINK.sub("", text or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("в ответе нет JSON-объекта")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("JSON-объект не закрыт")


def schema_hint(schema: type[BaseModel]) -> str:
    return json.dumps(schema.model_json_schema(), ensure_ascii=False)


# --------------------------------------------------------------------------- daily budget


class DailyBudget:
    """Сколько потрачено сегодня. Хранится в history/_spend.json, общий для всех процессов."""

    def __init__(self, history_dir: Path, limit_usd: float):
        self.path = Path(history_dir) / "_spend.json"
        self.limit = limit_usd

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def spent_today(self) -> float:
        return float(self._load().get(date.today().isoformat(), 0.0))

    def add(self, usd: float) -> None:
        data = self._load()
        key = date.today().isoformat()
        data[key] = round(float(data.get(key, 0.0)) + usd, 6)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data))

    def check(self) -> None:
        if self.spent_today() >= self.limit:
            raise BudgetExceeded(f"дневной бюджет ${self.limit:.2f} исчерпан")


# --------------------------------------------------------------------------- client


class ModelClient:
    def __init__(self, settings: Settings | None = None, model: str | None = None, client=None):
        self.s = settings or Settings()
        self.model = model or self.s.model
        if client is None:
            if not self.s.api_key:
                raise ModelError("NEBIUS_API_KEY не задан (см. .env.example)")
            from openai import OpenAI

            client = OpenAI(base_url=self.s.base_url, api_key=self.s.api_key, timeout=self.s.request_timeout_s)
        self._client = client
        self.daily = DailyBudget(self.s.history_dir, self.s.daily_budget_usd)
        self._json_mode = True  # выключается, если эндпоинт не поддерживает response_format

    # -- low level

    def _chat(self, messages: list[dict], max_tokens: int):
        kwargs = dict(model=self.model, messages=messages, max_tokens=max_tokens, temperature=0.2)
        if self._json_mode:
            try:
                return self._client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
            except Exception as e:  # эндпоинт не знает response_format — работаем без него
                if "response_format" not in str(e) and "json" not in str(e).lower():
                    raise
                self._json_mode = False
        return self._client.chat.completions.create(**kwargs)

    def _record(self, step: str, resp, started: float, usage: Usage) -> None:
        u = getattr(resp, "usage", None)
        pt = getattr(u, "prompt_tokens", 0) or 0
        ct = getattr(u, "completion_tokens", 0) or 0
        pin, pout = self.s.price(self.model)
        cost = pt / 1e6 * pin + ct / 1e6 * pout
        usage.calls.append(ModelCall(step=step, model=self.model, prompt_tokens=pt, completion_tokens=ct,
                                     cost_usd=round(cost, 6), duration_s=round(time.time() - started, 2)))
        self.daily.add(cost)

    def _check_budget(self, usage: Usage) -> None:
        if usage.cost_usd >= self.s.incident_budget_usd:
            raise BudgetExceeded(f"бюджет инцидента ${self.s.incident_budget_usd:.2f} исчерпан")
        self.daily.check()

    # -- public

    def complete_json(self, step: str, system: str, user: str, schema: type[T], usage: Usage,
                      max_tokens: int = 4096) -> T:
        system = (
            f"{system}\n\nОтвечай ТОЛЬКО одним JSON-объектом, без пояснений вокруг. "
            f"JSON Schema ответа:\n{schema_hint(schema)}"
        )
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error = ""
        for attempt in range(2):
            self._check_budget(usage)
            started = time.time()
            resp = self._chat(messages, max_tokens)
            self._record(step if attempt == 0 else f"{step}:retry", resp, started, usage)
            text = resp.choices[0].message.content or ""
            try:
                return schema.model_validate(extract_json(text))
            except (ValueError, ValidationError) as e:
                last_error = str(e)[:1500]
                messages += [
                    {"role": "assistant", "content": text[:4000]},
                    {"role": "user", "content": f"Ответ не прошёл проверку схемы:\n{last_error}\nВерни исправленный JSON."},
                ]
        raise ModelError(f"{step}: модель не вернула корректный JSON: {last_error}")

    def list_models(self) -> list[str]:
        return [m.id for m in self._client.models.list().data]
