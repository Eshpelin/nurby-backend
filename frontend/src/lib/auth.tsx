"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useRouter, usePathname } from "next/navigation";

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: string;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  user: User;
}

interface AuthContextValue {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  register: (
    email: string,
    password: string,
    displayName: string
  ) => Promise<void>;
  authFetch: (url: string, init?: RequestInit) => Promise<Response>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const TOKEN_KEY = "nurby_token";
const USER_KEY = "nurby_user";

const PUBLIC_PATHS = ["/login", "/setup"];

// Error that also carries the HTTP status, so callers can react to a
// 409 (duplicate / already done) without string-matching the message.
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// FastAPI returns `detail` as a string for app-level errors but as an
// array of { msg, loc } objects for 422 validation failures. Render a
// clean human string in both cases (this is what produced the
// "[object Object]" message before).
function extractDetail(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object") return fallback;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d && typeof d === "object" ? (d as { msg?: string }).msg : String(d)))
      .filter(Boolean);
    if (msgs.length) return msgs.join(". ");
  }
  return fallback;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const savedToken = localStorage.getItem(TOKEN_KEY);
    const savedUser = localStorage.getItem(USER_KEY);
    if (savedToken && savedUser) {
      setToken(savedToken);
      setUser(JSON.parse(savedUser));
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    if (loading) return;
    if (token || PUBLIC_PATHS.includes(pathname)) return;
    // No token on a protected path. A brand-new install with zero users
    // should land on /setup, not a sign-in form for an account that does
    // not exist. Ask the backend before bouncing.
    let cancelled = false;
    (async () => {
      let dest = "/login";
      try {
        const res = await fetch("/api/auth/needs-setup");
        if (res.ok) {
          const data = await res.json();
          if (data?.needs_setup) dest = "/setup";
        }
      } catch {
        /* default to /login on any error */
      }
      if (!cancelled) router.replace(dest);
    })();
    return () => {
      cancelled = true;
    };
  }, [loading, token, pathname, router]);

  const saveAuth = useCallback((data: TokenResponse) => {
    setToken(data.access_token);
    setUser(data.user);
    localStorage.setItem(TOKEN_KEY, data.access_token);
    localStorage.setItem(USER_KEY, JSON.stringify(data.user));
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new ApiError(extractDetail(body, "Login failed"), res.status);
      }
      const data: TokenResponse = await res.json();
      saveAuth(data);
      router.replace("/");
    },
    [saveAuth, router]
  );

  const register = useCallback(
    async (email: string, password: string, displayName: string) => {
      const res = await fetch("/api/auth/setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          password,
          display_name: displayName,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new ApiError(extractDetail(body, "Setup failed"), res.status);
      }
      const data: TokenResponse = await res.json();
      saveAuth(data);
      router.replace("/");
    },
    [saveAuth, router]
  );

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    router.replace("/login");
  }, [router]);

  const authFetch = useCallback(
    async (url: string, init?: RequestInit): Promise<Response> => {
      const headers = new Headers(init?.headers);
      if (token) {
        headers.set("Authorization", `Bearer ${token}`);
      }
      const res = await fetch(url, { ...init, headers });
      // Stale or invalid token. Clear auth and bounce to login.
      if (res.status === 401 && token) {
        setToken(null);
        setUser(null);
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
        if (!PUBLIC_PATHS.includes(pathname)) {
          router.replace("/login");
        }
      }
      return res;
    },
    [token, pathname, router]
  );

  const value = useMemo(
    () => ({ user, token, loading, login, logout, register, authFetch }),
    [user, token, loading, login, logout, register, authFetch]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
