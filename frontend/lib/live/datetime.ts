/**
 * Client-side display derivation for server timestamps (contract §4).
 *
 * The wire carries RFC 3339 (`ActivityResponse.at`); human strings like
 * "2h ago" are presentation only and computed here — never stored, never
 * sent back. Unparseable input renders as-is (honest fallback, no invented
 * timestamps).
 */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function isIsoDate(value: string): boolean {
  return value !== "" && !Number.isNaN(Date.parse(value));
}

export function formatWhen(iso: string, nowMs: number = Date.now()): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  const diffS = Math.max(0, Math.floor((nowMs - ms) / 1000));
  if (diffS < 60) return "just now";
  const diffM = Math.floor(diffS / 60);
  if (diffM < 60) return `${diffM}m ago`;
  const diffH = Math.floor(diffM / 60);
  if (diffH < 24) return `${diffH}h ago`;
  const d = new Date(ms);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}
