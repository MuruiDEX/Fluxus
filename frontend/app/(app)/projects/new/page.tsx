"use client";

import { Suspense, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowLeft, ArrowRight, Check, Sparkles } from "lucide-react";
import { Avatar } from "@/components/system/badges";
import { Card, GhostButton, PageHeader, PrimaryButton } from "@/components/system/ui";
import { ProgressBar } from "@/components/system/badges";
import type { LeadAnswers } from "@/lib/sim/teamLead";
import { buildBriefSummary, extractSignals, nextQuestion } from "@/lib/sim/teamLead";
import { hireListing } from "@/lib/hiring";
import { useActivity, useAgents, useProjects } from "@/lib/store/app";
import { useSession } from "@/lib/store/auth";
import { isLiveContext } from "@/lib/store/live";
import { LiveIntakeView } from "@/components/system/IntakeWizard";

const missions = [
  "Launch Business",
  "Get More Customers",
  "Build Website",
  "Automate Business",
  "Analyze Competitors",
  "Explore Opportunities",
];

let projectSeq = 1;

function Wizard() {
  const params = useSearchParams();
  const router = useRouter();
  const initialGoal = params.get("goal") ?? "";
  const [step, setStep] = useState<"goal" | "questions" | "brief">(initialGoal ? "questions" : "goal");
  const [goal, setGoal] = useState(initialGoal || "I want to launch a fitness studio in Krakow.");
  const [answers, setAnswers] = useState<LeadAnswers>({});
  const [draft, setDraft] = useState("");
  const [hiredSeo, setHiredSeo] = useState(false);

  const detected = useMemo(() => extractSignals(goal), [goal]);
  const question = useMemo(
    () => (step === "questions" ? nextQuestion(goal, answers) : null),
    [step, goal, answers],
  );
  const brief = useMemo(() => buildBriefSummary(goal, answers), [goal, answers]);

  const { addProject } = useProjects();
  const agents = useAgents((s) => s.agents);
  const push = useActivity((s) => s.log);

  const answer = () => {
    if (!question || !draft.trim()) return;
    const key = question.key === "extra" ? "extra" : question.key;
    setAnswers((a) => ({ ...a, [key]: draft.trim() }));
    setDraft("");
  };

  const launch = () => {
    projectSeq += 1;
    const id = `proj_custom_${projectSeq}`;
    addProject(
      {
        id,
        businessId: "biz_northstar",
        title: goal.length > 48 ? `${goal.slice(0, 48)}…` : goal,
        goal,
        status: "in_progress",
        progress: 8,
        budget: 20000,
        aiBudget: 20,
        aiSpent: 0.4,
        deadline: "30 Aug 2026",
        teamIds: ["ag_lead", "ag_market", "ag_comp", "ag_cfo", "ag_brand"],
        milestones: [
          { label: "Market research", done: false },
          { label: "Brand identity", done: false },
          { label: "Website and booking system", done: false },
          { label: "Launch", done: false },
        ],
      },
      [
        { id: `${id}_research`, businessId: "biz_northstar", projectId: id, title: "Market Research", ownerAgentId: "ag_market", status: "working", progress: 12, dependsOn: [], costAccrued: 0.2, etaMinutes: 14, summary: "Team Lead spun up from your goal." },
        { id: `${id}_brand`, businessId: "biz_northstar", projectId: id, title: "Brand Identity", ownerAgentId: "ag_brand", status: "working", progress: 5, dependsOn: [], costAccrued: 0.2, etaMinutes: 20, summary: "First concepts after audience brief." },
      ],
    );
    push({ at: "now", agentName: "Team Lead", text: `Assembled team for: ${goal}`, kind: "system" });
    router.push(`/projects/${id}`);
  };

  return (
    <>
      <PageHeader title="New Project" sub="Team Lead turns your goal into a brief, a team and a plan." />
      <div className="grid gap-3 xl:grid-cols-[1fr_320px]">
        <Card className="p-6">
          <AnimatePresence mode="wait">
            {step === "goal" && (
              <motion.div key="goal" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                <h2 className="text-center text-[22px] font-bold">What do you want to build?</h2>
                <p className="mt-1 text-center text-[13px] text-ink-500">Your AI company will handle the rest.</p>
                <div className="mx-auto mt-4 flex max-w-lg items-center gap-2 rounded-2xl border border-mist-200 p-2 pl-4 focus-within:border-brand-500 dark:border-white/10">
                  <input
                    value={goal}
                    onChange={(e) => setGoal(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && goal.trim() && setStep("questions")}
                    className="h-10 flex-1 bg-transparent text-[14px] outline-none"
                    placeholder="I want to launch a fitness studio in Krakow."
                  />
                  <button
                    onClick={() => goal.trim() && setStep("questions")}
                    className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-r from-brand-500 to-sky-400 text-white"
                    aria-label="Continue"
                  >
                    <ArrowRight className="h-4 w-4" />
                  </button>
                </div>
                <p className="mt-6 text-center text-[12.5px] text-ink-400">Or choose a mission:</p>
                <div className="mx-auto mt-2 grid max-w-lg grid-cols-2 gap-2 sm:grid-cols-3">
                  {missions.map((m) => (
                    <button
                      key={m}
                      onClick={() => { setGoal(m); setStep("questions"); }}
                      className="rounded-xl border border-mist-200 p-3 text-[12.5px] font-medium transition-colors hover:border-brand-200 hover:bg-brand-50 dark:border-white/10 dark:hover:bg-white/5"
                    >
                      {m}
                    </button>
                  ))}
                </div>
              </motion.div>
            )}

            {step === "questions" && question && (
              <motion.div key={question.key} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                <p className="flex items-center gap-2 text-[12px] font-semibold text-brand-500">
                  <Sparkles className="h-3.5 w-3.5" /> TEAM LEAD
                  {Object.keys(detected).length > 0 && (
                    <span className="font-normal text-ink-400">
                      · detected {Object.values(detected).filter(Boolean).join(" · ")}
                    </span>
                  )}
                </p>
                <h2 className="mt-2 text-[20px] font-bold">{question.title}</h2>
                <p className="mt-1 text-[13px] text-ink-500">{question.hint}</p>
                {question.options && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {question.options.map((o) => (
                      <button
                        key={o}
                        onClick={() => setDraft(o)}
                        className={`rounded-full border px-3 py-1.5 text-[13px] font-medium ${draft === o ? "border-brand-500 bg-brand-50 text-brand-600" : "border-mist-200 hover:border-brand-200"}`}
                      >
                        {o}
                      </button>
                    ))}
                  </div>
                )}
                <div className="mt-3 flex items-center gap-2 rounded-2xl border border-mist-200 p-2 pl-4 focus-within:border-brand-500 dark:border-white/10">
                  <input
                    autoFocus
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && answer()}
                    className="h-10 flex-1 bg-transparent text-[14px] outline-none"
                    placeholder={question.placeholder}
                  />
                  <button onClick={answer} className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-r from-brand-500 to-sky-400 text-white" aria-label="Answer">
                    <ArrowRight className="h-4 w-4" />
                  </button>
                </div>
                <button onClick={() => setStep("brief")} className="mt-3 text-[12.5px] font-semibold text-ink-400 hover:text-brand-500">
                  Skip — build the brief with what you know →
                </button>
              </motion.div>
            )}

            {step === "questions" && !question && (
              <motion.div key="ready" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                <p className="text-[13px] text-ink-500">Got it — no more questions. Here is your brief.</p>
                <PrimaryButton className="mt-3" onClick={() => setStep("brief")}>
                  Review brief <ArrowRight className="h-4 w-4" />
                </PrimaryButton>
              </motion.div>
            )}

            {step === "brief" && (
              <motion.div key="brief" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
                <button onClick={() => setStep("questions")} className="flex items-center gap-1 text-[12.5px] font-semibold text-ink-400 hover:text-brand-500">
                  <ArrowLeft className="h-3.5 w-3.5" /> Back
                </button>
                <h2 className="mt-2 text-[20px] font-bold">Business brief</h2>
                <div className="mt-3 flex flex-col gap-1.5">
                  {brief.map((line) => (
                    <p key={line} className="flex items-start gap-2 rounded-xl bg-mist-50 px-3 py-2 text-[13px] dark:bg-white/5">
                      <Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" /> {line}
                    </p>
                  ))}
                </div>
                <h3 className="mb-2 mt-5 text-[14px] font-bold">Recommended team</h3>
                <div className="grid gap-2 sm:grid-cols-2">
                  {agents.filter((a) => ["ag_lead", "ag_market", "ag_cfo", "ag_brand"].includes(a.id)).map((a) => (
                    <div key={a.id} className="flex items-center gap-2.5 rounded-xl border border-mist-200 p-2.5 dark:border-white/10">
                      <Avatar name={a.name} hue={a.avatarHue} size={30} />
                      <div><p className="text-[13px] font-semibold">{a.role}</p><p className="text-[11.5px] text-ink-400">{a.responsibilities[0]}</p></div>
                    </div>
                  ))}
                </div>
                <div className="mt-2 flex items-center gap-2.5 rounded-xl border border-dashed border-brand-200 bg-brand-50/50 p-3 dark:border-brand-500/30 dark:bg-brand-500/10">
                  <Avatar name="Sage" hue={95} size={30} />
                  <div className="flex-1">
                    <p className="text-[13px] font-semibold">Gap: SEO Specialist AI <span className="ml-1 rounded bg-emerald-100 px-1.5 py-px text-[10px] font-bold text-emerald-700">RECOMMENDED</span></p>
                    <p className="text-[11.5px] text-ink-500">Local search will decide this launch. $19/mo · 4.8★</p>
                  </div>
                  {!hiredSeo ? (
                    <button
                      onClick={() => { hireListing("seo-specialist"); setHiredSeo(true); }}
                      className="h-8 rounded-lg bg-brand-500 px-3 text-[12.5px] font-semibold text-white hover:opacity-90"
                    >
                      Hire
                    </button>
                  ) : (
                    <span className="flex items-center gap-1 text-[12.5px] font-bold text-emerald-600"><Check className="h-4 w-4" /> Hired</span>
                  )}
                </div>
                <div className="mt-4">
                  <div className="mb-1 flex justify-between text-[12px] text-ink-500"><span>Estimated AI cost: $11–14 of $20 budget</span><span>Plan ready</span></div>
                  <ProgressBar value={65} />
                </div>
                <div className="mt-4 flex gap-2">
                  <PrimaryButton onClick={launch}>Build My AI Team & Launch <ArrowRight className="h-4 w-4" /></PrimaryButton>
                  <GhostButton onClick={() => setStep("goal")}>Edit goal</GhostButton>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </Card>

        <Card className="h-fit p-4">
          <h3 className="text-[13px] font-bold">How Team Lead works</h3>
          <ol className="mt-2 flex flex-col gap-2 text-[12.5px] text-ink-500">
            {["Understands your goal", "Asks only what is missing", "Writes a structured brief", "Assembles the team", "Plans parallel work", "Requests approval for real-world actions"].map((s, i) => (
              <li key={s} className="flex gap-2">
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-50 text-[11px] font-bold text-brand-600 dark:bg-brand-500/15">{i + 1}</span>
                {s}
              </li>
            ))}
          </ol>
        </Card>
      </div>
    </>
  );
}

export default function NewProjectPage() {
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveIntakeView businessId={currentBusinessId} />;
  }
  return (
    <Suspense fallback={<p className="text-[13px] text-ink-400">Loading Team Lead…</p>}>
      <Wizard />
    </Suspense>
  );
}
