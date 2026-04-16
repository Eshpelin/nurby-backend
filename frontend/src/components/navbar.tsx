"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

const NAV_ITEMS = [
  { label: "Dashboard", href: "/" },
  { label: "People", href: "/people" },
  { label: "Rules", href: "/rules" },
  { label: "Settings", href: "/settings" },
];

interface ProviderInfo {
  name: string;
  kind: string;
  active: boolean;
}

export function Navbar() {
  const pathname = usePathname();
  const [provider, setProvider] = useState<ProviderInfo | null>(null);
  const [loaded, setLoaded] = useState(false);

  const fetchProvider = useCallback(async () => {
    try {
      const res = await fetch("/api/providers");
      if (res.ok) {
        const list: ProviderInfo[] = await res.json();
        const active = list.find((p) => p.active) || null;
        setProvider(active);
      }
    } catch {
      /* silent */
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    fetchProvider();
    const interval = setInterval(fetchProvider, 30000);
    return () => clearInterval(interval);
  }, [fetchProvider]);

  return (
    <div className="border-b border-border bg-background sticky top-0 z-50">
      <div className="px-6 h-14 flex items-center justify-between">
        <div className="flex items-center gap-8">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-md bg-accent flex items-center justify-center">
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="black"
                strokeWidth="2.5"
              >
                <circle cx="12" cy="12" r="3" />
                <path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z" />
              </svg>
            </div>
            <span className="font-semibold tracking-tight">Nurby</span>
            <span className="font-mono text-xs text-muted-foreground ml-2">
              v0.1
            </span>
          </div>

          <nav className="flex items-center gap-1">
            {NAV_ITEMS.map((item) => {
              const isActive = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`px-3 py-1.5 rounded-md text-sm transition-all ${
                    isActive
                      ? "bg-muted text-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="flex items-center gap-3">
          <Link
            href="/settings"
            className="flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            {loaded && (
              <>
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    provider ? "bg-green-500 pulse-dot" : "bg-yellow-500"
                  }`}
                />
                <span className="font-mono">
                  {provider ? `${provider.kind} / ${provider.name}` : "no provider configured"}
                </span>
              </>
            )}
          </Link>
          <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center text-xs font-medium">
            N
          </div>
        </div>
      </div>
    </div>
  );
}
