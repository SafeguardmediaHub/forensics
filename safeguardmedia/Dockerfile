FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NUMBA_DISABLE_JIT=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libglib2.0-0 \
    libgl1 \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY safeguardmedia/requirements-engines.txt /tmp/requirements-engines.txt
COPY safeguardmedia/pyproject.toml /app/safeguardmedia/pyproject.toml
COPY safeguardmedia/README.md /app/safeguardmedia/README.md
COPY safeguardmedia/src /app/safeguardmedia/src
COPY AFF /app/AFF
COPY VFF /app/VFF
COPY work /app/work

RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install -r /tmp/requirements-engines.txt && \
    python -m pip install -e /app/safeguardmedia

WORKDIR /app/safeguardmedia

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
