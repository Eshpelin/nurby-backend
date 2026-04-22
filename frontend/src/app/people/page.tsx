"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

interface Person {
  id: string;
  display_name: string;
  relationship: string | null;
  consent_given: boolean;
  privacy_blur?: boolean;
  is_starred?: boolean;
  recap_prompt?: string | null;
  photo_path: string | null;
  created_at: string;
}

interface PersonSummary {
  person_id: string;
  display_name: string;
  relationship: string | null;
  photo_path: string | null;
  total_sightings: number;
  sightings_1h: number;
  sightings_24h: number;
  last_seen_at: string | null;
  last_seen_camera: string | null;
  first_seen_at: string | null;
}

interface PersonActivity {
  observation_id: string;
  camera_id: string;
  camera_name: string | null;
  started_at: string;
  ended_at: string | null;
  vlm_description: string | null;
  thumbnail_path: string | null;
  person_name: string | null;
  match_distance: number | null;
  object_detections: Record<string, unknown> | null;
}

interface FaceSuggestion {
  id: string;
  sample_thumbnail_path: string | null;
  sighting_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  first_camera_id: string | null;
  status: string;
}

interface ClusterSample {
  id: string;
  camera_id: string;
  thumbnail_path: string | null;
  captured_at: string | null;
}

function timeAgo(iso: string | null): string {
  if (!iso) return "unknown";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);

  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

export default function PeoplePage() {
  const { authFetch } = useAuth();
  const [persons, setPersons] = useState<Person[]>([]);
  const [summaries, setSummaries] = useState<PersonSummary[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [editPerson, setEditPerson] = useState<Person | null>(null);
  const [loading, setLoading] = useState(true);

  // Expanded person activity
  const [expandedPerson, setExpandedPerson] = useState<string | null>(null);
  const [activities, setActivities] = useState<PersonActivity[]>([]);
  const [loadingActivity, setLoadingActivity] = useState(false);

  // Edit form state
  const [formName, setFormName] = useState("");
  const [formRelationship, setFormRelationship] = useState("");
  const [formConsent, setFormConsent] = useState(false);
  const [formStarred, setFormStarred] = useState(false);
  const [formRecapPrompt, setFormRecapPrompt] = useState("");
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [togglingStar, setTogglingStar] = useState<string | null>(null);

  // Suggestions state
  const [suggestions, setSuggestions] = useState<FaceSuggestion[]>([]);
  const [clusterSamples, setClusterSamples] = useState<Record<string, ClusterSample[]>>({});
  const [nameInputs, setNameInputs] = useState<Record<string, string>>({});
  const [relationshipInputs, setRelationshipInputs] = useState<
    Record<string, string>
  >({});
  const [namingSubmitting, setNamingSubmitting] = useState<string | null>(null);

  const fetchPersons = useCallback(async () => {
    try {
      const res = await authFetch("/api/persons");
      if (res.ok) setPersons(await res.json());
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchSummaries = useCallback(async () => {
    try {
      const res = await authFetch("/api/persons/activity/summary");
      if (res.ok) setSummaries(await res.json());
    } catch {
      /* silent */
    }
  }, []);

  const fetchSuggestions = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await authFetch("/api/persons/suggestions?min_sightings=2");
      if (!res.ok || signal?.aborted) return;
      const data: FaceSuggestion[] = await res.json();
      if (signal?.aborted) return;
      setSuggestions(data);
      // Fetch samples for each cluster
      const samplesMap: Record<string, ClusterSample[]> = {};
      await Promise.all(data.map(async (s) => {
        if (signal?.aborted) return;
        try {
          const sRes = await authFetch(`/api/persons/suggestions/${s.id}/samples`);
          if (sRes.ok && !signal?.aborted) samplesMap[s.id] = await sRes.json();
        } catch { /* silent */ }
      }));
      if (!signal?.aborted) setClusterSamples(samplesMap);
    } catch {
      /* silent */
    }
  }, [authFetch]);

  const fetchActivity = useCallback(async (personId: string) => {
    setLoadingActivity(true);
    try {
      const res = await authFetch(
        `/api/persons/activity/${personId}?limit=50`
      );
      if (res.ok) setActivities(await res.json());
    } catch {
      /* silent */
    } finally {
      setLoadingActivity(false);
    }
  }, [authFetch]);

  useEffect(() => {
    const controller = new AbortController();
    fetchPersons();
    fetchSummaries();
    fetchSuggestions(controller.signal);
    const interval = setInterval(() => {
      fetchSummaries();
    }, 30000);
    return () => {
      controller.abort();
      clearInterval(interval);
    };
  }, [fetchPersons, fetchSummaries, fetchSuggestions]);

  const toggleExpand = (personId: string) => {
    if (expandedPerson === personId) {
      setExpandedPerson(null);
      setActivities([]);
    } else {
      setExpandedPerson(personId);
      fetchActivity(personId);
    }
  };

  const openEdit = (p: Person) => {
    setEditPerson(p);
    setFormName(p.display_name);
    setFormRelationship(p.relationship || "");
    setFormConsent(p.consent_given);
    setFormStarred(!!p.is_starred);
    setFormRecapPrompt(p.recap_prompt || "");
    setFormError("");
    setShowModal(true);
  };

  const toggleStar = useCallback(async (p: Person) => {
    setTogglingStar(p.id);
    const next = !p.is_starred;
    setPersons((prev) => prev.map((x) => (x.id === p.id ? { ...x, is_starred: next } : x)));
    try {
      const res = await authFetch(`/api/persons/${p.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_starred: next }),
      });
      if (!res.ok) {
        setPersons((prev) => prev.map((x) => (x.id === p.id ? { ...x, is_starred: !next } : x)));
      }
    } catch {
      setPersons((prev) => prev.map((x) => (x.id === p.id ? { ...x, is_starred: !next } : x)));
    } finally {
      setTogglingStar(null);
    }
  }, [authFetch]);

  const handleSubmit = async () => {
    if (!editPerson || !formName.trim()) {
      setFormError("Name is required");
      return;
    }
    setSubmitting(true);
    setFormError("");

    try {
      const res = await authFetch(`/api/persons/${editPerson.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: formName.trim(),
          relationship: formRelationship.trim() || null,
          consent_given: formConsent,
          is_starred: formStarred,
          recap_prompt: formRecapPrompt.trim() || null,
        }),
      });

      if (!res.ok) {
        setFormError("Failed to save");
        return;
      }

      setShowModal(false);
      fetchPersons();
      fetchSummaries();
    } catch {
      setFormError("Network error");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await authFetch(`/api/persons/${id}`, { method: "DELETE" });
      fetchPersons();
      fetchSummaries();
    } catch {
      /* silent */
    }
  };

  const handleNameSuggestion = async (clusterId: string) => {
    const name = nameInputs[clusterId]?.trim();
    if (!name) return;

    setNamingSubmitting(clusterId);
    try {
      const res = await authFetch(`/api/persons/suggestions/${clusterId}/name`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: name,
          relationship: relationshipInputs[clusterId]?.trim() || null,
        }),
      });
      if (res.ok) {
        fetchSuggestions();
        fetchPersons();
        fetchSummaries();
      }
    } catch {
      /* silent */
    } finally {
      setNamingSubmitting(null);
    }
  };

  const handleIgnoreSuggestion = async (clusterId: string) => {
    try {
      await authFetch(`/api/persons/suggestions/${clusterId}/ignore`, {
        method: "POST",
      });
      setSuggestions((prev) => prev.filter((s) => s.id !== clusterId));
    } catch {
      /* silent */
    }
  };

  // Group activities by date
  const groupedActivities: Record<string, PersonActivity[]> = {};
  for (const a of activities) {
    const dateKey = formatDate(a.started_at);
    if (!groupedActivities[dateKey]) groupedActivities[dateKey] = [];
    groupedActivities[dateKey].push(a);
  }

  // Build summary map for quick lookup
  const summaryMap: Record<string, PersonSummary> = {};
  for (const s of summaries) {
    summaryMap[s.person_id] = s;
  }

  return (
    <div className="px-6 py-6 max-w-5xl mx-auto">
      {/* Suggestions section */}
      {suggestions.length > 0 && (
        <div className="mb-10">
          <div className="mb-4">
            <h2 className="text-lg font-semibold">Who are these people?</h2>
            <p className="text-sm text-muted-foreground mt-1">
              {suggestions.length} unknown{" "}
              {suggestions.length === 1 ? "person" : "people"} discovered from
              your camera feeds
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {suggestions.map((s) => (
              <div
                key={s.id}
                className="rounded-lg border border-accent/30 bg-card p-4 space-y-3"
              >
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <div>
                      <div className="text-sm font-medium">Unknown person</div>
                      <div className="text-xs text-muted-foreground mt-0.5">
                        Seen {s.sighting_count} time
                        {s.sighting_count !== 1 ? "s" : ""}
                        {" · "}First {timeAgo(s.first_seen_at)} / Last{" "}
                        {timeAgo(s.last_seen_at)}
                      </div>
                    </div>
                  </div>
                  <div className="grid grid-cols-4 gap-1.5">
                    {(clusterSamples[s.id] && clusterSamples[s.id].length > 0
                      ? clusterSamples[s.id]
                      : [{ id: "main", camera_id: "", thumbnail_path: s.sample_thumbnail_path, captured_at: null }]
                    ).slice(0, 8).map((sample) => (
                      <div key={sample.id} className="aspect-square rounded-md overflow-hidden border border-border bg-muted">
                        {sample.thumbnail_path ? (
                          <img
                            src={sample.id === "main"
                              ? `/api/persons/suggestions/${s.id}/thumbnail`
                              : `/api/persons/suggestions/${s.id}/samples/${sample.id}/thumbnail`}
                            alt="Sighting"
                            className="w-full h-full object-cover"
                            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                          />
                        ) : (
                          <div className="w-full h-full bg-muted" />
                        )}
                      </div>
                    ))}
                    {/* Fill remaining slots up to sighting count as placeholder boxes */}
                    {(() => {
                      const sampleCount = clusterSamples[s.id]?.length || (s.sample_thumbnail_path ? 1 : 0);
                      const remaining = Math.min(s.sighting_count, 8) - Math.min(sampleCount, 8);
                      if (remaining <= 0) return null;
                      return Array.from({ length: remaining }).map((_, i) => (
                        <div key={`empty-${i}`} className="aspect-square rounded-md border border-border bg-muted/50 flex items-center justify-center">
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-muted-foreground/40">
                            <circle cx="12" cy="8" r="4" /><path d="M5 20c0-4 3.5-7 7-7s7 3 7 7" />
                          </svg>
                        </div>
                      ));
                    })()}
                    {s.sighting_count > 8 && (
                      <div className="aspect-square rounded-md border border-border bg-muted/30 flex items-center justify-center">
                        <span className="text-[10px] text-muted-foreground font-mono">+{s.sighting_count - 8}</span>
                      </div>
                    )}
                  </div>
                </div>

                <div className="space-y-2">
                  <input
                    type="text"
                    value={nameInputs[s.id] || ""}
                    onChange={(e) =>
                      setNameInputs((prev) => ({
                        ...prev,
                        [s.id]: e.target.value,
                      }))
                    }
                    placeholder="Who is this?"
                    className="w-full px-3 py-2 text-sm rounded-md border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleNameSuggestion(s.id);
                    }}
                  />
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      value={relationshipInputs[s.id] || ""}
                      onChange={(e) =>
                        setRelationshipInputs((prev) => ({
                          ...prev,
                          [s.id]: e.target.value,
                        }))
                      }
                      placeholder="Relationship (optional)"
                      className="flex-1 px-3 py-1.5 text-xs rounded-md border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent"
                    />
                    <button
                      onClick={() => handleNameSuggestion(s.id)}
                      disabled={
                        !nameInputs[s.id]?.trim() ||
                        namingSubmitting === s.id
                      }
                      className="px-3 py-1.5 text-xs rounded-md bg-accent text-accent-foreground font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
                    >
                      {namingSubmitting === s.id ? "Saving" : "Name"}
                    </button>
                  </div>
                  <button
                    onClick={() => handleIgnoreSuggestion(s.id)}
                    className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                  >
                    Not a person / Ignore
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* People activity feed */}
      <div>
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight">People</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Activity updates across all cameras
          </p>
        </div>

        {loading ? (
          <div className="text-sm text-muted-foreground py-20 text-center">
            Loading.
          </div>
        ) : persons.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <div className="w-16 h-16 rounded-full border border-border flex items-center justify-center mb-4 text-muted-foreground text-2xl">
              ?
            </div>
            <p className="text-muted-foreground text-sm">
              No people identified yet. When cameras detect faces, they will appear here for you to name.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {persons.map((p) => {
              const summary = summaryMap[p.id];
              const isExpanded = expandedPerson === p.id;

              return (
                <div
                  key={p.id}
                  className="rounded-lg border border-border bg-card overflow-hidden"
                >
                  {/* Person row */}
                  <div
                    className="flex items-center gap-4 px-4 py-3 cursor-pointer hover:bg-muted/30 transition-colors"
                    onClick={() => toggleExpand(p.id)}
                  >
                    {/* Avatar */}
                    {p.photo_path ? (
                      <img
                        src={`/api/persons/${p.id}/photo`}
                        alt={p.display_name}
                        className="w-11 h-11 rounded-full object-cover border border-border flex-shrink-0"
                      />
                    ) : (
                      <div className="w-11 h-11 rounded-full bg-muted flex items-center justify-center text-base font-medium flex-shrink-0">
                        {p.display_name.charAt(0).toUpperCase()}
                      </div>
                    )}

                    {/* Name and relationship */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium truncate">
                          {p.display_name}
                        </span>
                        {p.relationship && (
                          <span className="text-xs text-muted-foreground px-1.5 py-0.5 rounded bg-muted">
                            {p.relationship}
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-muted-foreground mt-0.5">
                        {summary?.last_seen_at
                          ? `Last seen ${timeAgo(summary.last_seen_at)}${summary.last_seen_camera ? ` at ${summary.last_seen_camera}` : ""}`
                          : "No sightings yet"}
                      </div>
                    </div>

                    {/* Activity counters */}
                    <div className="flex items-center gap-3 flex-shrink-0">
                      <button
                        onClick={(e) => { e.stopPropagation(); toggleStar(p); }}
                        disabled={togglingStar === p.id}
                        title={p.is_starred ? "Unpin from dashboard" : "Pin to dashboard"}
                        aria-label={p.is_starred ? "Unpin from dashboard" : "Pin to dashboard"}
                        className={`p-1 rounded transition-colors disabled:opacity-50 ${p.is_starred ? "text-amber-400 hover:bg-amber-500/10" : "text-muted-foreground hover:text-amber-400 hover:bg-muted"}`}
                      >
                        <svg width="16" height="16" viewBox="0 0 24 24" fill={p.is_starred ? "currentColor" : "none"} stroke="currentColor" strokeWidth="2">
                          <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
                        </svg>
                      </button>
                      {summary && summary.sightings_1h > 0 && (
                        <div className="flex items-center gap-1">
                          <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                          <span className="text-xs text-green-400">
                            {summary.sightings_1h} past hour
                          </span>
                        </div>
                      )}
                      {summary && summary.sightings_24h > 0 && (
                        <div className="flex items-center gap-1">
                          <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
                          <span className="text-xs text-blue-400">
                            {summary.sightings_24h} today
                          </span>
                        </div>
                      )}
                      {summary && summary.total_sightings > 0 && (
                        <div className="text-xs text-muted-foreground">
                          {summary.total_sightings} total
                        </div>
                      )}

                      {/* Expand arrow */}
                      <svg
                        width="14"
                        height="14"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        className={`text-muted-foreground transition-transform ${isExpanded ? "rotate-180" : ""}`}
                      >
                        <path d="M6 9l6 6 6-6" />
                      </svg>
                    </div>
                  </div>

                  {/* Expanded activity feed */}
                  {isExpanded && (
                    <div className="border-t border-border">
                      {/* Action bar */}
                      <div className="px-4 py-2 flex items-center gap-2 border-b border-border/50 bg-muted/20">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            openEdit(p);
                          }}
                          className="px-2 py-1 text-xs rounded border border-border hover:bg-muted transition-colors"
                        >
                          Edit
                        </button>
                        <div className="flex-1" />
                        <span
                          className={`w-2 h-2 rounded-full ${p.consent_given ? "bg-green-500" : "bg-yellow-500"}`}
                        />
                        <span className="text-[11px] text-muted-foreground">
                          {p.consent_given ? "Consent given" : "No consent"}
                        </span>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDelete(p.id);
                          }}
                          className="px-2 py-1 text-xs rounded border border-red-800 text-red-400 hover:bg-red-900/30 transition-colors ml-2"
                        >
                          Delete
                        </button>
                      </div>

                      {/* Activity timeline */}
                      <div className="max-h-96 overflow-y-auto">
                        {loadingActivity ? (
                          <div className="text-xs text-muted-foreground text-center py-8">
                            Loading activity.
                          </div>
                        ) : activities.length === 0 ? (
                          <div className="text-xs text-muted-foreground text-center py-8">
                            No activity recorded for this person yet.
                          </div>
                        ) : (
                          <div className="divide-y divide-border/50">
                            {Object.entries(groupedActivities).map(
                              ([dateLabel, items]) => (
                                <div key={dateLabel}>
                                  <div className="px-4 py-1.5 bg-muted/30 text-[11px] font-medium text-muted-foreground sticky top-0">
                                    {dateLabel}
                                  </div>
                                  {items.map((a) => (
                                    <div
                                      key={a.observation_id}
                                      className="px-4 py-2.5 flex items-start gap-3 hover:bg-muted/20 transition-colors"
                                    >
                                      {/* Thumbnail */}
                                      {a.thumbnail_path ? (
                                        <img
                                          src={`/api/observations/${a.observation_id}/thumbnail`}
                                          alt=""
                                          className="w-14 h-10 rounded object-cover border border-border flex-shrink-0"
                                          onError={(e) => {
                                            (
                                              e.target as HTMLImageElement
                                            ).style.display = "none";
                                          }}
                                        />
                                      ) : (
                                        <div className="w-14 h-10 rounded bg-muted flex-shrink-0" />
                                      )}

                                      {/* Event details */}
                                      <div className="flex-1 min-w-0">
                                        <div className="text-sm leading-snug">
                                          {a.vlm_description ||
                                            "Person detected"}
                                        </div>
                                        <div className="flex items-center gap-2 mt-1">
                                          <span className="text-[11px] text-muted-foreground">
                                            {formatTime(a.started_at)}
                                          </span>
                                          {a.camera_name && (
                                            <span className="text-[11px] text-muted-foreground px-1.5 py-0.5 rounded bg-muted">
                                              {a.camera_name}
                                            </span>
                                          )}
                                          {a.ended_at && (
                                            <span className="text-[11px] text-muted-foreground">
                                              until{" "}
                                              {formatTime(a.ended_at)}
                                            </span>
                                          )}
                                        </div>
                                      </div>

                                      {/* Match confidence */}
                                      {a.match_distance != null && (
                                        <div className="flex-shrink-0 text-[10px] text-muted-foreground font-mono">
                                          {(
                                            (1 - a.match_distance) *
                                            100
                                          ).toFixed(0)}
                                          % match
                                        </div>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              )
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Add/Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60"
            onClick={() => setShowModal(false)}
          />
          <div className="relative bg-card border border-border rounded-lg p-6 w-full max-w-md shadow-xl">
            <h2 className="text-lg font-semibold mb-4">Edit person</h2>

            <div className="space-y-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">
                  Name
                </label>
                <input
                  type="text"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm focus:outline-none focus:border-accent"
                  placeholder="Display name"
                  autoFocus
                />
              </div>

              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">
                  Relationship
                </label>
                <input
                  type="text"
                  value={formRelationship}
                  onChange={(e) => setFormRelationship(e.target.value)}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm focus:outline-none focus:border-accent"
                  placeholder="Family, friend, delivery, etc."
                />
              </div>

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={formConsent}
                  onChange={(e) => setFormConsent(e.target.checked)}
                  className="accent-green-500"
                />
                <span className="text-sm">
                  Consent given for face recognition
                </span>
              </label>

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={formStarred}
                  onChange={(e) => setFormStarred(e.target.checked)}
                  className="accent-amber-500"
                />
                <span className="text-sm">Pin to dashboard status row</span>
              </label>

              <div>
                <label className="block text-xs text-muted-foreground mb-1">
                  Recap prompt
                </label>
                <textarea
                  rows={3}
                  value={formRecapPrompt}
                  onChange={(e) => setFormRecapPrompt(e.target.value)}
                  placeholder="What do you care about for this person? Example. Is the baby still asleep. Any crying. Did grandma take her meds. Is the dog walker on time."
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm focus:outline-none focus:border-accent resize-y"
                />
                <p className="text-[11px] text-muted-foreground mt-1">
                  The dashboard recap will bias toward whatever you put here. Leave blank for a neutral status.
                </p>
              </div>

              {formError && (
                <div className="text-xs text-red-400">{formError}</div>
              )}
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button
                onClick={() => setShowModal(false)}
                className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSubmit}
                disabled={submitting}
                className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90 disabled:opacity-50"
              >
                {submitting ? "Saving." : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
