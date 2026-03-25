FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
COPY polyarb/ polyarb/

RUN pip install --no-cache-dir -e ".[dev]"

ENTRYPOINT ["python", "-m", "polyarb"]
