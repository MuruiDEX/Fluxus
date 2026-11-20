/**
 * Account & profile presentation helpers.
 *
 * The account surface is deliberately thin because the backend is: it exposes
 * only `POST /auth/login`, `POST /auth/logout` and `GET /auth/me` (user id,
 * email, display name, business ids). There is no profile update, session
 * list/revocation, password, locale or notification preference endpoint.
 *
 * Everything here either derives display text from the real session payload or
 * names a capability this deployment does not provide. Nothing invents account
 * state, and no helper touches the session cookie or CSRF token.
 */

export type ThemePreference = "light" | "dark" | "system";

/** `next-themes` reports `undefined` until the client preference is known. */
export function readThemePreference(value: string | undefined | null): ThemePreference | null {
  return value === "light" || value === "dark" || value === "system" ? value : null;
}

/**
 * The toolbar toggle acts on the palette actually on screen, so a "system" user
 * on a dark OS toggles to light and that explicit choice then wins.
 */
export function toggledTheme(resolved: string | undefined | null): "light" | "dark" {
  return resolved === "dark" ? "light" : "dark";
}

/**
 * Initials for the account avatar. Falls back to the email handle when no
 * display name is stored, so the avatar is never blank.
 */
export function accountInitials(
  displayName: string | null | undefined,
  email: string | null | undefined,
): string {
  const name = (displayName ?? "").trim();
  if (name) {
    const initials = name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join("");
    if (initials) return initials.toUpperCase();
  }
  const handle = (email ?? "").trim();
  return handle ? handle[0].toUpperCase() : "?";
}

/** Short, support-friendly form of a UUID account id (never a secret). */
export function shortAccountId(id: string | null | undefined): string {
  const value = (id ?? "").trim();
  if (!value) return "—";
  return value.length <= 12 ? value : `${value.slice(0, 8)}…`;
}

export function workspaceSummary(count: number): string {
  const safe = Number.isFinite(count) ? Math.max(0, Math.trunc(count)) : 0;
  if (safe === 0) return "No workspaces";
  return safe === 1 ? "1 workspace" : `${safe} workspaces`;
}

/**
 * A setting the UI must not fake. `detail` states why it cannot be saved, so a
 * reader learns the limitation instead of clicking a dead control.
 */
export interface MissingCapability {
  label: string;
  detail: string;
}

/** Language / time zone / regional formatting have no account record. */
export const localisationCapabilities: MissingCapability[] = [
  {
    label: "Language",
    detail: "The interface is English-only and the account record has no language field to save.",
  },
  {
    label: "Time zone",
    detail: "No time-zone preference is stored per account, so a saved choice would be discarded.",
  },
  {
    label: "Date & number format",
    detail: "Values use the app's fixed formatting; there is no regional preference to read or write.",
  },
  {
    label: "Week starts on",
    detail: "Calendar settings are not configurable per account.",
  },
];

/** No notification preference records or delivery channels exist. */
export const notificationCapabilities: MissingCapability[] = [
  {
    label: "Email notifications",
    detail: "No delivery channel is configured for this deployment.",
  },
  {
    label: "Approval & task alerts",
    detail: "There are no notification preference records to read or write.",
  },
  {
    label: "Weekly summary",
    detail: "No digest job or preference endpoint exists.",
  },
];

/** Session and credential management the API cannot perform. */
export const securityCapabilities: MissingCapability[] = [
  {
    label: "Other sessions",
    detail:
      "The API can only revoke the session that made the request; it cannot list or revoke other devices.",
  },
  {
    label: "Change password",
    detail:
      "No verified change-password or reset flow is exposed, so this action stays out of the UI.",
  },
  {
    label: "Two-factor authentication",
    detail: "Not implemented by the backend.",
  },
];
