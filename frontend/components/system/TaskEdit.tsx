"use client";

import { useState } from "react";
import { Pencil } from "lucide-react";
import {
  Field,
  FormError,
  PhaseMessage,
  SaveFooter,
  WriteModal,
  areaClass,
  inputClass,
  parseIntInput,
} from "@/components/system/WriteModal";
import { PrimaryButton } from "@/components/system/ui";
import { useLiveAgents } from "@/lib/store/live";
import { useLiveWrites } from "@/lib/store/writes";
import type { TaskPatchInput } from "@/lib/api";
import type { Task } from "@/lib/domain/types";

/**
 * LIVE task metadata editor (Stage 7.2) — drives
 * PATCH .../projects/{id}/tasks/{task_id} through the management-write
 * store. Rendered only on the LIVE Tasks page, never in DEMO and never on
 * the project-detail graph.
 *
 * Only TaskPatch allowlist fields exist here: status, progress, cost,
 * attempts, gates and the project link have no input and no representation
 * in the request type by construction — executor ownership is untouched.
 */

function TaskEditForm({
  businessId,
  task,
  onClose,
}: {
  businessId: string;
  task: Task;
  onClose: () => void;
}) {
  const key = `${businessId}/${task.projectId}/${task.id}`;
  const record = useLiveWrites((s) => s.tasks[key]);
  const patchTask = useLiveWrites((s) => s.patchTask);
  const clearTask = useLiveWrites((s) => s.clearTask);
  const agentsSlice = useLiveAgents();

  const [title, setTitle] = useState(task.title);
  const [summary, setSummary] = useState(task.summary);
  const [etaMinutes, setEtaMinutes] = useState(String(task.etaMinutes));
  const [ownerAgentId, setOwnerAgentId] = useState(task.ownerAgentId);
  const [gateAt, setGateAt] = useState(task.gateAt === undefined ? "" : String(task.gateAt));
  const [clientError, setClientError] = useState<string | null>(null);

  const phase = record?.phase ?? "idle";
  const saving = phase === "saving";
  const dirty =
    title !== task.title ||
    summary !== task.summary ||
    etaMinutes !== String(task.etaMinutes) ||
    ownerAgentId !== task.ownerAgentId ||
    gateAt !== (task.gateAt === undefined ? "" : String(task.gateAt));

  const close = () => {
    clearTask(businessId, task.projectId, task.id);
    onClose();
  };

  const save = () => {
    if (saving) return;
    if (!title.trim()) {
      setClientError("Title must be non-empty.");
      return;
    }
    const parsedEta = parseIntInput(etaMinutes, 0, 100000, "ETA minutes");
    if (!parsedEta.ok) {
      setClientError(parsedEta.error);
      return;
    }
    const parsedGate = parseIntInput(gateAt, 0, 100, "Gate at");
    if (!parsedGate.ok) {
      setClientError(parsedGate.error);
      return;
    }
    if (!ownerAgentId) {
      setClientError("Pick the owning agent from this business.");
      return;
    }
    const selected = agentsSlice.agents.find((a) => a.id === ownerAgentId);
    if (selected && !selected.hired) {
      setClientError("That agent is disabled — pick an active agent.");
      return;
    }
    setClientError(null);
    const patch: TaskPatchInput = {
      title: title.trim(),
      summary,
      ownerAgentId,
    };
    if (parsedEta.value !== undefined) patch.etaMinutes = parsedEta.value;
    if (parsedGate.value !== undefined) patch.gateAt = parsedGate.value;
    void patchTask(businessId, task.projectId, task.id, patch);
  };

  const agentsReady = agentsSlice.status === "ready";

  return (
    <WriteModal
      title="Edit task"
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
      <Field label="Summary">
        <textarea
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          rows={3}
          className={areaClass}
        />
      </Field>
      <div className="grid grid-cols-2 gap-2.5">
        <Field label="ETA (minutes)" hint="Whole number, 0 or more.">
          <input
            value={etaMinutes}
            onChange={(e) => setEtaMinutes(e.target.value)}
            inputMode="numeric"
            className={inputClass}
          />
        </Field>
        <Field label="Gate at (%)" hint="0–100. Empty keeps the server value.">
          <input
            value={gateAt}
            onChange={(e) => setGateAt(e.target.value)}
            inputMode="numeric"
            placeholder="100"
            className={inputClass}
          />
        </Field>
      </div>
      <Field label="Owner">
        {agentsReady ? (
          <select
            value={ownerAgentId}
            onChange={(e) => setOwnerAgentId(e.target.value)}
            className={inputClass}
          >
            <option value="">Select an agent…</option>
            {agentsSlice.agents
              .filter((a) => a.hired || a.id === task.ownerAgentId)
              .map((a) => (
                <option key={a.id} value={a.id} disabled={!a.hired}>
                  {a.name} · {a.role}
                  {!a.hired ? " (disabled)" : ""}
                </option>
              ))}
          </select>
        ) : agentsSlice.status === "error" ? (
          <p className="text-[12.5px] text-amber-700 dark:text-amber-300">
            Agent list unavailable ({agentsSlice.error ?? "load failed"}) — the current owner is
            kept unchanged.
          </p>
        ) : (
          <p className="text-[12.5px] text-ink-400">Loading agents…</p>
        )}
      </Field>
      <FormError message={clientError} />
      <PhaseMessage phase={phase} message={record?.message ?? null} />
    </WriteModal>
  );
}

export function TaskEditButton({ businessId, task }: { businessId: string; task: Task }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="flex h-7 items-center gap-1.5 rounded-lg border border-mist-200 px-2.5 text-[12px] font-medium text-ink-500 hover:bg-mist-100 dark:border-white/10 dark:hover:bg-white/5"
      >
        <Pencil className="h-3.5 w-3.5" /> Edit
      </button>
      {open && (
        <TaskEditForm businessId={businessId} task={task} onClose={() => setOpen(false)} />
      )}
    </>
  );
}
