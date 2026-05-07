FROM python:3.12-slim

WORKDIR /app

# ffmpeg is required for the conversation-anchored clip builder
# (slices an mp4 covering the conversation window from the matching
# Recording row). libgl/libglib are needed by opencv-python-headless
# which the perception pipeline pulls for frame decoding.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libpq-dev ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY shared/ shared/
COPY services/perception/ services/perception/
COPY services/search/ services/search/
COPY services/api/ services/api/

CMD ["python", "-m", "services.perception.main"]
