"use client";

import { useEffect, useState } from "react";
import { ActivityFeed } from "@/components/system/ActivityFeed";
import { Card, LiveBadge, PageHeader, PrimaryButton } from "@/components/system/ui";
import { formatWhen } from "@/lib/live/datetime";
import { useSession } from "@/lib/store/auth";
import { isLiveContext, useLiveActivity, useLiveProjects } from "@/lib/store/live";
import { useActivity } from "@/lib/store/app";

function DemoActivityView() {
  const events = useActivity((s) => s.events);
  return (
    <>
      <PageHeader
        title="Activity"
        sub="Simulated audit trail for the demo business."
      />
      <Card className="p-4">
        <ActivityFeed events={events} />
      </Card>
    </>
  );
}

const ALL_PROJECTS = "all";

function LiveActivityView({ businessId }: { businessId: string }) {
  const { status, events, total, error, load, reset } = useLiveActivity();
  const projectsSlice = useLiveProjects();
  const [projectId, setProjectId] = useState<string>(ALL_PROJECTS);

  useEffect(() => {
    void projectsSlice.load(businessId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId]);

  useEffect(() => {
    void load({ businessId, projectId: projectId === ALL_PROJECTS ? undefined : projectId });
    return () => reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId, projectId]);

  const projects = projectsSlice.projects;

  return (
    <>
      <PageHeader
        title="Activity"
        sub="Server audit trail, newest first."
        badge={<LiveBadge />}
        actions={
          <label className="flex items-center gap-2 text-[12.5px] text-ink-500">
            Project
            <select
              aria-label="Filter activity by project"
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              className="h-9 max-w-[220px] truncate rounded-[10px] border border-mist-200 bg-white px-2 text-[13px] text-ink-700 outline-none hover:border-brand-200 dark:border-white/10 dark:bg-night-900 dark:text-slate-200"
            >
              <option value={ALL_PROJECTS}>All projects</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </select>
          </label>
        }
      />
      {status === "loading" || status === "idle" ? (
        <Card className="p-8 text-center text-[13px] text-ink-400">Loading activity…</Card>
      ) : status === "error" ? (
        <Card className="p-8 text-center">
          <p className="text-[14px] font-semibold">Couldn&apos;t load activity</p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">{error}</p>
          <div className="mt-3 flex justify-center">
            <PrimaryButton
              className="justify-center"
              onClick={() =>
                void load({ businessId, projectId: projectId === ALL_PROJECTS ? undefined : projectId })
              }
            >
              Retry
            </PrimaryButton>
          </div>
        </Card>
      ) : (
        <Card className="p-4">
          <ActivityFeed events={events.map((e) => ({ ...e, at: formatWhen(e.at) }))} />
          <p className="mt-2 text-[11.5px] text-ink-400">
            Showing {events.length} of {total} most recent events.
          </p>
        </Card>
      )}
    </>
  );
}

export default function ActivityPage() {
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveActivityView businessId={currentBusinessId} />;
  }
  return <DemoActivityView />;
}
