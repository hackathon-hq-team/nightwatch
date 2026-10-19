# Агент Nightwatch (часть Никиты)

Модель только предлагает: гипотезу, тест воспроизведения и патч. Запускает их `runner`, решает `verifier`.

```
алерт → context.py → Nemotron Super: гипотезы + тест → runner (сломанная и исправная версия)
      → verifier: воспроизведено? ── нет → отчёт
                         └─ да → Nemotron Super: патч → runner (весь набор) → verifier → Draft PR
                                         ↑ до 3 попыток, причина отказа уходит в следующую
```

## Модули

| Файл | Что делает |
| --- | --- |
| `nightwatch/schemas.py` | Общие модели: `Alert`, `Evidence`, `Investigation`, `PatchProposal`, `RunResult`, `Verdict`, `Incident` |
| `nightwatch/contracts.py` | Интерфейсы `Runner` (Тимур) и `Verifier` (Даник) |
| `nightwatch/model_client.py` | Nemotron через Token Factory: JSON по схеме, повтор при ошибке, учёт токенов, бюджеты |
| `nightwatch/context.py` | Доказательства с id `E1…`: стектрейс, запрос, логи, diff, файлы, похожие случаи |
| `nightwatch/history.py` | История `history/inc-*.json` и поиск похожих инцидентов (память) |
| `nightwatch/prompts.py` | Промпты расследования, патча и отчёта |
| `nightwatch/pipeline.py` | Оркестратор и состояния инцидента |
| `nightwatch/sentry.py` | Событие Sentry → `Alert` |
| `nightwatch/pytest_plugin.py` | Пишет результат pytest с сигнатурой исключения в JSON |
| `nightwatch/dev/` | Временные `LocalRunner` (pytest без Docker) и `ReferenceVerifier` — до готовности модулей Тимура и Даника |

## Контракт для Тимура (runner)

Реализовать `contracts.Runner`: `run(commit, test_files, patch, select) -> RunResult` и `tests_hash(...)`.

- копия проекта на `commit`, запись `test_files`, затем патч: `git apply --recount`, запасной вариант `patch -p1 --fuzz=3`;
- pytest с плагином: `NIGHTWATCH_REPORT=/tmp/r.json pytest -p nightwatch.pytest_plugin` — плагин отдаёт `passed` и `failures` с типом исключения, сообщением, файлом и функцией;
- `--network none`, без ключей в окружении, таймаут;
- неприменимый патч → `status="error", patch_applied=False`; `tests_hash` — sha256 всех `tests/**/*.py` после патча.

Эталон поведения — `nightwatch/dev/local_runner.py`.

## Контракт для Даника (verifier)

Реализовать `contracts.Verifier`: `check_reproduction(...)` и `check_patch(...)` → `Verdict(ok, reason, checks)`. Эталон — `nightwatch/dev/reference_verifier.py`; тесты в `tests/test_pipeline.py` показывают, какие случаи он обязан ловить.

## Запуск

```bash
pip install -e .
cp .env.example .env            # вписать NEBIUS_API_KEY
python -m nightwatch.cli models # точный ID Super в Token Factory
python -m nightwatch.cli run --repo ../demo-app --alert scenarios/bug01.json
pytest -q                       # тесты без сети и ключа
```

## Открытые вопросы

- Точный ID Nemotron 3 Super и поддержка `response_format` в Token Factory (клиент сам откатывается на обычный режим).
- Sentry SDK в учебном проекте должен ставить `release=<git sha>` и тег `previous_release` — иначе `sentry.py` не знает версию.
