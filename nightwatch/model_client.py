"""Nemotron client for Nebius Token Factory (OpenAI-compatible API).

On top of a plain chat call it:
- asks for JSON and validates it against a Pydantic schema; on failure it retries once
  with the validation error;
- strips <think>…</think> and ```json fences;
- records tokens and cost of every call (Incident.usage);
- enforces the per-incident and daily budgets (BudgetExceeded).
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
    """What the pipeline needs. Tests plug in a ScriptedLLM."""

    def complete_json(self, step: str, system: str, user: str, schema: type[T], usage: Usage,
                      max_tokens: int = 4096, model: str | None = None) -> T: ...


# --------------------------------------------------------------------------- JSON extraction

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    """Return the first JSON object in a model reply."""
    text = _THINK.sub("", text or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in the reply")
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
    raise ValueError("JSON object is not closed")


def schema_hint(schema: type[BaseModel]) -> str:
    return json.dumps(schema.model_json_schema(), ensure_ascii=False)


# --------------------------------------------------------------------------- daily budget


class DailyBudget:
    """Spend for today. Stored in history/_spend.json, shared by all processes."""

    def __init__(self, history_dir: Path, limit_usd: float):
        self.path = Path(history_dir) / "_spend.json"
        self.limit = limit_usd

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def spent_today(self) -> float:
        return float(self._load().get(date.today().isoformat(), 0.0))

    def add(self, usd: float) -> None:
        data = self._load()
        key = date.today().isoformat()
        data[key] = round(float(data.get(key, 0.0)) + usd, 6)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def check(self) -> None:
        if self.spent_today() >= self.limit:
            raise BudgetExceeded(f"daily budget ${self.limit:.2f} exhausted")


# --------------------------------------------------------------------------- client


class ModelClient:
    def __init__(self, settings: Settings | None = None, model: str | None = None, client=None):
        self.s = settings or Settings()
        self.model = model or self.s.model
        if client is None:
            if not self.s.api_key:
                raise ModelError("NEBIUS_API_KEY is not set (see .env.example)")
            from openai import OpenAI

            client = OpenAI(base_url=self.s.base_url, api_key=self.s.api_key, timeout=self.s.request_timeout_s)
        self._client = client
        self.daily = DailyBudget(self.s.history_dir, self.s.daily_budget_usd)
        self._json_mode = True  # switched off if the endpoint does not support response_format

    # -- low level

    def _chat(self, messages: list[dict], max_tokens: int, model: str):
        kwargs = dict(model=model, messages=messages, max_tokens=max_tokens, temperature=0.2)
        if self._json_mode:
            try:
                return self._client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
            except Exception as e:  # endpoint does not know response_format — continue without it
                if "response_format" not in str(e) and "json" not in str(e).lower():
                    raise
                self._json_mode = False
        return self._client.chat.completions.create(**kwargs)

    def _record(self, step: str, resp, started: float, usage: Usage, model: str) -> None:
        u = getattr(resp, "usage", None)
        pt = getattr(u, "prompt_tokens", 0) or 0
        ct = getattr(u, "completion_tokens", 0) or 0
        pin, pout = self.s.price(model)
        cost = pt / 1e6 * pin + ct / 1e6 * pout
        usage.calls.append(ModelCall(step=step, model=model, prompt_tokens=pt, completion_tokens=ct,
                                     cost_usd=round(cost, 6), duration_s=round(time.time() - started, 2)))
        self.daily.add(cost)

    def _check_budget(self, usage: Usage) -> None:
        if usage.cost_usd >= self.s.incident_budget_usd:
            raise BudgetExceeded(f"incident budget ${self.s.incident_budget_usd:.2f} exhausted")
        self.daily.check()

    def _dump(self, step: str, attempt: int, messages: list[dict], reply: str, usage: Usage) -> None:
        """Save the exact prompt and raw reply when NIGHTWATCH_DEBUG_DIR is set."""
        if not self.s.debug_dir:
            return
        self.s.debug_dir.mkdir(parents=True, exist_ok=True)
        n = len(usage.calls)
        path = self.s.debug_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{n:02d}-{step}{'-retry' if attempt else ''}.json"
        call = usage.calls[-1] if usage.calls else None
        path.write_text(json.dumps({"step": step, "model": call.model if call else self.model, "messages": messages, "reply": reply,
                                    "usage": call.model_dump() if call else None}, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- public

    def complete_json(self, step: str, system: str, user: str, schema: type[T], usage: Usage,
                      max_tokens: int = 4096, model: str | None = None) -> T:
        """model overrides the configured model for this call (used for escalation)."""
        model = model or self.model
        system = (
            f"{system}\n\nReply with ONLY one JSON object, no text around it. "
            f"JSON Schema of the reply:\n{schema_hint(schema)}"
        )
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error = ""
        for attempt in range(2):
            self._check_budget(usage)
            started = time.time()
            resp = self._chat(messages, max_tokens, model)
            self._record(step if attempt == 0 else f"{step}:retry", resp, started, usage, model)
            text = resp.choices[0].message.content or ""
            self._dump(step, attempt, messages, text, usage)
            try:
                return schema.model_validate(extract_json(text))
            except (ValueError, ValidationError) as e:
                last_error = str(e)[:1500]
                messages += [
                    {"role": "assistant", "content": text[:4000]},
                    {"role": "user", "content": f"The reply failed schema validation:\n{last_error}\n"
                                                "Return corrected JSON."},
                ]
        raise ModelError(f"{step}: model did not return valid JSON: {last_error}")

    def list_models(self) -> list[str]:
        return [m.id for m in self._client.models.list().data]
