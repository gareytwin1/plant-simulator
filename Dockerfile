# syntax=docker/dockerfile:1
# Targets: `runtime` (default, what ships) and `test` (runs the suite).

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv/plant-simulator
COPY requirements.txt ./
RUN pip install -r requirements.txt

FROM base AS test
COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
COPY . .
CMD ["python", "-m", "pytest", "-q"]

FROM base AS runtime
COPY app ./app
COPY config ./config
COPY static ./static
COPY gunicorn.conf.py ./
RUN useradd --system --no-create-home plant
USER plant
EXPOSE 8000
# One worker process, threads for concurrency: see gunicorn.conf.py.
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app.main:app"]
