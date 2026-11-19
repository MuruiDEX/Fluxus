"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { motion } from "framer-motion";
import { Loader2, X } from "lucide-react";
import {
  Button,
  IconButton,
  inputClass as canonicalInputClass,
  textareaClass as canonicalTextareaClass,
} from "@/components/system/controls";
import {
  FieldError,
  fieldHelpClass,
  fieldLabelClass,
} from "@/components/system/form-field";
import { cn } from "@/lib/cn";
import type { WritePhase } from "@/lib/store/writes";
import { useModalSurface } from "@/lib/use-modal-surface";
import { transition } from "@/lib/motion";

/**
 * Shared management-write modal shell (Stage 7.2) — same overlay language
 * as the Brain demo modal: centered card, overlay/Esc close, autofocus.
 *
 * Unsaved-data guard: closing with dirty, unsent edits does not silently
 * drop them — the shell asks once inline ([Keep editing] / [Discard]).
 * While `phase === "saving"` the modal cannot be dismissed at all.
 */

/**
 * Compatibility aliases for existing modal forms. The control styling itself
 * lives in `controls.tsx`, so these cannot drift from standalone inputs.
 */
export const inputClass = canonicalInputClass;
export const areaClass = canonicalTextareaClass;

/**
 * Modal field caption. `group` renders a real `role="group"` instead of a
 * `<label>`: a label wrapped around a set of chips would forward clicks to the
 * first chip and label the group with no control at all.
 */
export function Field({
  label,
  children,
  hint,
  group = false,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
  group?: boolean;
}) {
  const id = useId();
  if (group) {
    return (
      <div role="group" aria-labelledby={id} className="flex flex-col gap-1">
        <span id={id} className={fieldLabelClass}>
          {label}
        </span>
        {children}
        {hint && <span className={fieldHelpClass}>{hint}</span>}
      </div>
    );
  }
  return (
    <label className="flex flex-col gap-1">
      <span className={fieldLabelClass}>{label}</span>
      {children}
      {hint && <span className={fieldHelpClass}>{hint}</span>}
    </label>
  );
}

export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return <FieldError>{message}</FieldError>;
}

export function PhaseMessage({ phase, message }: { phase: WritePhase; message: string | null }) {
  if (phase === "saving") {
    return (
      <p role="status" className="flex items-center gap-1.5 text-caption font-medium text-brand">
        <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" /> Saving to server…
      </p>
    );
  }
  if (!message) return null;
  return (
    <p
      role={phase === "error" ? "alert" : "status"}
      className={cn(
        "text-caption leading-5",
        phase === "saved" && "text-success-fg",
        phase === "error" && "text-danger-fg",
        phase === "idle" && "text-fg-muted",
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
  onSubmit,
  children,
  footer,
}: {
  title: string;
  /** True when the form holds unsent edits (enables the discard guard). */
  dirty: boolean;
  phase: WritePhase;
  onClose: () => void;
  /** Enter in a single-line field saves; Ctrl/⌘+Enter in a textarea saves. */
  onSubmit?: () => void;
  children: ReactNode;
  footer: ReactNode;
}) {
  const [confirming, setConfirming] = useState(false);
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  const saving = phase === "saving";
  const requestClose = () => {
    if (saving) return;
    if (dirty && !confirming) {
      setConfirming(true);
      return;
    }
    onClose();
  };
  // Scroll lock, initial focus, Escape, focus containment and focus restore.
  useModalSurface(surfaceRef, true, { onEscape: requestClose });

  // The discard prompt is a state change inside the same dialog; announcing it
  // tells screen-reader users why the footer just changed.
  useEffect(() => {
    if (!confirming) return;
    const timer = window.setTimeout(() => {
      surfaceRef.current
        ?.querySelector<HTMLElement>("[data-discard-prompt]")
        ?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [confirming]);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex overflow-y-auto bg-overlay px-4 py-6 sm:px-6"
      onClick={requestClose}
    >
      <motion.div
        ref={surfaceRef}
        initial={{ y: 12, scale: 0.98, opacity: 0 }}
        animate={{ y: 0, scale: 1, opacity: 1 }}
        exit={{ y: 8, scale: 0.98, opacity: 0 }}
        transition={transition.overlay}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key !== "Enter" || event.nativeEvent.isComposing || !onSubmit || saving) return;
          const tag = (event.target as HTMLElement).tagName;
          if (tag !== "INPUT" && tag !== "TEXTAREA") return;
          if (tag === "TEXTAREA" && !(event.metaKey || event.ctrlKey)) return;
          event.preventDefault();
          onSubmit();
        }}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="m-auto w-full max-w-md rounded-card border border-line bg-surface p-5 text-fg shadow-pop"
      >
        <div className="mb-3 flex items-center justify-between gap-3 border-b border-line pb-2.5">
          <h3 className="text-heading-sm font-bold tracking-tight">{title}</h3>
          <IconButton
            variant="ghost"
            size="sm"
            label="Close"
            onClick={requestClose}
            disabled={saving}
            className="-mr-1"
          >
            <X className="h-4 w-4" />
          </IconButton>
        </div>
        <div className="flex flex-col gap-2.5">{children}</div>
        <div className="mt-4 flex flex-col gap-2">
          {confirming && dirty && !saving && (
            <div
              data-discard-prompt
              tabIndex={-1}
              className="rounded-control border border-warning-line bg-warning-soft p-2.5"
            >
              <p className="text-caption font-semibold text-warning-fg">
                Discard unsaved changes?
              </p>
              <div className="mt-2 flex gap-2">
                <Button size="sm" variant="secondary" onClick={() => setConfirming(false)}>
                  Keep editing
                </Button>
                <Button size="sm" variant="destructive" onClick={onClose}>
                  Discard
                </Button>
              </div>
            </div>
          )}
          <div className="flex gap-2">{footer}</div>
          {onSubmit && (
            <p className="text-micro text-fg-subtle">
              Enter saves · Esc closes
            </p>
          )}
        </div>
      </motion.div>
    </motion.div>
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
      <Button
        type="button"
        loading={saving}
        onClick={onSave}
        className="flex-1 justify-center"
      >
        {saving ? "Saving…" : saveLabel}
      </Button>
      <Button type="button" variant="secondary" onClick={onClose} disabled={saving}>
        Cancel
      </Button>
    </>
  );
}
