/**
 * Session auth state (Stage 6 frontend slice).
 *
 * Holds only non-secret data: the current user profile, business list, and
 * connection status. Session id stays in the HttpOnly cookie, CSRF token in
 * the api module's memory — neither ever lands in Zustand, localStorage, or
 * the URL. The demo simulation stores (`lib/store/app.ts`) are untouched and
 * never merged with server data.
 */

import { create } from "zustand";
import { ApiError, apiLogin, apiLogout, apiMe, onUnauthorized, type ApiUser } from "@/lib/api";

export type SessionStatus = "unknown" | "authenticated" | "anonymous" | "unreachable";

interface SessionState {
  status: SessionStatus;
  user: ApiUser | null;
  businesses: string[];
  error: string | null;
  login: (email: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  markAnonymous: () => void;
}

export const useSession = create<SessionState>((set) => ({
  status: "unknown",
  user: null,
  businesses: [],
  error: null,

  login: async (email, password) => {
    set({ error: null });
    try {
      const data = await apiLogin(email, password);
      set({ status: "authenticated", user: data.user, businesses: data.businesses });
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.code === "network_unreachable") {
        set({ status: "unreachable", error: "Backend is not reachable." });
        return false;
      }
      set({
        status: "anonymous",
        user: null,
        businesses: [],
        error: err instanceof Error ? err.message : "Login failed.",
      });
      return false;
    }
  },

  logout: async () => {
    await apiLogout();
    set({ status: "anonymous", user: null, businesses: [], error: null });
  },

  refresh: async () => {
    try {
      const data = await apiMe();
      set({ status: "authenticated", user: data.user, businesses: data.businesses });
    } catch (err) {
      if (err instanceof ApiError && err.code === "network_unreachable") {
        // No backend reachable (dev without server, e2e): stay in demo mode.
        set({ status: "unreachable" });
        return;
      }
      set({ status: "anonymous", user: null, businesses: [] });
    }
  },

  markAnonymous: () => set({ status: "anonymous", user: null, businesses: [] }),
}));

onUnauthorized(() => {
  useSession.getState().markAnonymous();
});
