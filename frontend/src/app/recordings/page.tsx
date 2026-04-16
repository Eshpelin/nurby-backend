"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/lib/auth";

interface Recording {
  id: string;
  camera_id: string;
  file_path: string;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  file_size_bytes: number | null;
  thumbnail_path: string | null;
}

interface Camera {
  id: string;
  name: string;
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "unknown";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function formatFileSize(bytes: number | null): string {
  if (bytes == null) return "unknown";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return `${d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

const PAGE_SIZE = 24;

export default function RecordingsPage() {
  const { authFetch } = useAuth();
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [cameraFilter, setCameraFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const cameraNames = useMemo(() => {
    const map: Record<string, string> = {};
    for (const c of cameras) map[c.id] = c.name;
    return map;
  }, [cameras]);

  const fetchCameras = useCallback(async () => {
    try {
      const res = await authFetch("/api/cameras");
      if (res.ok) setCameras(await res.json());
    } catch {
      /* silent */
    }
  }, []);

  const fetchRecordings = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set("limit", String(PAGE_SIZE));
      params.set("offset", String(page * PAGE_SIZE));
      if (cameraFilter) params.set("camera_id", cameraFilter);

      const res = await authFetch(`/api/recordings?${params.toString()}`);
      if (res.ok) {
        let list: Recording[] = await res.json();

        if (dateFrom) {
          const fromTs = new Date(dateFrom).getTime();
          list = list.filter(
            (r) => new Date(r.started_at).getTime() >= fromTs
          );
        }
        if (dateTo) {
          const toTs = new Date(dateTo).getTime() + 86400000;
          list = list.filter(
            (r) => new Date(r.started_at).getTime() < toTs
          );
        }

        setRecordings(list);
      }
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, [page, cameraFilter, dateFrom, dateTo]);

  useEffect(() => {
    fetchCameras();
  }, [fetchCameras]);

  useEffect(() => {
    fetchRecordings();
  }, [fetchRecordings]);

  const resetFiltersAndPage = () => {
    setCameraFilter("");
    setDateFrom("");
    setDateTo("");
    setPage(0);
  };

  const hasNextPage = recordings.length === PAGE_SIZE;
  const hasPrevPage = page > 0;

  return (
    <div className="px-6 py-6 max-w-6xl mx-auto">
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Recordings</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {recordings.length} recording{recordings.length !== 1 ? "s" : ""}{" "}
            on this page
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-6">
        <select
          value={cameraFilter}
          onChange={(e) => {
            setCameraFilter(e.target.value);
            setPage(0);
          }}
          className="px-3 py-2 text-sm rounded-md border border-border bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
        >
          <option value="">All cameras</option>
          {cameras.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>

        <div className="flex items-center gap-2">
          <label className="text-xs text-muted-foreground">From</label>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => {
              setDateFrom(e.target.value);
              setPage(0);
            }}
            className="px-3 py-2 text-sm rounded-md border border-border bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
          />
        </div>

        <div className="flex items-center gap-2">
          <label className="text-xs text-muted-foreground">To</label>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => {
              setDateTo(e.target.value);
              setPage(0);
            }}
            className="px-3 py-2 text-sm rounded-md border border-border bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
          />
        </div>

        {(cameraFilter || dateFrom || dateTo) && (
          <button
            onClick={resetFiltersAndPage}
            className="px-3 py-2 text-xs rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            Clear filters
          </button>
        )}
      </div>

      {loading ? (
        <div className="text-sm text-muted-foreground py-20 text-center">
          Loading recordings.
        </div>
      ) : recordings.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="w-16 h-16 rounded-full border border-border flex items-center justify-center mb-4 text-muted-foreground text-2xl">
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <rect x="2" y="4" width="20" height="16" rx="2" />
              <circle cx="12" cy="12" r="3" />
            </svg>
          </div>
          <p className="text-muted-foreground text-sm">
            No recordings match your filters. Try adjusting the camera or date
            range.
          </p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {recordings.map((rec) => {
              const isExpanded = expandedId === rec.id;
              return (
                <div
                  key={rec.id}
                  className={`rounded-lg border bg-card overflow-hidden transition-all ${isExpanded ? "border-accent col-span-1 sm:col-span-2 lg:col-span-3 xl:col-span-4" : "border-border hover:border-accent/50"}`}
                >
                  <div
                    className="cursor-pointer"
                    onClick={() =>
                      setExpandedId(isExpanded ? null : rec.id)
                    }
                  >
                    {rec.thumbnail_path ? (
                      <img
                        src={`/api/recordings/${rec.id}/thumbnail`}
                        alt="Recording thumbnail"
                        className="w-full h-36 object-cover bg-muted"
                        onError={(e) => {
                          (e.target as HTMLImageElement).style.display =
                            "none";
                        }}
                      />
                    ) : (
                      <div className="w-full h-36 bg-muted flex items-center justify-center">
                        <svg
                          width="32"
                          height="32"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="1.5"
                          className="text-muted-foreground"
                        >
                          <polygon points="5,3 19,12 5,21" />
                        </svg>
                      </div>
                    )}

                    <div className="p-3 space-y-1.5">
                      <div className="text-sm font-medium truncate">
                        {cameraNames[rec.camera_id] || "Unknown camera"}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {formatDateTime(rec.started_at)}
                      </div>
                      <div className="flex items-center gap-3 text-xs text-muted-foreground">
                        <span>{formatDuration(rec.duration_seconds)}</span>
                        <span>{formatFileSize(rec.file_size_bytes)}</span>
                      </div>
                    </div>
                  </div>

                  {isExpanded && (
                    <div className="border-t border-border p-4 space-y-3">
                      <video
                        controls
                        className="w-full max-h-[480px] rounded bg-black"
                        src={`/api/recordings/${rec.id}/stream`}
                      />
                      <div className="flex items-center gap-2">
                        <a
                          href={`/api/recordings/${rec.id}/download`}
                          download
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90 transition-opacity"
                        >
                          <svg
                            width="14"
                            height="14"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                          >
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                            <polyline points="7 10 12 15 17 10" />
                            <line x1="12" y1="15" x2="12" y2="3" />
                          </svg>
                          Download
                        </a>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setExpandedId(null);
                          }}
                          className="px-3 py-1.5 text-sm rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                        >
                          Close
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <div className="flex items-center justify-between mt-6">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={!hasPrevPage}
              className="px-3 py-1.5 text-sm rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              Previous
            </button>
            <span className="text-xs text-muted-foreground">
              Page {page + 1}
            </span>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={!hasNextPage}
              className="px-3 py-1.5 text-sm rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              Next
            </button>
          </div>
        </>
      )}
    </div>
  );
}
