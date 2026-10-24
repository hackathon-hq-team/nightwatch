# 🚀 Project name

> The topic and project name will be defined at the hackathon. This repository is a template: create a project with **Use this template**.

## 💡 Idea
Briefly describe the problem and target users (1–2 sentences).

## 🛠 Stack
- Frontend: —
- Backend: —
- Deployment: —

## ▶️ Getting started

Streamlit operator console: [UI setup and demo modes](docs/ui.md).

Docker runner setup and isolation checks: [instructions for Timur](docs/docker-runner.md).

Danik's verifier is implemented: [behaviour, validation and limitations](docs/verifier.md).
Select it with the existing CLI option `--verifier team`.

The demo project now includes seven scenarios, including five harder bug scenarios:
[setup instructions for Nikita and Timur](docs/demo-handoff.md).
Prepare from the repository root: `python -m tools.prepare_demo --output .demo-workspace`.
See the [scenario contracts, independent acceptance checks and live-run handoff](docs/complex-scenarios.md).
Offline cycles use scripted responses; live model quality, Tavily and container execution require separate validation.

Three additional compound scenarios are available as the opt-in `advanced` suite:
[contracts, offline checks and token-budget comparison](docs/advanced-scenarios.md).
Use `python -m tools.prepare_demo --suite advanced --output .qa-results/advanced-workspace`
for offline preparation. The default seven-scenario run remains unchanged.

Combined verifier/demo validation and failure checks:
[QA instructions](docs/qa-integration.md).

```bash
# 1. Clone the repository
git clone <url>
cd <repo>

# 2. Copy the environment template and configure values
cp .env.example .env

# 3. Install dependencies and run
# (complete when the stack is ready)
```

## 🌐 Demo
Deployed demo URL: —

## 👥 Team
| Member | Role |
|----------|------|
| — | — |

## 🔀 Workflow
- Keep `main` working; submit changes through pull requests.
- One branch per task: `feat/...`, `fix/...`, `docs/...`.
- Track each task as an issue on the GitHub Projects board.
- Never commit secrets; include only `.env.example` without secret values.
