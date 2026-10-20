"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowRight, Loader2 } from "lucide-react";
import {
  Card,
  GhostButton,
  LiveBadge,
  PageHeader,
  PrimaryButton,
} from "@/components/system/ui";
import { useLiveIntake, type IntakePhase } from "@/lib/store/intake";
import type { ApiBrief } from "@/lib/api";
import { cn } from "@/lib/cn";

/**
 * LIVE Team-Lead intake (Stage 11) — goal → questions → answers → brief
 * review → plan, all through the management intake store (real endpoints,
 * server-validated generations). Rendered only in LIVE branches — never in
 * DEMO (the demo wizard owns its simulated flow).
 *
 * - Inputs live in local state: a failed request never wipes them.
 * - ?brief=<id> resumes from server truth (answering/reviewing/done by
 *   persisted status); unknown ids surface the 404 honestly.
 * - No optimistic rows: questions, brief and plan appear only from 2xx.
 */

function StepShell({
  step,
  of,
  children,
}: {
  step: string;
  of: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="p-5">
      <p className="text-[11.5px] font-bold uppercase tracking-wide text-ink-400">
        {step} · {of}
      </p>
      <div className="mt-3 flex flex-col gap-3">{children}</div>
    </Card>
  );
}

function BusyNote({ text }: { text: string }) {
  return (
    <p className="flex items-center gap-2 text-[13px] font-medium text-brand-600 dark:text-sky-300">
      <Loader2 className="h-4 w-4 animate-spin" /> {text}
    </p>
  );
}

function ErrorNote({ message, onRetry }: { message: string | null; onRetry: () => void }) {
  if (!message) return null;
  return (
    <div className="flex flex-col gap-2">
      <p role="alert" className="text-[13px] leading-5 text-red-600 dark:text-red-300">
        {message}
      </p>
      <div>
        <GhostButton onClick={onRetry}>Retry</GhostButton>
      </div>
    </div>
  );
}

function SummaryList({ title, items }: { title: string; items: unknown }) {
  const rows = Array.isArray(items) ? items.filter((v) => typeof v === "string") : [];
  if (rows.length === 0) return null;
  return (
    <div>
      <h4 className="text-[12.5px] font-bold">{title}</h4>
      <ul className="mt-1 flex list-disc flex-col gap-1 pl-5 text-[13px] leading-5 text-ink-700 dark:text-slate-300">
        {(rows as string[]).map((row, i) => (
          <li key={i}>{row}</li>
        ))}
      </ul>
    </div>
  );
}

function BriefReview({ brief }: { brief: ApiBrief }) {
  const summary = (brief.summary ?? {}) as Record<string, unknown>;
  const goal = typeof summary.goal === "string" ? summary.goal : brief.goal;
  return (
    <div className="flex flex-col gap-3">
      <div>
        <h4 className="text-[12.5px] font-bold">Goal</h4>
        <p className="mt-0.5 text-[13.5px] font-medium">{goal}</p>
      </div>
      <SummaryList title="Context" items={summary.context} />
      {typeof summary.desired_outcome === "string" && (
        <div>
          <h4 className="text-[12.5px] font-bold">Desired outcome</h4>
          <p className="mt-0.5 text-[13px] leading-5 text-ink-700 dark:text-slate-300">
            {summary.desired_outcome}
          </p>
        </div>
      )}
      <SummaryList title="Success criteria" items={summary.success_criteria} />
      <SummaryList title="Assumptions" items={summary.assumptions} />
      <SummaryList title="Open questions" items={summary.open_questions} />
    </div>
  );
}

function phaseStep(phase: IntakePhase): { step: string; of: string } {
  switch (phase) {
    case "answering":
    case "submitting":
      return { step: "Step 2", of: "Answers" };
    case "reviewing":
      return { step: "Step 3", of: "Brief review" };
    case "planning":
    case "done":
      return { step: "Step 4", of: "Plan" };
    default:
      return { step: "Step 1", of: "Goal" };
  }
}

export function LiveIntakeView({ businessId }: { businessId: string }) {
  const record = useLiveIntake((s) => s.records[businessId]);
  const start = useLiveIntake((s) => s.start);
  const answer = useLiveIntake((s) => s.answer);
  const plan = useLiveIntake((s) => s.plan);
  const resume = useLiveIntake((s) => s.resume);
  const clear = useLiveIntake((s) => s.clear);
  const resumeId = useSearchParams().get("brief");

  const [goal, setGoal] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [clientError, setClientError] = useState<string | null>(null);

  const phase = record?.phase ?? "idle";
  const brief = record?.brief ?? null;
  const busy = phase === "asking" || phase === "submitting" || phase === "planning";

  useEffect(() => {
    if (resumeId) void resume(businessId, resumeId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId, resumeId]);

  const { step, of } = phaseStep(phase);

  const doStart = () => {
    if (busy) return;
    if (!goal.trim()) {
      setClientError("Describe the goal first.");
      return;
    }
    setClientError(null);
    void start(businessId, goal.trim());
  };

  const doAnswer = () => {
    if (busy || !brief) return;
    const answers = brief.questions.map((q) => ({
      question_id: q.id,
      text: (drafts[q.id] ?? "").trim(),
    }));
    const blank = answers.find((a) => !a.text);
    if (blank) {
      setClientError("Answer every question before continuing.");
      return;
    }
    setClientError(null);
    void answer(businessId, brief.id, answers);
  };

  const doPlan = () => {
    if (busy || !brief) return;
    void plan(businessId, brief.id);
  };

  const startOver = () => {
    clear(businessId);
    setDrafts({});
    setClientError(null);
  };

  return (
    <>
      <PageHeader
        title="New project"
        sub="State a goal — the Team Lead asks what matters, then plans the work."
        badge={<LiveBadge />}
      />
      <StepShell step={step} of={of}>
        {(phase === "idle" || (phase === "error" && !brief)) && (
          <>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-semibold">What do you want to accomplish?</span>
              <textarea
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                placeholder="I want to launch a fitness studio in Krakow."
                rows={3}
                className="rounded-[10px] border border-mist-200 bg-transparent p-3 text-[13.5px] outline-none focus:border-brand-500 dark:border-white/10"
              />
            </label>
            {clientError && (
              <p role="alert" className="text-[12.5px] text-red-600 dark:text-red-300">
                {clientError}
              </p>
            )}
            <ErrorNote
              message={record?.phase === "error" ? record.message : null}
              onRetry={doStart}
            />
            <div>
              <PrimaryButton onClick={doStart}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                {busy ? "Asking…" : "Ask the Team Lead"}
                {!busy && <ArrowRight className="h-4 w-4" />}
              </PrimaryButton>
            </div>
          </>
        )}

        {phase === "asking" && <BusyNote text="Team Lead is drafting clarifying questions…" />}

        {(phase === "answering" || (phase === "error" && brief && brief.status === "questions_ready")) &&
          brief && (
            <>
              <p className="text-[13px] text-ink-500">
                Answer {brief.questions.length} question{brief.questions.length === 1 ? "" : "s"}{" "}
                to shape the brief.
              </p>
              {brief.questions.map((q) => (
                <div key={q.id} className="flex flex-col gap-1.5">
                  <p className="text-[13.5px] font-semibold">{q.text}</p>
                  {q.options && q.options.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {q.options.map((opt) => (
                        <button
                          key={opt}
                          type="button"
                          onClick={() => setDrafts((d) => ({ ...d, [q.id]: opt }))}
                          aria-pressed={(drafts[q.id] ?? "") === opt}
                          className={cn(
                            "rounded-full px-2.5 py-1 text-[12px] font-medium",
                            (drafts[q.id] ?? "") === opt
                              ? "bg-brand-500 text-white"
                              : "border border-mist-200 text-ink-500 dark:border-white/10",
                          )}
                        >
                          {opt}
                        </button>
                      ))}
                    </div>
                  )}
                  <input
                    value={drafts[q.id] ?? ""}
                    onChange={(e) => setDrafts((d) => ({ ...d, [q.id]: e.target.value }))}
                    placeholder="Your answer"
                    aria-label={q.text}
                    className="h-10 rounded-[10px] border border-mist-200 bg-transparent px-3 text-[13.5px] outline-none focus:border-brand-500 dark:border-white/10"
                  />
                </div>
              ))}
              {clientError && (
                <p role="alert" className="text-[12.5px] text-red-600 dark:text-red-300">
                  {clientError}
                </p>
              )}
              <ErrorNote
                message={record?.phase === "error" ? record.message : null}
                onRetry={doAnswer}
              />
              <div className="flex gap-2">
                <PrimaryButton onClick={doAnswer}>
                  {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                  {busy ? "Writing brief…" : "Review brief"}
                </PrimaryButton>
                <GhostButton onClick={startOver}>Start over</GhostButton>
              </div>
            </>
          )}

        {phase === "submitting" && <BusyNote text="Writing the brief from your answers…" />}

        {(phase === "reviewing" || (phase === "error" && brief && brief.status === "brief_ready")) &&
          brief && (
            <>
              <BriefReview brief={brief} />
              <ErrorNote
                message={record?.phase === "error" ? record.message : null}
                onRetry={doPlan}
              />
              <div className="flex gap-2">
                <PrimaryButton onClick={doPlan}>
                  {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                  {busy ? "Planning…" : "Build plan & project"}
                </PrimaryButton>
                <GhostButton onClick={startOver}>Start over</GhostButton>
              </div>
            </>
          )}

        {phase === "planning" && <BusyNote text="Turning the approved brief into a project…" />}

        {phase === "done" && record?.plan && (
          <>
            <p className="text-[13.5px] font-semibold text-emerald-700 dark:text-emerald-300">
              Planned — server confirmed.
            </p>
            <p className="text-[13px] text-ink-500">
              Project “{record.plan.project.title}” with {record.plan.tasks.length} task
              {record.plan.tasks.length === 1 ? "" : "s"}
              {record.plan.approvals.length > 0 &&
                ` and ${record.plan.approvals.length} approval gate${record.plan.approvals.length === 1 ? "" : "s"}`}{" "}
              is ready. Tasks never start on their own — run them from the project page.
            </p>
            <div className="flex gap-2">
              <Link
                href={`/projects/${record.plan.project.id}?brief=${record.plan.briefId}`}
                className="inline-flex h-9 items-center gap-1.5 rounded-[10px] bg-gradient-to-r from-brand-500 to-sky-400 px-3.5 text-[13px] font-semibold text-white shadow-card transition-opacity hover:opacity-90"
              >
                Open project <ArrowRight className="h-4 w-4" />
              </Link>
              <GhostButton onClick={startOver}>Plan another</GhostButton>
            </div>
          </>
        )}

        {phase === "done" && !record?.plan && brief?.projectId && (
          <>
            <p className="text-[13.5px] font-semibold text-emerald-700 dark:text-emerald-300">
              Resumed a planned brief.
            </p>
            <div>
              <Link
                href={`/projects/${brief.projectId}?brief=${brief.id}`}
                className="inline-flex h-9 items-center gap-1.5 rounded-[10px] bg-gradient-to-r from-brand-500 to-sky-400 px-3.5 text-[13px] font-semibold text-white shadow-card transition-opacity hover:opacity-90"
              >
                Open project <ArrowRight className="h-4 w-4" />
              </Link>
            </div>
          </>
        )}

      </StepShell>
    </>
  );
}
