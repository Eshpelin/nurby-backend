"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { NotificationItem, NotificationsDropdown } from "./notifications";

const NAV_ITEMS = [
  { label: "Dashboard", href: "/" },
  { label: "Recordings", href: "/recordings" },
  { label: "People", href: "/people" },
  { label: "Rules", href: "/rules" },
  { label: "Settings", href: "/settings" },
];

interface ProviderInfo {
  name: string;
  kind: string;
  active: boolean;
}

function SunIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="5" />
      <line x1="12" y1="1" x2="12" y2="3" />
      <line x1="12" y1="21" x2="12" y2="23" />
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
      <line x1="1" y1="12" x2="3" y2="12" />
      <line x1="21" y1="12" x2="23" y2="12" />
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

function getInitials(name: string | null | undefined): string {
  if (!name) return "N";
  return name
    .split(" ")
    .map((w) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);
}

export function Navbar() {
  const pathname = usePathname();
  const { user, logout, authFetch} = useAuth();
  const { resolvedTheme, setTheme } = useTheme();
  const [provider, setProvider] = useState<ProviderInfo | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);

  const fetchProvider = useCallback(async () => {
    try {
      const res = await authFetch("/api/providers");
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

  const fetchUnreadCount = useCallback(async () => {
    try {
      const res = await authFetch("/api/notifications/count");
      if (res.ok) {
        const data = await res.json();
        setUnreadCount((prev) => (prev === data.unread ? prev : data.unread));
      }
    } catch {
      /* silent */
    }
  }, []);

  const fetchNotifications = useCallback(async () => {
    try {
      const res = await authFetch("/api/notifications?limit=20");
      if (res.ok) {
        const list: NotificationItem[] = await res.json();
        setNotifications(list);
      }
    } catch {
      /* silent */
    }
  }, []);

  const handleMarkRead = useCallback(
    async (id: string) => {
      try {
        await authFetch(`/api/notifications/${id}/read`, { method: "PATCH" });
        setNotifications((prev) =>
          prev.map((n) => (n.id === id ? { ...n, read: true } : n))
        );
        setUnreadCount((prev) => Math.max(0, prev - 1));
      } catch {
        /* silent */
      }
    },
    []
  );

  const handleMarkAllRead = useCallback(async () => {
    try {
      await authFetch("/api/notifications/read-all", { method: "POST" });
      setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
      setUnreadCount(0);
    } catch {
      /* silent */
    }
  }, []);

  const closeDropdown = useCallback(() => setDropdownOpen(false), []);

  const toggleDropdown = useCallback(() => {
    setDropdownOpen((prev) => {
      const opening = !prev;
      if (opening) {
        fetchNotifications();
      }
      return opening;
    });
  }, [fetchNotifications]);

  useEffect(() => {
    fetchProvider();
    fetchUnreadCount();
    const providerInterval = setInterval(fetchProvider, 30000);
    const countInterval = setInterval(fetchUnreadCount, 15000);
    return () => {
      clearInterval(providerInterval);
      clearInterval(countInterval);
    };
  }, [fetchProvider, fetchUnreadCount]);

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

          {/* Theme toggle */}
          <button
            onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground transition-colors"
            aria-label="Toggle theme"
          >
            {resolvedTheme === "dark" ? <SunIcon /> : <MoonIcon />}
          </button>

          {/* Notifications */}
          <div className="relative">
            <button
              onClick={toggleDropdown}
              className="relative p-1.5 rounded-md text-muted-foreground hover:text-foreground transition-colors"
              aria-label="Notifications"
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
                <path d="M13.73 21a2 2 0 0 1-3.46 0" />
              </svg>
              {unreadCount > 0 && (
                <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 flex items-center justify-center rounded-full bg-red-500 text-white text-[10px] font-bold leading-none">
                  {unreadCount > 99 ? "99+" : unreadCount}
                </span>
              )}
            </button>
            <NotificationsDropdown
              open={dropdownOpen}
              onClose={closeDropdown}
              notifications={notifications}
              onMarkRead={handleMarkRead}
              onMarkAllRead={handleMarkAllRead}
            />
          </div>

          {/* User avatar + logout */}
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center text-xs font-medium">
              {getInitials(user?.display_name)}
            </div>
            <button
              onClick={logout}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              Logout
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
