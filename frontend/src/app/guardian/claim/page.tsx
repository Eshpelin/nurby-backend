"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

function ClaimForm() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token") || "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const valid = token && password.length >= 8 && password === confirm;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!token) {
      setError("This link is missing its token. Ask the facility to resend the invite.");
      return;
    }
    if (password.length < 8) {
      setError("Use at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/guardian/claim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password, display_name: name || undefined }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        setError(data?.detail || "This invite link is invalid or has expired.");
        setBusy(false);
        return;
      }
      localStorage.setItem("nurby_token", data.token);
      localStorage.setItem("nurby_user", JSON.stringify(data.user));
      router.replace("/guardian");
    } catch {
      setError("Something went wrong. Try again.");
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm rounded-lg border border-[hsl(0_0%_14.9%)] bg-[hsl(0_0%_5.5%)] p-7">
        <h1 className="text-xl font-semibold text-foreground">Set your password</h1>
        <p className="mt-1.5 text-sm text-muted-foreground">
          You were invited to follow your dependant on Nurby Guardian. Choose a password to finish.
        </p>
        {!token && (
          <p className="mt-4 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-400">
            This link is missing its token. Ask the facility to resend the invite.
          </p>
        )}
        <form onSubmit={submit} className="mt-5 space-y-3">
          <input
            type="text"
            placeholder="Your name (optional)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-md border border-[hsl(0_0%_14.9%)] bg-[hsl(0_0%_8%)] px-3 py-2 text-sm text-foreground outline-none focus:border-emerald-500"
          />
          <input
            type="password"
            placeholder="New password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
            className="w-full rounded-md border border-[hsl(0_0%_14.9%)] bg-[hsl(0_0%_8%)] px-3 py-2 text-sm text-foreground outline-none focus:border-emerald-500"
          />
          <input
            type="password"
            placeholder="Confirm password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            className="w-full rounded-md border border-[hsl(0_0%_14.9%)] bg-[hsl(0_0%_8%)] px-3 py-2 text-sm text-foreground outline-none focus:border-emerald-500"
          />
          {error && <p className="text-sm text-red-400">{error}</p>}
          <button
            type="submit"
            disabled={!valid || busy}
            className="w-full rounded-md bg-emerald-500 px-3 py-2 text-sm font-medium text-black transition disabled:opacity-40"
          >
            {busy ? "Setting up..." : "Set password and continue"}
          </button>
        </form>
      </div>
    </div>
  );
}

export default function GuardianClaimPage() {
  return (
    <Suspense fallback={null}>
      <ClaimForm />
    </Suspense>
  );
}
