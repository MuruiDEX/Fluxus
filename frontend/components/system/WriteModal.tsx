"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Loader2, X } from "lucide-react";
import { GhostButton, PrimaryButton } from "@/components/system/ui";
import { cn } from "@/lib/cn";
import type { WritePhase } from "@/lib/store/writes";

/**
 * Shared management-write modal shell (Stage 7.2) — same overlay language
 * as the Brain demo modal: centered card, overlay/Esc close, autofocus.
 *
 * Unsaved-data guard: closing with dirty, unsent edits does not silently
 * drop them — the shell asks once inline ([Keep editing] / [Discard]).
 * While `phase === "saving"` the modal cannot be dismissed at all.
 */

export const inputClass =
  "h-10 w-full rounded-[10px] border border-mist-200 bg-transparent px-3 text-[13.5px] outline-none focus:border-brand-500 dark:border-white/10";

export const areaClass =
  "w-full rounded-[10px] border border-mist-200 bg-transparent p-3 text-[13.5px] outline-none focus:border-brand-500 dark:border-white/10";

export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[12px] font-semibold text-ink-700 dark:text-slate-200">{label}</span>
      {children}
      {hint && <span className="text-[11.5px] text-ink-400">{hint}</span>}
    </label>
  );
}

export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p role="alert" className="text-[12.5px] leading-5 text-red-600 dark:text-red-300">
      {message}
    </p>
  );
}

export function PhaseMessage({ phase, message }: { phase: WritePhase; message: string | null }) {
  if (phase === "saving") {
    return (
      <p className="flex items-center gap-1.5 text-[12.5px] font-medium text-brand-600 dark:text-sky-300">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Saving to server…
      </p>
    );
  }
  if (!message) return null;
  return (
    <p
      role={phase === "error" ? "alert" : "status"}
      className={cn(
        "text-[12.5px] leading-5",
        phase === "saved" && "text-emerald-700 dark:text-emerald-300",
        phase === "error" && "text-red-600 dark:text-red-300",
      )}
    >
      {message}
    </p>
  );
}

/** Parse a money text field: non-negative, max 2 decimals. Empty → omitted. */
export function parseMoneyInput(
  raw: string,
): { ok: true; value: string | undefined } | { ok: false; error: string } {
  const text = raw.trim();
  if (text === "") return { ok: true, value: undefined };
  if (!/^\d+(\.\d{1,2})?$/.test(text)) {
    return { ok: false, error: "Enter a non-negative amount with up to 2 decimals." };
  }
  return { ok: true, value: text };
}

/** Parse an integer text field within [min, max]. Empty → omitted. */
export function parseIntInput(
  raw: string,
  min: number,
  max: number,
  label: string,
): { ok: true; value: number | undefined } | { ok: false; error: string } {
  const text = raw.trim();
  if (text === "") return { ok: true, value: undefined };
  if (!/^\d+$/.test(text)) {
    return { ok: false, error: `${label} must be a whole number.` };
  }
  const value = Number(text);
  if (value < min || value > max) {
    return { ok: false, error: `${label} must be between ${min} and ${max}.` };
  }
  return { ok: true, value };
}

export function WriteModal({
  title,
  dirty,
  phase,
  onClose,
  children,
  footer,
}: {
  title: string;
  /** True when the form holds unsent edits (enables the discard guard). */
  dirty: boolean;
  phase: WritePhase;
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
}) {
  const [confirming, setConfirming] = useState(false);
  const saving = phase === "saving";
  const requestClose = () => {
    if (saving) return;
    if (dirty && !confirming) {
      setConfirming(true);
      return;
    }
    onClose();
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") requestClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirty, confirming, saving]);

  const firstField = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    firstField.current?.focus();
  }, []);

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-night-950/50 px-4 py-6"
        onClick={requestClose}
      >
        <motion.div
          initial={{ y: 12, scale: 0.98 }}
          animate={{ y: 0, scale: 1 }}
          exit={{ y: 8, scale: 0.98 }}
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-label={title}
          className="w-full max-w-md rounded-2xl border border-mist-200 bg-white p-5 shadow-pop dark:border-white/10 dark:bg-night-900"
        >
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-[15px] font-bold">{title}</h3>
            <button onClick={requestClose} disabled={saving} aria-label="Close">
              <X className="h-4 w-4 text-ink-400" />
            </button>
          </div>
          <div
            className="flex flex-col gap-2.5"
            ref={(el) => {
              if (el && !firstField.current) {
                const input = el.querySelector("input");
                if (input instanceof HTMLInputElement) {
                  firstField.current = input;
                  input.focus();
                }
              }
            }}
          >
            {children}
          </div>
          <div className="mt-4 flex flex-col gap-2">
            {confirming && dirty && !saving && (
              <div className="rounded-[10px] border border-amber-200 bg-amber-50 p-2.5 text-[12.5px] dark:border-amber-500/30 dark:bg-amber-500/10">
                <p className="font-medium text-amber-800 dark:text-amber-200">
                  Discard unsaved changes?
                </p>
                <div className="mt-2 flex gap-2">
                  <GhostButton className="h-8" onClick={() => setConfirming(false)}>
                    Keep editing
                  </GhostButton>
                  <GhostButton className="h-8" onClick={onClose}>
                    Discard
                  </GhostButton>
                </div>
              </div>
            )}
            <div className="flex gap-2">{footer}</div>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}

export function SaveFooter({
  phase,
  saveLabel,
  onSave,
  onClose,
}: {
  phase: WritePhase;
  saveLabel: string;
  onSave: () => void;
  onClose: () => void;
}) {
  const saving = phase === "saving";
  return (
    <>
      <PrimaryButton onClick={onSave} className={cn("flex-1 justify-center", saving && "opacity-70")}>
        {saving && <Loader2 className="h-4 w-4 animate-spin" />}
        {saving ? "Saving…" : saveLabel}
      </PrimaryButton>
      <GhostButton onClick={onClose}>Cancel</GhostButton>
    </>
  );
}
