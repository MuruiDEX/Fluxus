import { Suspense } from "react";
import { PROJECT_ID } from "@/lib/data/seed-northstar";
import { ProjectDetail } from "./view";

export function generateStaticParams() {
  // Seed-known projects are pre-rendered. Projects created at runtime
  // (e.g. via the Team Lead wizard) resolve client-side; direct loads of
  // unknown ids fall back to the active project in the client view.
  return [{ id: PROJECT_ID }];
}

export default function ProjectDetailPage() {
  return (
    <Suspense fallback={<p className="text-[13px] text-ink-400">Loading project...</p>}>
      <ProjectDetail />
    </Suspense>
  );
}
