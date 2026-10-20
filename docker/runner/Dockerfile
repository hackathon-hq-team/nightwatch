FROM python:3.11.14-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest==8.4.2
COPY nightwatch/pytest_plugin.py /opt/nightwatch/nightwatch_pytest_plugin.py
ENV PYTHONPATH=/workspace:/opt/nightwatch \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    NIGHTWATCH_REPORT=/tmp/nightwatch-report.json \
    HOME=/tmp
WORKDIR /workspace
USER 10001:10001
CMD ["sleep", "infinity"]
