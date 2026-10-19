"""Запуск конвейера из командной строки — для отладки промптов.

  python -m nightwatch.cli run --repo ../demo-app --alert scenarios/bug01.json
  python -m nightwatch.cli models          # проверить ключ и список моделей
  python -m nightwatch.cli show inc-1234abcd
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Settings
from .context import RepoView
from .history import HistoryStore
from .schemas import Alert


def _load_alert(path: str) -> Alert:
    data = json.loads(Path(path).read_text())
    if "exception" in data or "data" in data:
        from .sentry import alert_from_sentry
        return alert_from_sentry(data)
    return Alert.model_validate(data)


def cmd_run(args) -> int:
    from .model_client import ModelClient
    from .pipeline import Pipeline

    s = Settings()
    if args.runner == "local":
        from .dev.local_runner import LocalRunner
        runner = LocalRunner(args.repo)
    else:  # настоящий runner Тимура
        from runner import Runner  # type: ignore
        runner = Runner(args.repo)
    if args.verifier == "reference":
        from .dev.reference_verifier import ReferenceVerifier
        verifier = ReferenceVerifier()
    else:
        from verifier import Verifier  # type: ignore
        verifier = Verifier()

    pipe = Pipeline(ModelClient(s), runner, verifier, RepoView(args.repo), HistoryStore(s.history_dir), s)
    inc = pipe.run(_load_alert(args.alert))
    for e in inc.events:
        print(f"{e.at:%H:%M:%S}  {e.state.value:<13} {e.message}")
    print(f"\nИтог: {inc.state.value}  ·  ${inc.usage.cost_usd:.4f}  ·  {inc.usage.tokens} токенов  ·  "
          f"{s.history_dir}/{inc.id}.json")
    if inc.final_patch:
        print("\n" + inc.final_patch.diff)
    return 0 if inc.state.value in ("verified", "reported") else 1


def cmd_models(_args) -> int:
    from .model_client import ModelClient
    c = ModelClient()
    ids = c.list_models()
    for m in sorted(ids):
        mark = "  <- сейчас" if m == c.model else ""
        if "nemotron" in m.lower() or mark:
            print(m + mark)
    if c.model not in ids:
        print(f"\nВНИМАНИЕ: модели {c.model} нет в списке — задайте NIGHTWATCH_MODEL", file=sys.stderr)
        return 1
    return 0


def cmd_show(args) -> int:
    inc = HistoryStore(Settings().history_dir).load(args.id)
    print(inc.model_dump_json(indent=2))
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="nightwatch")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="расследовать алерт")
    r.add_argument("--repo", required=True, help="путь к git-репозиторию учебного проекта")
    r.add_argument("--alert", required=True, help="JSON: Alert или событие Sentry")
    r.add_argument("--runner", choices=["local", "team"], default="local")
    r.add_argument("--verifier", choices=["reference", "team"], default="reference")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("models", help="проверить ключ и модели").set_defaults(fn=cmd_models)
    s = sub.add_parser("show", help="показать инцидент из истории")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
