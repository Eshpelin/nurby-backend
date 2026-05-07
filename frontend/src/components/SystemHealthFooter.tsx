"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

interface GPU {
  index: number;
  name: string;
  util_percent: number;
  mem_total_mb: number;
  mem_used_mb: number;
  temp_c: number;
}

interface Health {
  cpu_percent: number;
  cpu_count: number;
  load_avg: number[] | null;
  mem: {
    total_bytes: number;
    used_bytes: number;
    available_bytes: number;
    percent: number;
  };
  disk: {
    path: string;
    total_bytes: number;
    used_bytes: number;
    free_bytes: number;
    percent: number;
  };
  gpus: GPU[] | null;
}

const POLL_MS = 10000;

function fmtBytes(n: number): string {
  if (n >= 1e12) return `${(n / 1e12).toFixed(1)} TB`;
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)} KB`;
  return `${n} B`;
}

function tone(percent: number): string {
  if (percent >= 90) return "text-danger";
  if (percent >= 75) return "text-warning";
  return "text-muted-foreground";
}

/**
 * Compact host-resource readout pinned to the dashboard footer.
 * Polls /api/system/health on a coarse cadence so it never competes
 * with the live perception feed for bandwidth. Hover any pill for the
 * full breakdown.
 */
export function SystemHealthFooter() {
  const { token, authFetch } = useAuth();
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const res = await authFetch("/api/system/health");
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = await res.json();
        if (!cancelled) {
          setHealth(data);
          setError(false);
        }
      } catch {
        if (!cancelled) setError(true);
      } finally {
        if (!cancelled) timer = setTimeout(poll, POLL_MS);
      }
    };

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [token, authFetch]);

  if (!health) {
    return (
      <div className="flex items-center gap-2 text-[10px] text-muted-foreground/60 font-mono">
        <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40" />
        <span>{error ? "host stats unavailable" : "loading host stats"}</span>
      </div>
    );
  }

  const cpu = health.cpu_percent;
  const memP = health.mem.percent;
  const diskP = health.disk.percent;
  const load = health.load_avg
    ? health.load_avg.map((n) => n.toFixed(2)).join(" / ")
    : null;

  return (
    <div className="flex items-center gap-3 text-[10px] font-mono select-none">
      <Pill
        label="CPU"
        value={`${cpu.toFixed(0)}%`}
        toneClass={tone(cpu)}
        title={
          load
            ? `CPU ${cpu.toFixed(1)}% across ${health.cpu_count} cores · load ${load}`
            : `CPU ${cpu.toFixed(1)}% across ${health.cpu_count} cores`
        }
      />
      <Pill
        label="RAM"
        value={`${memP.toFixed(0)}%`}
        toneClass={tone(memP)}
        title={`${fmtBytes(health.mem.used_bytes)} / ${fmtBytes(health.mem.total_bytes)} used · ${fmtBytes(health.mem.available_bytes)} free`}
      />
      <Pill
        label="DISK"
        value={`${diskP.toFixed(0)}%`}
        toneClass={tone(diskP)}
        title={`${fmtBytes(health.disk.used_bytes)} / ${fmtBytes(health.disk.total_bytes)} used · ${fmtBytes(health.disk.free_bytes)} free on ${health.disk.path}`}
      />
      {health.gpus && health.gpus.length > 0 && (
        <>
          {health.gpus.map((g) => (
            <Pill
              key={g.index}
              label={`GPU${health.gpus!.length > 1 ? g.index : ""}`}
              value={`${g.util_percent.toFixed(0)}%`}
              toneClass={tone(g.util_percent)}
              title={`${g.name} · ${g.util_percent.toFixed(0)}% util · ${(g.mem_used_mb / 1024).toFixed(1)} / ${(g.mem_total_mb / 1024).toFixed(1)} GB VRAM · ${g.temp_c.toFixed(0)}°C`}
            />
          ))}
        </>
      )}
    </div>
  );
}

function Pill({
  label,
  value,
  toneClass,
  title,
}: {
  label: string;
  value: string;
  toneClass: string;
  title: string;
}) {
  return (
    <span className="flex items-center gap-1" title={title}>
      <span className="text-muted-foreground/70">{label}</span>
      <span className={toneClass}>{value}</span>
    </span>
  );
}
