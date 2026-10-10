import { Suspense } from "react";
import { agents } from "@/lib/data/seed-northstar";
import { AgentDetail } from "./view";

export function generateStaticParams() {
  return agents.map((a) => ({ id: a.id }));
}

export default function AgentDetailPage() {
  return (
    <Suspense fallback={<p className="text-[13px] text-ink-400">Loading agent…</p>}>
      <AgentDetail />
    </Suspense>
  );
}
