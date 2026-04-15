#!/usr/bin/env bash
# Generates a fake RTSP camera stream using FFmpeg test sources.
# Publishes to MediaMTX at rtsp://localhost:8554/cam-test
#
# Usage: ./dev/fake-camera.sh [rtsp-url]

set -e

RTSP_URL="${1:-rtsp://localhost:8554/cam-test}"

echo "Publishing fake camera stream to $RTSP_URL"
echo "Press Ctrl+C to stop."

ffmpeg -hide_banner -loglevel warning \
  -re \
  -f lavfi -i "testsrc2=size=1280x720:rate=15" \
  -c:v libx264 -preset ultrafast -tune zerolatency -g 30 \
  -pix_fmt yuv420p \
  -f rtsp -rtsp_transport tcp \
  "$RTSP_URL"
