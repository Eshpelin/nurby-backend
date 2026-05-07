FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libpq-dev ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY shared/ shared/
COPY services/api/ services/api/
COPY alembic/ alembic/
COPY alembic.ini .

CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
