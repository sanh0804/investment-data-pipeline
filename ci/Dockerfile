FROM python:3.12-slim-bookworm
WORKDIR /workspace
COPY requirements.txt requirements-dev.txt requirements-dbt.txt ./
RUN python -m venv .venv && .venv/bin/pip install --no-cache-dir -r requirements-dev.txt \
    && python -m venv .venv-dbt && .venv-dbt/bin/pip install --no-cache-dir -r requirements-dbt.txt \
    && .venv/bin/pip check && .venv-dbt/bin/pip check
COPY pipeline ./pipeline
COPY scripts ./scripts
COPY tests ./tests
COPY sql ./sql
COPY analytics ./analytics
CMD ["bash", "-ec", ".venv/bin/python -m pytest -q --junitxml=data/unit-results.xml && .venv/bin/python scripts/verify_ci.py"]
