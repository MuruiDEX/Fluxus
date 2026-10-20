"use client";

import { useState } from "react";
import { ArrowDown, ArrowUp, Pencil, Plus, Trash2 } from "lucide-react";
import {
  Field,
  FormError,
  PhaseMessage,
  SaveFooter,
  WriteModal,
  areaClass,
  inputClass,
  parseMoneyInput,
} from "@/components/system/WriteModal";
import { GhostButton, PrimaryButton } from "@/components/system/ui";
import { useLiveAgents } from "@/lib/store/live";
import { useLiveWrites } from "@/lib/store/writes";
import type { ProjectPatchInput, ProjectStatus } from "@/lib/api";
import type { Project } from "@/lib/domain/types";
import { cn } from "@/lib/cn";

/**
 * LIVE project editor (Stage 7.2) — drives PATCH .../projects/{id} through
 * the management-write store. Rendered only in LIVE branches, never DEMO.
 *
 * Only ProjectPatch allowlist fields exist here: aiSpent/progress have no
 * input and no representation in the request type by construction. Money is
 * validated client-side (server adjudicates); the form keeps its inputs on
 * failed requests and applies the server answer via the LIVE cache.
 */

const STATUSES: { value: ProjectStatus; label: string }[] = [
  { value: "planning", label: "Planning" },
  { value: "in_progress", label: "In Progress" },
  { value: "review", label: "In Review" },
  { value: "completed", label: "Completed" },
];

export interface MilestoneDraft {
  label: string;
  done: boolean;
}

/** Pure milestone list edits (unit-tested; the form below only renders). */
export function milestoneAdd(list: MilestoneDraft[], label: string): MilestoneDraft[] {
  const text = label.trim();
  if (!text) return list;
  return [...list, { label: text, done: false }];
}

export function milestoneRemove(list: MilestoneDraft[], index: number): MilestoneDraft[] {
  return list.filter((_, i) => i !== index);
}

export function milestoneMove(
  list: MilestoneDraft[],
  index: number,
  dir: -1 | 1,
): MilestoneDraft[] {
  const to = index + dir;
  if (to < 0 || to >= list.length) return list;
  const next = [...list];
  [next[index], next[to]] = [next[to], next[index]];
  return next;
}

export function milestoneToggle(list: MilestoneDraft[], index: number): MilestoneDraft[] {
  return list.map((m, i) => (i === index ? { ...m, done: !m.done } : m));
}

export function milestoneRename(
  list: MilestoneDraft[],
  index: number,
  label: string,
): MilestoneDraft[] {
  return list.map((m, i) => (i === index ? { ...m, label } : m));
}

function MilestoneEditor({
  value,
  onChange,
}: {
  value: MilestoneDraft[];
  onChange: (next: MilestoneDraft[]) => void;
}) {
  const [draft, setDraft] = useState("");
  return (
    <div className="flex flex-col gap-1.5">
      {value.map((m, i) => (
        <div key={`${i}-${m.label}`} className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => onChange(milestoneToggle(value, i))}
            aria-pressed={m.done}
            title={m.done ? "Mark open" : "Mark done"}
            className={cn(
              "h-8 w-8 shrink-0 rounded-lg border text-[13px] font-bold",
              m.done
                ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300"
                : "border-mist-200 text-ink-400 dark:border-white/10",
            )}
          >
            ✓
          </button>
          <input
            value={m.label}
            onChange={(e) => onChange(milestoneRename(value, i, e.target.value))}
            aria-label={`Milestone ${i + 1}`}
            className={cn(inputClass, "h-8 text-[12.5px]", m.done && "line-through opacity-60")}
          />
          <button
            type="button"
            onClick={() => onChange(milestoneMove(value, i, -1))}
            disabled={i === 0}
            aria-label="Move up"
            className="h-8 w-7 shrink-0 rounded-lg border border-mist-200 text-ink-400 disabled:opacity-30 dark:border-white/10"
          >
            <ArrowUp className="mx-auto h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => onChange(milestoneMove(value, i, 1))}
            disabled={i === value.length - 1}
            aria-label="Move down"
            className="h-8 w-7 shrink-0 rounded-lg border border-mist-200 text-ink-400 disabled:opacity-30 dark:border-white/10"
          >
            <ArrowDown className="mx-auto h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => onChange(milestoneRemove(value, i))}
            aria-label="Remove milestone"
            className="h-8 w-7 shrink-0 rounded-lg border border-mist-200 text-red-500 dark:border-white/10"
          >
            <Trash2 className="mx-auto h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      <div className="flex items-center gap-1.5">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              onChange(milestoneAdd(value, draft));
              setDraft("");
            }
          }}
          placeholder="New milestone label"
          aria-label="New milestone label"
          className={cn(inputClass, "h-8 text-[12.5px]")}
        />
        <button
          type="button"
          onClick={() => {
            onChange(milestoneAdd(value, draft));
            setDraft("");
          }}
          aria-label="Add milestone"
          className="h-8 w-8 shrink-0 rounded-lg bg-brand-500 text-white hover:opacity-90"
        >
          <Plus className="mx-auto h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

function ProjectEditForm({
  businessId,
  project,
  onClose,
}: {
  businessId: string;
  project: Project;
  onClose: () => void;
}) {
  const key = `${businessId}/${project.id}`;
  const record = useLiveWrites((s) => s.projects[key]);
  const patchProject = useLiveWrites((s) => s.patchProject);
  const clearProject = useLiveWrites((s) => s.clearProject);
  const agentsSlice = useLiveAgents();

  const [title, setTitle] = useState(project.title);
  const [goal, setGoal] = useState(project.goal);
  const [status, setStatus] = useState<ProjectStatus>(project.status);
  const [budget, setBudget] = useState(String(project.budget));
  const [aiBudget, setAiBudget] = useState(String(project.aiBudget));
  const [deadline, setDeadline] = useState(project.deadline);
  const [teamIds, setTeamIds] = useState<string[]>([...project.teamIds]);
  const [allowUnpricedAi, setAllowUnpricedAi] = useState<boolean>(
    project.allowUnpricedAi ?? false,
  );
  const [milestones, setMilestones] = useState<MilestoneDraft[]>(
    project.milestones.map((m) => ({ label: m.label, done: m.done })),
  );
  const [clientError, setClientError] = useState<string | null>(null);

  const phase = record?.phase ?? "idle";
  const saving = phase === "saving";
  const dirty =
    title !== project.title ||
    goal !== project.goal ||
    status !== project.status ||
    budget !== String(project.budget) ||
    aiBudget !== String(project.aiBudget) ||
    deadline !== project.deadline ||
    allowUnpricedAi !== (project.allowUnpricedAi ?? false) ||
    JSON.stringify(teamIds) !== JSON.stringify(project.teamIds) ||
    JSON.stringify(milestones) !== JSON.stringify(project.milestones);

  const close = () => {
    clearProject(businessId, project.id);
    onClose();
  };

  const save = () => {
    if (saving) return;
    if (!title.trim()) {
      setClientError("Title must be non-empty.");
      return;
    }
    const parsedBudget = parseMoneyInput(budget);
    if (!parsedBudget.ok) {
      setClientError(`Budget: ${parsedBudget.error}`);
      return;
    }
    const parsedAi = parseMoneyInput(aiBudget);
    if (!parsedAi.ok) {
      setClientError(`AI budget: ${parsedAi.error}`);
      return;
    }
    setClientError(null);
    const patch: ProjectPatchInput = {
      title: title.trim(),
      goal,
      status,
      deadline,
      teamIds,
      allowUnpricedAi,
      milestones: milestones
        .filter((m) => m.label.trim())
        .map((m) => ({ label: m.label.trim(), done: m.done })),
    };
    if (parsedBudget.value !== undefined) patch.budget = parsedBudget.value;
    if (parsedAi.value !== undefined) patch.aiBudget = parsedAi.value;
    void patchProject(businessId, project.id, patch);
  };

  const toggleTeam = (agentId: string) => {
    setTeamIds((prev) =>
      prev.includes(agentId) ? prev.filter((id) => id !== agentId) : [...prev, agentId],
    );
  };

  return (
    <WriteModal
      title="Edit project"
      dirty={dirty && phase !== "saved"}
      phase={phase}
      onClose={close}
      footer={
        phase === "saved" ? (
          <PrimaryButton onClick={close} className="flex-1 justify-center">
            Done
          </PrimaryButton>
        ) : (
          <SaveFooter phase={phase} saveLabel="Save changes" onSave={save} onClose={close} />
        )
      }
    >
      <Field label="Title">
        <input value={title} onChange={(e) => setTitle(e.target.value)} className={inputClass} />
      </Field>
      <Field label="Goal">
        <textarea value={goal} onChange={(e) => setGoal(e.target.value)} rows={3} className={areaClass} />
      </Field>
      <Field label="Status">
        <div className="flex flex-wrap gap-1.5">
          {STATUSES.map((s) => (
            <button
              key={s.value}
              type="button"
              onClick={() => setStatus(s.value)}
              aria-pressed={status === s.value}
              className={cn(
                "rounded-full px-3 py-1.5 text-[12.5px] font-medium",
                status === s.value
                  ? "bg-brand-500 text-white"
                  : "border border-mist-200 bg-white text-ink-500 dark:border-white/10 dark:bg-night-900",
              )}
            >
              {s.label}
            </button>
          ))}
        </div>
      </Field>
      <div className="grid grid-cols-2 gap-2.5">
        <Field label="Budget ($)" hint="Non-negative, up to 2 decimals.">
          <input
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            inputMode="decimal"
            className={inputClass}
          />
        </Field>
        <Field label="AI budget ($)" hint="Non-negative, up to 2 decimals.">
          <input
            value={aiBudget}
            onChange={(e) => setAiBudget(e.target.value)}
            inputMode="decimal"
            className={inputClass}
          />
        </Field>
      </div>
      <Field label="Deadline" hint="Display text, stored verbatim.">
        <input value={deadline} onChange={(e) => setDeadline(e.target.value)} className={inputClass} />
      </Field>
      <Field label="Team">
        {agentsSlice.status === "ready" ? (
          <div className="flex flex-col gap-1.5">
            {agentsSlice.agents.map((a) => (
              <label key={a.id} className="flex cursor-pointer items-center gap-2 text-[13px]">
                <input
                  type="checkbox"
                  checked={teamIds.includes(a.id)}
                  onChange={() => toggleTeam(a.id)}
                  className="h-4 w-4 accent-brand-500"
                />
                <span className="font-medium">{a.name}</span>
                <span className="text-ink-400">· {a.role}</span>
              </label>
            ))}
          </div>
        ) : agentsSlice.status === "error" ? (
          <p className="text-[12.5px] text-amber-700 dark:text-amber-300">
            Team list unavailable ({agentsSlice.error ?? "load failed"}) — current team is kept
            unchanged.
          </p>
        ) : (
          <p className="text-[12.5px] text-ink-400">Loading team…</p>
        )}
      </Field>
      <Field
        label="AI spending"
        hint="Best-effort AI calls without a configured price are refused unless enabled."
      >
        <label className="flex cursor-pointer items-center gap-2 text-[13px]">
          <input
            type="checkbox"
            checked={allowUnpricedAi}
            onChange={(e) => setAllowUnpricedAi(e.target.checked)}
            className="h-4 w-4 accent-brand-500"
          />
          <span className="font-medium">Allow unpriced AI calls</span>
        </label>
      </Field>
      <Field label="Milestones">
        <MilestoneEditor value={milestones} onChange={setMilestones} />
      </Field>
      <FormError message={clientError} />
      <PhaseMessage phase={phase} message={record?.message ?? null} />
    </WriteModal>
  );
}

export function ProjectEditButton({
  businessId,
  project,
}: {
  businessId: string;
  project: Project;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <GhostButton onClick={() => setOpen(true)}>
        <Pencil className="h-4 w-4" /> Edit
      </GhostButton>
      {open && (
        <ProjectEditForm businessId={businessId} project={project} onClose={() => setOpen(false)} />
      )}
    </>
  );
}
