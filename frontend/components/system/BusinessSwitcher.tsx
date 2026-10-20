"use client";

import { useEffect } from "react";
import { useSession } from "@/lib/store/auth";

/**
 * Authenticated business context switcher (Stage 7.2).
 *
 * - Visible only when authenticated; renders nothing in demo/anonymous states.
 * - Options come ONLY from `/auth/me` (`businesses`); names resolve lazily
 *   via `GET /businesses/{id}` and fall back to a truncated id.
 * - Single business → static label (no fake choice). Zero → access notice,
 *   and no LIVE business requests are issued from this state.
 * - Selection writes memory-only Zustand state; the backend re-checks
 *   membership on every request regardless of what is selected here.
 */
export function BusinessSwitcher() {
  const {
    status,
    businesses,
    currentBusinessId,
    businessNames,
    selectBusiness,
    ensureBusinessNames,
  } = useSession();

  useEffect(() => {
    if (status === "authenticated" && businesses.length > 0) {
      void ensureBusinessNames();
    }
  }, [status, businesses, ensureBusinessNames]);

  if (status !== "authenticated") return null;

  if (businesses.length === 0) {
    return (
      <span
        role="status"
        title="Your account has no business memberships"
        className="hidden max-w-[220px] truncate px-2 text-[12px] text-ink-400 sm:block dark:text-slate-400"
      >
        No business access — ask an operator for a grant.
      </span>
    );
  }

  const label = (id: string) => businessNames[id] ?? `${id.slice(0, 8)}…`;

  if (businesses.length === 1) {
    return (
      <span
        title={businesses[0]}
        className="hidden max-w-[220px] truncate px-2 text-[13px] font-medium text-ink-700 sm:block dark:text-slate-200"
      >
        {label(businesses[0])}
      </span>
    );
  }

  return (
    <select
      aria-label="Select business"
      title="Select business"
      value={currentBusinessId ?? ""}
      onChange={(e) => selectBusiness(e.target.value)}
      className="h-9 max-w-[220px] truncate rounded-[10px] border border-mist-200 bg-mist-50 px-2 text-[13px] text-ink-700 outline-none hover:border-brand-200 dark:border-white/10 dark:bg-white/5 dark:text-slate-200"
    >
      {businesses.map((id) => (
        <option key={id} value={id}>
          {label(id)}
        </option>
      ))}
    </select>
  );
}
