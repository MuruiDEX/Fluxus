"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import {
  Field,
  FormError,
  PhaseMessage,
  SaveFooter,
  WriteModal,
  areaClass,
  inputClass,
} from "@/components/system/WriteModal";
import { GhostButton, PrimaryButton } from "@/components/system/ui";
import { useLiveWrites } from "@/lib/store/writes";
import type { BrainCategory } from "@/lib/api";
import { cn } from "@/lib/cn";

/**
 * LIVE manual Brain entry creator (Stage 7.2) — drives POST .../brain
 * through the management-write store. Rendered only in the LIVE Brain view,
 * never in DEMO (which keeps its local add-note flow).
 *
 * Categories are the frozen contract literals; the request carries only
 * category/title/body. Success is declared by the 201 answer (the list
 * reloads from the server and the message quotes the returned id) — never
 * by local state alone.
 */

const CATEGORIES: BrainCategory[] = [
  "company",
  "audience",
  "model",
  "brand",
  "goals",
  "decisions",
  "knowledge",
  "results",
];

function BrainCreateForm({ businessId, onClose }: { businessId: string; onClose: () => void }) {
  const record = useLiveWrites((s) => s.brains[businessId]);
  const createBrain = useLiveWrites((s) => s.createBrain);
  const clearBrain = useLiveWrites((s) => s.clearBrain);

  const [category, setCategory] = useState<BrainCategory>("knowledge");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [clientError, setClientError] = useState<string | null>(null);

  const phase = record?.phase ?? "idle";
  const saving = phase === "saving";
  const dirty = title.trim() !== "" || body.trim() !== "";

  const close = () => {
    clearBrain(businessId);
    onClose();
  };

  const save = () => {
    if (saving) return;
    if (!title.trim()) {
      setClientError("Title must be non-empty.");
      return;
    }
    setClientError(null);
    void createBrain(businessId, { category, title: title.trim(), body });
  };

  return (
    <WriteModal
      title="Add memory"
      dirty={dirty && phase !== "saved"}
      phase={phase}
      onClose={close}
      footer={
        phase === "saved" ? (
          <PrimaryButton onClick={close} className="flex-1 justify-center">
            Done
          </PrimaryButton>
        ) : (
          <SaveFooter phase={phase} saveLabel="Save to Business Brain" onSave={save} onClose={close} />
        )
      }
    >
      <Field label="Category">
        <div className="flex flex-wrap gap-1.5">
          {CATEGORIES.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setCategory(c)}
              aria-pressed={category === c}
              className={cn(
                "rounded-full px-2.5 py-1 text-[12px] font-medium capitalize",
                category === c
                  ? "bg-brand-500 text-white"
                  : "border border-mist-200 text-ink-500 dark:border-white/10",
              )}
            >
              {c}
            </button>
          ))}
        </div>
      </Field>
      <Field label="Title">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Title"
          className={inputClass}
        />
      </Field>
      <Field label="Memory">
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="What should the team remember?"
          rows={4}
          className={areaClass}
        />
      </Field>
      <FormError message={clientError} />
      <PhaseMessage phase={phase} message={record?.message ?? null} />
    </WriteModal>
  );
}

export function BrainCreateButton({ businessId }: { businessId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <GhostButton onClick={() => setOpen(true)}>
        <Plus className="h-4 w-4" /> Add note
      </GhostButton>
      {open && <BrainCreateForm businessId={businessId} onClose={() => setOpen(false)} />}
    </>
  );
}
