#!/usr/bin/env bash
#
# Fetch the perception model weights that get baked into the perception
# image at build time. These are large binaries (~430MB total) and are not
# committed to git, so a clean checkout must run this once before building
# the perception service.
#
# Why baked instead of downloaded at runtime: ultralytics, InsightFace and
# EasyOCR all auto-download their weights from GitHub release-assets, whose
# TLS chain fails to verify inside the build sandbox and inside many runtime
# containers ("unable to get local issuer certificate"), and offline /
# locked-down hosts have no network at all. Downloading here on the host
# (where the network and CA store are healthy) and COPYing the files into
# the image makes detection, faces and plates work with zero runtime network.
#
# Re-running is cheap. existing files are skipped.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/services/perception/models"
mkdir -p "$DEST/insightface" "$DEST/easyocr"

have() { [ -s "$1" ]; }

fetch() { # url dest
  if have "$2"; then echo "skip  $(basename "$2") (present)"; return; fi
  echo "fetch $(basename "$2")"
  curl -fSL --retry 3 -o "$2" "$1"
}

unzip_to() { # url destdir sentinel
  if have "$2/$3"; then echo "skip  $(basename "$2") models (present)"; return; fi
  local tmp; tmp="$(mktemp)"
  echo "fetch $(basename "$1")"
  curl -fSL --retry 3 -o "$tmp" "$1"
  unzip -o -q "$tmp" -d "$2"
  rm -f "$tmp"
}

# YOLO default detector. detector.py resolves a bare model name against
# NURBY_MODELS_DIR (/app/models) before any network fallback.
fetch "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.pt" \
      "$DEST/yolov8n.pt"

# InsightFace buffalo_l pack. lands at ~/.insightface/models/buffalo_l in the image.
unzip_to "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip" \
         "$DEST/insightface/buffalo_l" "w600k_r50.onnx"

# EasyOCR detector (CRAFT) + english recognizer. land at ~/.EasyOCR/model.
unzip_to "https://github.com/JaidedAI/EasyOCR/releases/download/pre-v1.1.6/craft_mlt_25k.zip" \
         "$DEST/easyocr" "craft_mlt_25k.pth"
unzip_to "https://github.com/JaidedAI/EasyOCR/releases/download/v1.3/english_g2.zip" \
         "$DEST/easyocr" "english_g2.pth"

echo "done. models in $DEST"
du -sh "$DEST"
