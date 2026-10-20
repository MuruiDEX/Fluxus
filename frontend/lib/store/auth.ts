/**
 * Session auth state (Stage 6 frontend slice, extended in Stage 7.2).
 *
 * Holds only non-secret data: the current user profile, business list, the
 * selected business context, and connection status. Session id stays in the
 * HttpOnly cookie, CSRF token in the api module's memory — neither ever
 * lands in Zustand, localStorage, or the URL. The demo simulation stores
 * (`lib/store/app.ts`) are untouched and never merged with server data.
 *
 * Business context rules:
 * - `businesses` comes ONLY from `/auth/me` (or login); IDs are never
 *   invented, hardcoded, or taken from the URL.
 * - `currentBusinessId` auto-selects the first available business and is
 *   preserved across refreshes while still a member; cleared on logout/401
 *   or when membership lapses. `selectBusiness` accepts member IDs only.
 * - The ID is a UI pointer, never proof of authorization — the backend
 *   re-checks membership on every request.
 * - Everything lives in memory (no localStorage anywhere in the app);
 *   a reload re-runs `refresh()` and re-derives the context.
 */

import { create } from "zustand";
import {
  ApiError,
  apiGetBusiness,
  apiLogin,
  apiLogout,
  apiMe,
  onUnauthorized,
  type ApiUser,
} from "@/lib/api";

export type SessionStatus = "unknown" | "authenticated" | "anonymous" | "unreachable";

interface SessionState {
  status: SessionStatus;
  user: ApiUser | null;
  businesses: string[];
  /** Selected business context (member of `businesses`, never anything else). */
  currentBusinessId: string | null;
  /** Lazily loaded id -> display name cache for the selector. */
  businessNames: Record<string, string>;
  /** True while a session check is in flight (drives retry-button state). */
  refreshing: boolean;
  error: string | null;
  /** Last non-401 refresh failure (network or server error), for retry UI. */
  sessionError: string | null;
  login: (email: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  retry: () => Promise<void>;
  selectBusiness: (businessId: string) => boolean;
  ensureBusinessNames: () => Promise<void>;
  markAnonymous: () => void;
}

function pickContext(businesses: string[], current: string | null): string | null {
  if (current !== null && businesses.includes(current)) return current;
  return businesses[0] ?? null;
}

function clearSession() {
  return {
    status: "anonymous" as const,
    user: null,
    businesses: [],
    currentBusinessId: null,
    businessNames: {},
    refreshing: false,
    sessionError: null,
  };
}

let refreshFlight: Promise<void> | null = null;
let namesFlight: Promise<void> | null = null;

export const useSession = create<SessionState>((set, get) => ({
  status: "unknown",
  user: null,
  businesses: [],
  currentBusinessId: null,
  businessNames: {},
  refreshing: false,
  error: null,
  sessionError: null,

  login: async (email, password) => {
    set({ error: null });
    try {
      const data = await apiLogin(email, password);
      set({
        status: "authenticated",
        user: data.user,
        businesses: data.businesses,
        currentBusinessId: pickContext(data.businesses, get().currentBusinessId),
        sessionError: null,
      });
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.code === "network_unreachable") {
        // Failed login proves nothing — drop any stale identity, stay usable.
        set({
          ...clearSession(),
          status: "unreachable" as const,
          sessionError: "Backend is not reachable.",
        });
        return false;
      }
      set({
        ...clearSession(),
        error: err instanceof Error ? err.message : "Login failed.",
      });
      return false;
    }
  },

  logout: async () => {
    await apiLogout();
    set({ ...clearSession(), error: null });
  },

  refresh: () => {
    // Dedupe concurrent checks (StrictMode double-mount, AuthGate + retry):
    // all callers share one flight, so re-init can never fan out requests.
    if (!refreshFlight) {
      set({ refreshing: true });
      refreshFlight = (async () => {
        try {
          const data = await apiMe();
          set((s) => ({
            status: "authenticated",
            user: data.user,
            businesses: data.businesses,
            currentBusinessId: pickContext(data.businesses, s.currentBusinessId),
            refreshing: false,
            sessionError: null,
          }));
        } catch (err) {
          if (err instanceof ApiError && err.code === "network_unreachable") {
            // No backend reachable (dev without server, e2e): stay in demo
            // mode, but keep the error visible so retry is possible.
            set({
              status: "unreachable",
              refreshing: false,
              sessionError: "Backend is not reachable.",
            });
            return;
          }
          if (err instanceof ApiError && err.status === 401) {
            // Proven logout (also fired via the 401 hook) → login screen.
            set({ ...clearSession(), error: null });
            return;
          }
          if (err instanceof ApiError) {
            // Reachable backend, non-401 failure (e.g. 5xx): do NOT present
            // as a proven logout — stay usable, surface error + retry.
            set({
              status: "unreachable",
              refreshing: false,
              sessionError: err.message,
            });
            return;
          }
          set({ ...clearSession(), error: null });
        } finally {
          refreshFlight = null;
        }
      })();
    }
    return refreshFlight;
  },

  retry: () => get().refresh(),

  selectBusiness: (businessId) => {
    if (!get().businesses.includes(businessId)) return false;
    if (get().currentBusinessId !== businessId) set({ currentBusinessId: businessId });
    return true;
  },

  ensureBusinessNames: () => {
    if (!namesFlight) {
      const missing = get().businesses.filter((id) => !(id in get().businessNames));
      if (missing.length === 0) return Promise.resolve();
      namesFlight = (async () => {
        try {
          const entries = await Promise.all(
            missing.map(async (id) => {
              try {
                const business = await apiGetBusiness(id);
                return [id, business.name] as const;
              } catch {
                return null;
              }
            }),
          );
          const names: Record<string, string> = {};
          for (const entry of entries) {
            if (entry) names[entry[0]] = entry[1];
          }
          if (Object.keys(names).length > 0) {
            set((s) => ({ businessNames: { ...s.businessNames, ...names } }));
          }
        } finally {
          namesFlight = null;
        }
      })();
    }
    return namesFlight;
  },

  markAnonymous: () => set(clearSession()),
}));

onUnauthorized(() => {
  useSession.getState().markAnonymous();
});
