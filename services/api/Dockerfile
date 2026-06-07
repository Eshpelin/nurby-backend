# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

ENV PIP_DEFAULT_TIMEOUT=120

# Exact build commit, surfaced by /api/system/version. Set by CI on
# release; harmless when unset for local builds.
ARG NURBY_BUILD_SHA=""
ENV NURBY_BUILD_SHA=$NURBY_BUILD_SHA

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libpq-dev ffmpeg \
    cmake build-essential libboost-all-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
# CPU-only torch first (no GPU here). avoids the ~426MB CUDA wheel that
# stalls on slow networks; the main install reuses the satisfied torch.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN --mount=type=cache,target=/root/.cache/pip pip install .

COPY shared/ shared/
COPY services/api/ services/api/
# Several api routes pull helpers from sibling services. Ship them so
# the api process can import them. Each module is light at import time;
# heavy paths (torch/torchreid/insightface) only load when their
# functions are called.
COPY services/perception/ services/perception/
COPY services/notify/ services/notify/
COPY services/events/ services/events/
COPY services/search/ services/search/
COPY services/digest/ services/digest/
COPY services/discovery/ services/discovery/
COPY services/agent/ services/agent/
COPY services/guardian/ services/guardian/
COPY services/recap.py services/recap.py
COPY services/__init__.py services/__init__.py
# Device alert presets. routes/devices.py imports integrations.devices.
COPY integrations/ integrations/
COPY alembic/ alembic/
COPY alembic.ini .
COPY VERSION .

CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
