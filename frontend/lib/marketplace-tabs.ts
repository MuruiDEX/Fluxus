/**
 * Marketplace tab semantics (pure, unit-tested).
 *
 * The demo catalog carries display strings ("2.1k" installs, "$19/month"), so
 * tabs must sort/filter on parsed values instead of the raw text, and every
 * tab must be honest about an empty result instead of silently showing
 * everything (the previous behaviour made the tabs look broken).
 */

export const MARKETPLACE_TABS = [
  "Recommended",
  "Trending",
  "Best Rated",
  "New",
  "Free",
  "Premium",
] as const;

export type MarketplaceTab = (typeof MARKETPLACE_TABS)[number];

export interface TabbableListing {
  name: string;
  role: string;
  blurb: string;
  price: string;
  rating: number;
  installs: string;
  badge?: string | null;
}

/** Parse "2.1k" / "950" / "1.2m" into a comparable number; junk → 0. */
export function parseInstalls(raw: string): number {
  const text = raw.trim().toLowerCase().replace(/,/g, "");
  const match = /^(\d+(?:\.\d+)?)\s*([km])?/.exec(text);
  if (!match) return 0;
  const value = Number(match[1]);
  if (!Number.isFinite(value)) return 0;
  if (match[2] === "k") return value * 1_000;
  if (match[2] === "m") return value * 1_000_000;
  return value;
}

/** "$0", "$0.00/month", "Free" → true. Any positive price is not free. */
export function isFreePrice(price: string): boolean {
  const text = price.trim().toLowerCase();
  if (text === "free") return true;
  const amount = /(\d+(?:\.\d+)?)/.exec(text);
  return amount ? Number(amount[1]) === 0 : false;
}

export function matchesQuery(item: TabbableListing, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return `${item.name} ${item.role} ${item.blurb}`.toLowerCase().includes(q);
}

/**
 * Apply tab + text query. Returns a new array (never mutates the store slice)
 * and keeps "Recommended" as the unfiltered default order.
 */
export function selectListings<T extends TabbableListing>(
  items: readonly T[],
  tab: MarketplaceTab,
  query: string,
): T[] {
  const matching = items.filter((item) => matchesQuery(item, query));
  switch (tab) {
    case "Trending":
      return [...matching].sort((a, b) => parseInstalls(b.installs) - parseInstalls(a.installs));
    case "Best Rated":
      return [...matching].sort((a, b) => b.rating - a.rating);
    case "New":
      return matching.filter((item) => (item.badge ?? "").toLowerCase() === "new");
    case "Free":
      return matching.filter((item) => isFreePrice(item.price));
    case "Premium":
      return matching.filter((item) => !isFreePrice(item.price));
    case "Recommended":
    default:
      return matching;
  }
}
