FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libpq-dev ffmpeg \
    cmake build-essential libboost-all-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY shared/ shared/
# The agent tool registry transitively imports the analyzer, which pulls
# perception + search + events + notify. Ship the same light-at-import
# subtrees the api image does so every tool the registry can dispatch
# resolves. Heavy paths (torch/insightface/clip) only load when an
# analyze_* tool is actually called.
COPY services/agent/ services/agent/
COPY services/perception/ services/perception/
COPY services/search/ services/search/
COPY services/events/ services/events/
COPY services/notify/ services/notify/
COPY services/mcp/ services/mcp/
COPY services/guardian/ services/guardian/
COPY services/__init__.py services/__init__.py

CMD ["python", "-m", "services.mcp.server"]
