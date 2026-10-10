/**
 * Marketplace hiring bridge — maps a listing slug to the team agent it
 * unlocks (packs install capabilities without a single agent).
 * Single place both the Marketplace and the Team Lead wizard hire through,
 * so `hired` state stays consistent everywhere.
 */
import { useAgents, useMarketplace } from "@/lib/store/app";

export const SLUG_TO_AGENT: Record<string, string | null> = {
  "sales-manager": "ag_sales",
  "marketing-manager": "ag_marketing",
  "seo-specialist": "ag_seo",
  "support-ai": "ag_support",
  "cfo-ai": "ag_cfo",
  "fitness-pack": null,
};

export function hireListing(slug: string): string | null {
  useMarketplace.getState().hireSlug(slug);
  const agentId = SLUG_TO_AGENT[slug] ?? null;
  if (agentId) {
    const exists = useAgents.getState().agents.some((a) => a.id === agentId);
    if (exists) useAgents.getState().hire(agentId);
  }
  return agentId;
}
