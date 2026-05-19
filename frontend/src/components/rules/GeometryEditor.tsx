"use client";

import { useEffect, useRef, useState } from "react";
import { extractStreamName, WEBRTC_URL, type Camera } from "./types";

export function GeometryEditor({
  camera,
  mode,
  points,
  onChange,
}: {
  camera: Camera;
  mode: "line" | "polygon";
  points: number[][];
  onChange: (pts: number[][]) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 640, h: 360 });
  const frameW = camera.width || 1280;
  const frameH = camera.height || 720;

  useEffect(() => {
    const update = () => {
      const w = wrapRef.current?.clientWidth || 640;
      const h = Math.round((w * frameH) / frameW);
      setSize({ w, h });
    };
    update();
    const ro = new ResizeObserver(update);
    if (wrapRef.current) ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, [frameW, frameH]);

  const scaleX = size.w / frameW;
  const scaleY = size.h / frameH;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, size.w, size.h);
    if (points.length === 0) return;

    const stroke = mode === "line" ? "#818cf8" : "#fbbf24";
    const fill = mode === "line" ? "rgba(129,140,248,0.15)" : "rgba(251,191,36,0.18)";

    ctx.beginPath();
    ctx.moveTo(points[0][0] * scaleX, points[0][1] * scaleY);
    for (let i = 1; i < points.length; i++) {
      ctx.lineTo(points[i][0] * scaleX, points[i][1] * scaleY);
    }
    if (mode === "polygon" && points.length >= 3) {
      ctx.closePath();
      ctx.fillStyle = fill;
      ctx.fill();
    }
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 2.5;
    ctx.stroke();

    for (const p of points) {
      ctx.beginPath();
      ctx.arc(p[0] * scaleX, p[1] * scaleY, 5, 0, Math.PI * 2);
      ctx.fillStyle = stroke;
      ctx.fill();
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }, [points, size, scaleX, scaleY, mode]);

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const x = Math.round((e.clientX - rect.left) / scaleX);
    const y = Math.round((e.clientY - rect.top) / scaleY);
    if (mode === "line") {
      if (points.length >= 2) {
        onChange([[x, y]]);
      } else {
        onChange([...points, [x, y]]);
      }
    } else {
      onChange([...points, [x, y]]);
    }
  };

  const streamName = camera.stream_url ? extractStreamName(camera.stream_url) : "";
  const iframeSrc = streamName ? `${WEBRTC_URL}/${streamName}/` : "";

  return (
    <div className="space-y-2">
      <div ref={wrapRef} className="relative w-full bg-black rounded-md overflow-hidden border border-border" style={{ height: size.h }}>
        {iframeSrc && camera.status !== "offline" ? (
          <iframe
            src={iframeSrc}
            className="absolute inset-0 w-full h-full border-0 pointer-events-none"
            allow="autoplay; encrypted-media"
            sandbox="allow-scripts allow-same-origin"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">
            {camera.status === "offline" ? "Camera offline" : "No preview"}
          </div>
        )}
        <canvas
          ref={canvasRef}
          width={size.w}
          height={size.h}
          onClick={handleClick}
          className="absolute inset-0 w-full h-full cursor-crosshair"
        />
      </div>
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>
          {mode === "line"
            ? points.length < 2
              ? `Click two points to place a tripwire. (${points.length}/2)`
              : "Tripwire placed. Click again to redraw."
            : points.length < 3
              ? `Click to add polygon points. (${points.length}/≥3)`
              : `${points.length} points. Add more or clear to redraw.`}
        </span>
        {points.length > 0 && (
          <button
            type="button"
            onClick={() => onChange([])}
            className="px-2 py-0.5 rounded border border-border hover:bg-muted transition-colors"
          >Clear</button>
        )}
      </div>
    </div>
  );
}
