FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libpq-dev ffmpeg \
    cmake build-essential libboost-all-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir .

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
COPY services/recap.py services/recap.py
COPY services/__init__.py services/__init__.py
COPY alembic/ alembic/
COPY alembic.ini .

CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
