"""Settings from environment variables (.env). Never put keys in code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv is optional
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

# Price per 1M tokens (input, output), USD. Check against the Token Factory console.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "nvidia/nemotron-3-super-120b-a12b": (0.30, 0.90),
    "nvidia/nemotron-3-nano-30b-a3b": (0.06, 0.24),
    "nvidia/nemotron-3-ultra-550b-a55b": (0.60, 2.40),
}


@dataclass
class Settings:
    api_key: str = field(default_factory=lambda: os.getenv("NEBIUS_API_KEY", ""))
    base_url: str = field(
        default_factory=lambda: os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")
    )
    model: str = field(default_factory=lambda: os.getenv("NIGHTWATCH_MODEL", "nvidia/nemotron-3-super-120b-a12b"))

    history_dir: Path = field(default_factory=lambda: Path(os.getenv("NIGHTWATCH_HISTORY", "history")))

    # Limits
    context_token_budget: int = field(default_factory=lambda: int(os.getenv("NIGHTWATCH_CONTEXT_TOKENS", "150000")))
    max_repro_attempts: int = 2  # how many times the reproduction test may be rewritten
    max_patch_attempts: int = 3
    incident_budget_usd: float = field(default_factory=lambda: float(os.getenv("NIGHTWATCH_INCIDENT_BUDGET", "0.50")))
    daily_budget_usd: float = field(default_factory=lambda: float(os.getenv("NIGHTWATCH_DAILY_BUDGET", "5.00")))
    max_diff_lines: int = 200
    request_timeout_s: float = 180.0

    # When set, every model call (prompt and raw reply) is saved here for prompt debugging.
    debug_dir: Path | None = field(default_factory=lambda: Path(d) if (d := os.getenv("NIGHTWATCH_DEBUG_DIR")) else None)

    def price(self, model: str) -> tuple[float, float]:
        return DEFAULT_PRICES.get(model, DEFAULT_PRICES["nvidia/nemotron-3-super-120b-a12b"])
