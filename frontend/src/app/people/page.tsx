"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface Person {
  id: string;
  display_name: string;
  relationship: string | null;
  consent_given: boolean;
  photo_path: string | null;
  created_at: string;
}

export default function PeoplePage() {
  const [persons, setPersons] = useState<Person[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [editPerson, setEditPerson] = useState<Person | null>(null);
  const [loading, setLoading] = useState(true);

  // Add/edit form state
  const [formName, setFormName] = useState("");
  const [formRelationship, setFormRelationship] = useState("");
  const [formConsent, setFormConsent] = useState(false);
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Face upload
  const [uploadingFace, setUploadingFace] = useState<string | null>(null);
  const [faceMessage, setFaceMessage] = useState<Record<string, string>>({});
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchPersons = useCallback(async () => {
    try {
      const res = await fetch("/api/persons");
      if (res.ok) setPersons(await res.json());
    } catch {
      /* silently fail */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPersons();
  }, [fetchPersons]);

  const openAdd = () => {
    setEditPerson(null);
    setFormName("");
    setFormRelationship("");
    setFormConsent(false);
    setFormError("");
    setShowModal(true);
  };

  const openEdit = (p: Person) => {
    setEditPerson(p);
    setFormName(p.display_name);
    setFormRelationship(p.relationship || "");
    setFormConsent(p.consent_given);
    setFormError("");
    setShowModal(true);
  };

  const handleSubmit = async () => {
    if (!formName.trim()) {
      setFormError("Name is required");
      return;
    }
    setSubmitting(true);
    setFormError("");

    try {
      const body = {
        display_name: formName.trim(),
        relationship: formRelationship.trim() || null,
        consent_given: formConsent,
      };

      let res: Response;
      if (editPerson) {
        res = await fetch(`/api/persons/${editPerson.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } else {
        res = await fetch("/api/persons", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }

      if (!res.ok) {
        setFormError("Failed to save");
        return;
      }

      setShowModal(false);
      fetchPersons();
    } catch {
      setFormError("Network error");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await fetch(`/api/persons/${id}`, { method: "DELETE" });
      fetchPersons();
    } catch {
      /* silently fail */
    }
  };

  const handleFaceUpload = async (personId: string, file: File) => {
    setUploadingFace(personId);
    setFaceMessage({});

    try {
      const formData = new FormData();
      formData.append("file", file);

      const res = await fetch(`/api/persons/${personId}/face`, {
        method: "POST",
        body: formData,
      });

      const data = await res.json();
      setFaceMessage({ [personId]: data.message || data.status });
      fetchPersons();
    } catch {
      setFaceMessage({ [personId]: "Upload failed" });
    } finally {
      setUploadingFace(null);
    }
  };

  return (
    <div className="px-6 py-6">
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">People</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {persons.length} person{persons.length !== 1 ? "s" : ""} registered
          </p>
        </div>
        <button
          onClick={openAdd}
          className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90"
        >
          + Add person
        </button>
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
          <p className="text-muted-foreground text-sm mb-4">
            No people registered yet. Add people and upload face photos
            to enable face recognition on camera feeds.
          </p>
          <button
            onClick={openAdd}
            className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90"
          >
            + Add first person
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {persons.map((p) => (
            <div
              key={p.id}
              className="rounded-lg border border-border bg-card p-4 space-y-3"
            >
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  {p.photo_path ? (
                    <img
                      src={`/api/persons/${p.id}/photo`}
                      alt={p.display_name}
                      className="w-12 h-12 rounded-full object-cover border border-border"
                    />
                  ) : (
                    <div className="w-12 h-12 rounded-full bg-muted flex items-center justify-center text-lg font-medium">
                      {p.display_name.charAt(0).toUpperCase()}
                    </div>
                  )}
                  <div>
                    <div className="font-medium">{p.display_name}</div>
                    {p.relationship && (
                      <div className="text-xs text-muted-foreground">
                        {p.relationship}
                      </div>
                    )}
                  </div>
                </div>
                <div className="flex gap-1">
                  <button
                    onClick={() => openEdit(p)}
                    className="px-2 py-1 text-xs rounded border border-border hover:bg-muted transition-colors"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => handleDelete(p.id)}
                    className="px-2 py-1 text-xs rounded border border-red-800 text-red-400 hover:bg-red-900/30 transition-colors"
                  >
                    Del
                  </button>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <span
                  className={`w-2 h-2 rounded-full ${
                    p.consent_given ? "bg-green-500" : "bg-yellow-500"
                  }`}
                />
                <span className="text-xs text-muted-foreground">
                  {p.consent_given
                    ? "Consent given for face recognition"
                    : "No consent for face recognition"}
                </span>
              </div>

              <div className="flex items-center gap-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) handleFaceUpload(p.id, file);
                    e.target.value = "";
                  }}
                />
                <button
                  onClick={() => {
                    setUploadingFace(p.id);
                    // Trigger file input
                    const input = document.createElement("input");
                    input.type = "file";
                    input.accept = "image/*";
                    input.onchange = (e) => {
                      const file = (e.target as HTMLInputElement).files?.[0];
                      if (file) handleFaceUpload(p.id, file);
                    };
                    input.click();
                  }}
                  disabled={uploadingFace === p.id}
                  className="px-2 py-1 text-xs rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
                >
                  {uploadingFace === p.id
                    ? "Uploading."
                    : p.photo_path
                    ? "Update face photo"
                    : "Upload face photo"}
                </button>

                {faceMessage[p.id] && (
                  <span className="text-xs text-muted-foreground">
                    {faceMessage[p.id]}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Add/Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60"
            onClick={() => setShowModal(false)}
          />
          <div className="relative bg-card border border-border rounded-lg p-6 w-full max-w-md shadow-xl">
            <h2 className="text-lg font-semibold mb-4">
              {editPerson ? "Edit person" : "Add person"}
            </h2>

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
                {submitting ? "Saving." : editPerson ? "Save" : "Add"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
