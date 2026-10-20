"use client";

import { useEffect, useState } from "react";
import { Check, Pencil, Sparkles, X } from "lucide-react";
import { Avatar } from "@/components/system/badges";
import { Card, GhostButton, LiveBadge, PageHeader, PrimaryButton } from "@/components/system/ui";
import { DecisionButtons } from "@/components/system/DecisionButtons";
import { applyApprovalToExecution } from "@/lib/sim/engine";
import { useSession } from "@/lib/store/auth";
import { isLiveContext, useLiveAgents, useLiveApprovals } from "@/lib/store/live";
import { useActivity, useAgents, useApprovals, useUI } from "@/lib/store/app";
import { cn } from "@/lib/cn";
import type { Agent, Approval } from "@/lib/domain/types";

const tabs = ["Pending", "Approved", "Rejected", "All"] as const;
const levels = [
  { id: "manual", label: "Manual" },
  { id: "assist", label: "Assist" },
  { id: "semi", label: "Semi-Autopilot" },
  { id: "full", label: "Full Autopilot" },
] as const;

function ApprovalCardBody({ approval: a, agent }: { approval: Approval; agent?: Agent }) {
  return (
    <>
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-[14.5px] font-bold">{a.title}</p>
          <p className="mt-0.5 flex items-center gap-1.5 text-[12px] text-ink-400">
            {agent && <Avatar name={agent.name} hue={agent.avatarHue} size={16} />}
            Requested by {a.requestedBy} · {a.ageLabel}
          </p>
        </div>
        <span className={cn(
          "shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold",
          a.status === "pending" ? "bg-orange-100 text-orange-700" : a.status === "approved" ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700",
        )}>
          {a.status}
        </span>
      </div>
      <dl className="mt-3 grid gap-x-4 gap-y-1.5 text-[12.5px] sm:grid-cols-2">
        {([["What will happen", a.what], ["Expected result", a.expectedResult], ["Estimated cost", a.cost], ["Affects", a.affects]] as const).map(([k, v]) => (
          <div key={k} className="rounded-lg bg-mist-50 px-2.5 py-1.5 dark:bg-white/5">
            <dt className="text-[11px] font-semibold uppercase tracking-wide text-ink-400">{k}</dt>
            <dd className="leading-5">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 text-[12.5px] text-ink-500"><span className="font-semibold">Why now?</span> {a.rationale}</p>
      {a.gatesTaskId && (
        <p className="mt-1 text-[11.5px] text-ink-400">Gates task {a.gatesTaskId}</p>
      )}
    </>
  );
}

function DemoApprovalsView() {
  const approvals = useApprovals((s) => s.approvals);
  const decide = useApprovals((s) => s.decide);
  const updateApproval = useApprovals((s) => s.updateApproval);
  const agents = useAgents((s) => s.agents);
  const push = useActivity((s) => s.log);
  const { autopilot, setAutopilot } = useUI();
  const [tab, setTab] = useState<(typeof tabs)[number]>("Pending");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [askingId, setAskingId] = useState<string | null>(null);
  const [draftCost, setDraftCost] = useState("");
  const [draftResult, setDraftResult] = useState("");

  const visible = approvals.filter((a) =>
    tab === "All" ? true : tab === "Pending" ? a.status === "pending" : a.status === tab.toLowerCase(),
  );

  const act = (id: string, status: "approved" | "rejected") => {
    decide(id, status);
    applyApprovalToExecution(id, status);
    const a = approvals.find((x) => x.id === id);
    if (a) push({ at: "now", agentName: "You", text: `${status === "approved" ? "Approved" : "Rejected"}: ${a.title}`, kind: "approval" });
  };

  return (
    <>
      <PageHeader
        title="Approval Inbox"
        sub="Nothing spends, publishes or sends without you. Review thetrade-offs, then decide."
        actions={
          <div className="flex items-center gap-1 rounded-[10px] border border-mist-200 bg-white p-1 text-[12px] dark:border-white/10 dark:bg-night-900">
            {levels.map((l) => (
              <button
                key={l.id}
                onClick={() => setAutopilot(l.id)}
                className={cn(
                  "rounded-lg px-2 py-1 font-medium",
                  autopilot === l.id ? "bg-brand-500 text-white" : "text-ink-500 hover:text-ink-950",
                )}
              >
                {l.label}
              </button>
            ))}
          </div>
        }
      />
      <div className="flex gap-1 border-b border-mist-200 dark:border-white/10">
        {tabs.map((t) => {
          const n = t === "All" ? approvals.length : approvals.filter((a) => t === "Pending" ? a.status === "pending" : a.status === t.toLowerCase()).length;
          return (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn(
                "border-b-2 px-3 py-2 text-[13px] font-medium",
                tab === t ? "border-brand-500 text-brand-600" : "border-transparent text-ink-500",
              )}
            >
              {t} ({n})
            </button>
          );
        })}
      </div>
      <div className="grid gap-3 xl:grid-cols-2">
        {visible.map((a) => {
          const agent = agents.find((x) => x.id === a.requestedByAgentId);
          return (
            <Card key={a.id} className="p-4">
              <ApprovalCardBody approval={a} agent={agent} />
              {askingId === a.id && (
                <div className="mt-2 rounded-xl border border-brand-200 bg-brand-50/60 p-3 text-[12.5px] leading-5 dark:border-brand-500/30 dark:bg-brand-500/10">
                  <p className="font-bold text-brand-600 dark:text-sky-300">AI assessment</p>
                  <p className="mt-0.5">
                    {a.requestedBy} proposes spending {a.cost} affecting {a.affects.toLowerCase()}.
                    Expected outcome: {a.expectedResult.toLowerCase()}. Timing argument: {a.rationale} Autopilot is {autopilot} —
                    {autopilot === "manual" || autopilot === "assist" ? " your decision is required." : " low-risk steps may proceed automatically after this."}
                  </p>
                </div>
              )}
              {editingId === a.id ? (
                <div className="mt-2 grid gap-2 sm:grid-cols-2">
                  <label className="text-[12px] font-semibold">Expected result
                    <input value={draftResult} onChange={(e) => setDraftResult(e.target.value)} className="mt-1 h-9 w-full rounded-lg border border-mist-200 bg-white px-2.5 font-normal outline-none focus:border-brand-500 dark:border-white/10 dark:bg-white/5" />
                  </label>
                  <label className="text-[12px] font-semibold">Estimated cost
                    <input value={draftCost} onChange={(e) => setDraftCost(e.target.value)} className="mt-1 h-9 w-full rounded-lg border border-mist-200 bg-white px-2.5 font-normal outline-none focus:border-brand-500 dark:border-white/10 dark:bg-white/5" />
                  </label>
                  <div className="flex gap-2 sm:col-span-2">
                    <PrimaryButton onClick={() => { updateApproval(a.id, { expectedResult: draftResult || a.expectedResult, cost: draftCost || a.cost }); setEditingId(null); }}>Save changes</PrimaryButton>
                    <GhostButton onClick={() => setEditingId(null)}>Cancel</GhostButton>
                  </div>
                </div>
              ) : a.status === "pending" ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  <PrimaryButton onClick={() => act(a.id, "approved")}><Check className="h-4 w-4" /> Approve</PrimaryButton>
                  <GhostButton onClick={() => act(a.id, "rejected")}><X className="h-4 w-4" /> Reject</GhostButton>
                  <GhostButton onClick={() => { setDraftCost(a.cost); setDraftResult(a.expectedResult); setEditingId(a.id); }}><Pencil className="h-4 w-4" /> Edit</GhostButton>
                  <GhostButton onClick={() => setAskingId(askingId === a.id ? null : a.id)}><Sparkles className="h-4 w-4" /> Ask AI</GhostButton>
                </div>
              ) : (
                <p className="mt-3 text-[12.5px] font-medium text-ink-400">Decided — recorded in the audit trail.</p>
              )}
            </Card>
          );
        })}
        {visible.length === 0 && (
          <Card className="p-8 text-center text-[13px] text-ink-400 xl:col-span-2">
            Nothing here. Approvals appear when an AI employee needs a human decision.
          </Card>
        )}
      </div>
    </>
  );
}

function LiveApprovalsView({ businessId }: { businessId: string }) {
  const { status, approvals, error, load, reset } = useLiveApprovals();
  const agents = useLiveAgents((s) => s.agents);
  const ensureAgents = useLiveAgents((s) => s.ensure);
  const resetAgents = useLiveAgents((s) => s.reset);
  const [tab, setTab] = useState<(typeof tabs)[number]>("Pending");

  useEffect(() => {
    void load(businessId);
    void ensureAgents(businessId);
    return () => {
      reset();
      resetAgents();
    };
  }, [businessId, load, ensureAgents, reset, resetAgents]);

  const visible = approvals.filter((a) =>
    tab === "All" ? true : tab === "Pending" ? a.status === "pending" : a.status === tab.toLowerCase(),
  );

  return (
    <>
      <PageHeader
        title="Approval Inbox"
        sub="Server approvals for your business. Decisions are recorded with an audit trail."
        badge={<LiveBadge />}
      />
      {status === "loading" || status === "idle" ? (
        <Card className="p-8 text-center text-[13px] text-ink-400">Loading approvals…</Card>
      ) : status === "error" ? (
        <Card className="p-8 text-center">
          <p className="text-[14px] font-semibold">Couldn&apos;t load approvals</p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">{error}</p>
          <div className="mt-3 flex justify-center">
            <PrimaryButton className="justify-center" onClick={() => void load(businessId)}>
              Retry
            </PrimaryButton>
          </div>
        </Card>
      ) : (
        <>
          <div className="flex gap-1 border-b border-mist-200 dark:border-white/10">
            {tabs.map((t) => {
              const n = t === "All" ? approvals.length : approvals.filter((a) => t === "Pending" ? a.status === "pending" : a.status === t.toLowerCase()).length;
              return (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={cn(
                    "border-b-2 px-3 py-2 text-[13px] font-medium",
                    tab === t ? "border-brand-500 text-brand-600" : "border-transparent text-ink-500",
                  )}
                >
                  {t} ({n})
                </button>
              );
            })}
          </div>
          <div className="grid gap-3 xl:grid-cols-2">
            {visible.map((a) => {
              const agent = agents.find((x) => x.id === a.requestedByAgentId);
              return (
                <Card key={a.id} className="p-4">
                  <ApprovalCardBody approval={a} agent={agent} />
                  {a.status === "pending" ? (
                    <DecisionButtons businessId={businessId} approvalId={a.id} />
                  ) : (
                    <p className="mt-3 text-[12.5px] font-medium text-ink-400">Decided — recorded in the audit trail.</p>
                  )}
                </Card>
              );
            })}
            {visible.length === 0 && (
              <Card className="p-8 text-center text-[13px] text-ink-400 xl:col-span-2">
                No approvals in this business.
              </Card>
            )}
          </div>
        </>
      )}
    </>
  );
}

export default function ApprovalsPage() {
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveApprovalsView businessId={currentBusinessId} />;
  }
  return <DemoApprovalsView />;
}
