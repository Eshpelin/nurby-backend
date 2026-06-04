"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";

interface Vehicle {
  id: string;
  display_name: string;
  nickname: string | null;
  license_plate: string | null;
  vehicle_type: string | null;
  make: string | null;
  model: string | null;
  color: string | null;
  description: string | null;
  description_status: string;
  is_starred: boolean;
  is_provisional: boolean;
  sighting_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
}

interface VehicleSummary {
  vehicle_id: string;
  display_name: string;
  license_plate: string | null;
  vehicle_type: string | null;
  color: string | null;
  make: string | null;
  model: string | null;
  description: string | null;
  is_starred: boolean;
  total_sightings: number;
  sightings_1h: number;
  sightings_24h: number;
  last_seen_at: string | null;
  last_seen_camera: string | null;
  first_seen_at: string | null;
}

interface VehicleActivity {
  observation_id: string;
  camera_id: string;
  camera_name: string | null;
  started_at: string;
  vlm_description: string | null;
  thumbnail_path: string | null;
  plate_text: string | null;
}

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const d = Date.now() - new Date(iso).getTime();
  const m = Math.floor(d / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

const TYPE_ICON: Record<string, string> = {
  car: "🚗", truck: "🚚", bus: "🚌", motorcycle: "🏍️", van: "🚐", forklift: "🚜",
};

export default function VehiclesPage() {
  const { authFetch, token } = useAuth();
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [summaries, setSummaries] = useState<Record<string, VehicleSummary>>({});
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [activity, setActivity] = useState<Record<string, VehicleActivity[]>>({});
  const [editing, setEditing] = useState<Vehicle | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [vRes, sRes] = await Promise.all([
        authFetch("/api/vehicles"),
        authFetch("/api/vehicles/activity/summary"),
      ]);
      if (vRes.ok) setVehicles(await vRes.json());
      if (sRes.ok) {
        const arr: VehicleSummary[] = await sRes.json();
        const map: Record<string, VehicleSummary> = {};
        for (const s of arr) map[s.vehicle_id] = s;
        setSummaries(map);
      }
    } catch {/* ignore */}
    finally { setLoading(false); }
  }, [authFetch]);

  useEffect(() => {
    fetchAll();
    const i = setInterval(fetchAll, 30000);
    return () => clearInterval(i);
  }, [fetchAll]);

  const toggle = useCallback(async (id: string) => {
    if (expanded === id) { setExpanded(null); return; }
    setExpanded(id);
    if (!activity[id]) {
      try {
        const r = await authFetch(`/api/vehicles/activity/${id}?limit=50`);
        if (r.ok) { const data = await r.json(); setActivity((p) => ({ ...p, [id]: data })); }
      } catch {/* ignore */}
    }
  }, [expanded, activity, authFetch]);

  const toggleStar = useCallback(async (v: Vehicle) => {
    setVehicles((prev) => prev.map((x) => x.id === v.id ? { ...x, is_starred: !x.is_starred } : x));
    try {
      await authFetch(`/api/vehicles/${v.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_starred: !v.is_starred }),
      });
    } catch { fetchAll(); }
  }, [authFetch, fetchAll]);

  const remove = useCallback(async (id: string) => {
    setVehicles((prev) => prev.filter((x) => x.id !== id));
    try { await authFetch(`/api/vehicles/${id}`, { method: "DELETE" }); } catch { fetchAll(); }
  }, [authFetch, fetchAll]);

  if (loading) {
    return <div className="px-6 py-8 text-sm text-muted-foreground">Loading vehicles.</div>;
  }

  if (vehicles.length === 0) {
    return (
      <div className="px-6 py-16 max-w-xl mx-auto text-center">
        <div className="text-4xl mb-3">🚗</div>
        <h1 className="text-xl font-semibold mb-2">No vehicles identified yet</h1>
        <p className="text-sm text-muted-foreground leading-relaxed">
          When a camera reads a license plate, the vehicle appears here with
          its plate, a description, and every time it entered or left. Plates
          are detected automatically. no setup needed.
        </p>
      </div>
    );
  }

  return (
    <div className="px-6 py-6 max-w-4xl mx-auto">
      <div className="mb-5">
        <h1 className="text-2xl font-semibold tracking-tight">Vehicles</h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Every vehicle seen, identified by plate, with full enter/leave history.
        </p>
      </div>

      <div className="space-y-2.5">
        {vehicles.map((v) => {
          const s = summaries[v.id];
          const isOpen = expanded === v.id;
          return (
            <div key={v.id} className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="flex items-center gap-3 p-3">
                <button onClick={() => toggle(v.id)} className="flex items-center gap-3 flex-1 min-w-0 text-left">
                  <div className="relative w-14 h-14 rounded-md bg-muted overflow-hidden flex-shrink-0 flex items-center justify-center">
                    <img
                      src={`/api/vehicles/${v.id}/photo${token ? `?token=${token}` : ""}`}
                      alt={v.display_name}
                      className="w-full h-full object-cover"
                      onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                    />
                    <span className="absolute text-xl pointer-events-none">{TYPE_ICON[v.vehicle_type || ""] || "🚗"}</span>
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm truncate">{v.nickname || v.display_name}</span>
                      {v.license_plate && (
                        <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-accent/15 text-accent border border-accent/30">
                          {v.license_plate}
                        </span>
                      )}
                      {v.is_provisional && (
                        <span className="text-[9px] uppercase tracking-wide text-muted-foreground">auto</span>
                      )}
                    </div>
                    {v.description && (
                      <div className="text-[11px] text-muted-foreground truncate mt-0.5">{v.description}</div>
                    )}
                    <div className="text-[11px] text-muted-foreground mt-0.5">
                      {s ? (
                        <>last seen {timeAgo(s.last_seen_at)}{s.last_seen_camera ? ` on ${s.last_seen_camera}` : ""}</>
                      ) : (
                        <>last seen {timeAgo(v.last_seen_at)}</>
                      )}
                    </div>
                  </div>
                </button>
                <div className="flex items-center gap-2 flex-shrink-0">
                  {s && (
                    <div className="hidden sm:flex flex-col items-end text-[10px] font-mono text-muted-foreground">
                      <span className="text-accent">{s.sightings_1h} / 1h</span>
                      <span>{s.total_sightings} total</span>
                    </div>
                  )}
                  <button onClick={() => toggleStar(v)} title="Star" className={`text-base ${v.is_starred ? "text-yellow-400" : "text-muted-foreground hover:text-foreground"}`}>
                    {v.is_starred ? "★" : "☆"}
                  </button>
                  <button onClick={() => setEditing(v)} title="Edit" className="text-muted-foreground hover:text-foreground text-xs">Edit</button>
                </div>
              </div>

              {isOpen && (
                <div className="border-t border-border bg-background/40 p-3">
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-2">
                    Sightings (newest first)
                  </div>
                  {!activity[v.id] ? (
                    <div className="text-[11px] text-muted-foreground">Loading sightings.</div>
                  ) : activity[v.id].length === 0 ? (
                    <div className="text-[11px] text-muted-foreground">No sightings in the recent window.</div>
                  ) : (
                    <div className="space-y-1.5">
                      {activity[v.id].map((a) => (
                        <Link
                          key={a.observation_id}
                          href={`/cameras/${a.camera_id}`}
                          className="flex items-center gap-2.5 rounded-md border border-border bg-card/50 p-1.5 hover:border-accent/50 transition-colors"
                        >
                          <div className="w-16 h-10 rounded bg-muted overflow-hidden flex-shrink-0">
                            {a.thumbnail_path && (
                              <img src={`/api/observations/${a.observation_id}/thumbnail${token ? `?token=${token}` : ""}`} alt="" className="w-full h-full object-cover" />
                            )}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="text-[11px] truncate">{a.vlm_description || "Vehicle seen"}</div>
                            <div className="text-[10px] text-muted-foreground">
                              {a.camera_name || "camera"} · {timeAgo(a.started_at)}
                              {a.plate_text ? ` · plate ${a.plate_text}` : ""}
                            </div>
                          </div>
                        </Link>
                      ))}
                    </div>
                  )}
                  <button onClick={() => remove(v.id)} className="mt-3 text-[11px] text-red-400 hover:text-red-300">
                    Delete this vehicle
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {editing && (
        <EditModal vehicle={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); fetchAll(); }} />
      )}
    </div>
  );
}

function EditModal({ vehicle, onClose, onSaved }: { vehicle: Vehicle; onClose: () => void; onSaved: () => void }) {
  const { authFetch } = useAuth();
  const [name, setName] = useState(vehicle.display_name);
  const [plate, setPlate] = useState(vehicle.license_plate || "");
  const [make, setMake] = useState(vehicle.make || "");
  const [model, setModel] = useState(vehicle.model || "");
  const [color, setColor] = useState(vehicle.color || "");
  const [saving, setSaving] = useState(false);
  const ref = useRef<HTMLInputElement | null>(null);
  useEffect(() => { ref.current?.focus(); }, []);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await authFetch(`/api/vehicles/${vehicle.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: name.trim() || vehicle.display_name,
          license_plate: plate.trim() || null,
          make: make.trim() || null,
          model: model.trim() || null,
          color: color.trim() || null,
        }),
      });
      onSaved();
    } finally { setSaving(false); }
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={onClose}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={save} className="w-full max-w-sm rounded-lg border border-border bg-card-elevated p-5 space-y-3">
        <h2 className="text-sm font-semibold">Edit vehicle</h2>
        {[
          ["Name", name, setName, ref] as const,
          ["License plate", plate, setPlate, undefined] as const,
          ["Make", make, setMake, undefined] as const,
          ["Model", model, setModel, undefined] as const,
          ["Color", color, setColor, undefined] as const,
        ].map(([label, val, set, r]) => (
          <div key={label}>
            <label className="block text-[11px] text-muted-foreground mb-1">{label}</label>
            <input
              ref={r as React.RefObject<HTMLInputElement> | undefined}
              value={val}
              onChange={(e) => set(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-md border border-border bg-background focus:border-accent/60 focus:outline-none"
            />
          </div>
        ))}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="px-3 py-1.5 text-xs rounded-md border border-border text-muted-foreground hover:text-foreground">Cancel</button>
          <button type="submit" disabled={saving} className="px-4 py-1.5 text-xs font-medium rounded-md bg-accent text-accent-foreground hover:opacity-90 disabled:opacity-50">
            {saving ? "Saving." : "Save"}
          </button>
        </div>
      </form>
    </div>
  );
}
