"""Launch a configured demo checkout without passing Nightwatch credentials to it."""
from pathlib import Path
import os
import subprocess

from dotenv import dotenv_values


def main():
    root = Path(__file__).resolve().parents[1]
    config = dotenv_values(root / '.env')
    repo = Path(config['NIGHTWATCH_TARGET_REPO']).resolve()
    python = repo / '.venv/Scripts/python.exe'
    if not python.is_file() or not (repo / 'app/main.py').is_file():
        raise SystemExit('Prepare the Demo Shop checkout and its .venv first.')
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(('NEBIUS_', 'NIGHTWATCH_', 'TAVILY_', 'GH_', 'GITHUB_', 'SENTRY_')):
            env.pop(key)
    app_config = dotenv_values(repo / '.env')
    for key in ('SENTRY_DSN', 'SENTRY_ENVIRONMENT'):
        if app_config.get(key):
            env[key] = app_config[key]
    if not env.get('SENTRY_DSN'):
        raise SystemExit('Set SENTRY_DSN in the Demo Shop checkout .env first.')
    for key, ref in (('SENTRY_RELEASE', 'HEAD'), ('SENTRY_PREVIOUS_RELEASE', 'demo-good^{commit}')):
        env[key] = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', ref], text=True).strip()
    raise SystemExit(subprocess.call(
        [str(python), '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8001'],
        cwd=repo, env=env,
    ))


if __name__ == '__main__':
    main()
