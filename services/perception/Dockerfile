# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# Pip downloads (notably the ~426MB torch wheel) are cached in a BuildKit
# cache mount so a killed or retried build resumes instead of re-pulling
# the whole ML stack. Bump the network timeout for flaky/slow mirrors.
ENV PIP_DEFAULT_TIMEOUT=120

# ffmpeg is required for the conversation-anchored clip builder
# (slices an mp4 covering the conversation window from the matching
# Recording row). libgl/libglib are needed by opencv-python-headless
# which the perception pipeline pulls for frame decoding.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libpq-dev ffmpeg ca-certificates \
    cmake build-essential libboost-all-dev \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
# Install CPU-only torch first, from PyTorch's own CDN. these containers
# have no GPU (InsightFace, faster-whisper, and YOLO all run on CPU), so
# the default CUDA torch wheel is ~426MB of unused GPU libs that also
# stalled repeatedly on slow networks. The CPU wheel is ~190MB and once
# torch is satisfied, the main install below won't pull it again.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN --mount=type=cache,target=/root/.cache/pip pip install .

# Bake the default YOLO weights into the image from the build context.
# ultralytics' own auto-download fails github TLS inside both the build
# sandbox and the runtime container (cert chain to objects.githubusercontent
# .com is not verifiable there), and offline/locked-down hosts have no
# network at all. The weights are vendored in the repo and copied in here,
# so detection works with zero network. detector.py resolves a bare model
# filename against NURBY_MODELS_DIR (default /app/models) before any
# network fallback.
ENV NURBY_MODELS_DIR=/app/models
COPY services/perception/models/yolov8n.pt /app/models/yolov8n.pt

# Face recognition engine. faces.py uses InsightFace (buffalo_l ArcFace)
# over onnxruntime, but these are not in pyproject, so face recognition
# (and the People tab) was silently disabled in the image. Install here
# (perception-only, to keep api/ingestion lean) and pre-bake the model so
# first use needs no network. without the pre-bake the model downloads at
# runtime and fails on offline / locked-down hosts.
RUN --mount=type=cache,target=/root/.cache/pip pip install insightface onnxruntime
# Bake the buffalo_l model pack from the build context. InsightFace's
# auto-download pulls from github release-assets, which fails TLS in this
# environment (and on offline hosts). FaceAnalysis(name='buffalo_l') reads
# from ~/.insightface/models/buffalo_l, so the vendored onnx files land there.
COPY services/perception/models/insightface/ /root/.insightface/models/

# License-plate OCR engine. plates.py uses EasyOCR but it was not in
# pyproject, so plate reading (and the Vehicles feature) was disabled in
# the image. Install it and pre-download the detection + recognition
# models so first read needs no network.
RUN --mount=type=cache,target=/root/.cache/pip pip install easyocr
# Bake the EasyOCR detector (CRAFT) + english_g2 recognizer from the build
# context. EasyOCR otherwise downloads them from github release-assets, which
# fails TLS here and on offline hosts. Reader() reads ~/.EasyOCR/model.
COPY services/perception/models/easyocr/ /root/.EasyOCR/model/

COPY shared/ shared/
COPY services/__init__.py services/__init__.py
COPY services/perception/ services/perception/
COPY services/search/ services/search/
COPY services/api/ services/api/
# The pipeline and audio path import the rule engine and these siblings.
# Ship them so imports resolve at startup. heavy paths load lazily.
COPY services/events/ services/events/
COPY services/discovery/ services/discovery/
COPY services/notify/ services/notify/
COPY services/recap.py services/recap.py
COPY integrations/ integrations/

CMD ["python", "-m", "services.perception.main"]
