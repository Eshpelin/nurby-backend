"use client";

import { useAuth } from "@/lib/auth";

// The dependant's enrolled photo (a parent recognizes a face in a second) with
// a calm initials silhouette fallback when none is set.
export function DependantAvatar({
  photoUrl,
  name,
  size = 40,
}: {
  photoUrl: string | null;
  name: string | null;
  size?: number;
}) {
  const { token } = useAuth();
  const initials = (name || "?")
    .split(" ")
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  if (photoUrl) {
    const src = `${photoUrl}?token=${token}`;
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={src}
        alt={name || "Dependant"}
        width={size}
        height={size}
        className="rounded-full object-cover border border-border shrink-0"
        style={{ width: size, height: size }}
      />
    );
  }
  return (
    <div
      className="rounded-full bg-zinc-800 text-zinc-300 flex items-center justify-center border border-border shrink-0"
      style={{ width: size, height: size, fontSize: size * 0.4 }}
    >
      {initials}
    </div>
  );
}
