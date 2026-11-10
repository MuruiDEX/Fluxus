"use client";

import { useEffect } from "react";
import { useSession } from "@/lib/store/auth";
import { Select } from "./controls";

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
        className="hidden max-w-[220px] truncate px-2 text-caption text-fg-subtle sm:block"
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
        className="hidden max-w-[220px] truncate px-2 text-body-sm font-medium text-fg sm:block"
      >
        {label(businesses[0])}
      </span>
    );
  }

  return (
    <Select
      aria-label="Select business"
      title="Select business"
      value={currentBusinessId ?? ""}
      onChange={(e) => selectBusiness(e.target.value)}
      className="h-9 w-auto max-w-[220px] truncate bg-surface-sunken text-body-sm text-fg hover:border-brand/40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
    >
      {businesses.map((id) => (
        <option key={id} value={id}>
          {label(id)}
        </option>
      ))}
    </Select>
  );
}
