import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * Form field scaffolding (Slice 3A).
 *
 * The association contract is explicit rather than injected: the caller owns
 * the control `id` (so it is stable across server and client renders) and
 * receives the `aria-describedby` / `aria-invalid` values through a render
 * prop. No `cloneElement`, no DOM queries, no hooks — the whole module stays
 * usable from Server Components.
 */

/** Accessible attributes the caller spreads onto its control. */
export type FieldControlAria = {
  "aria-describedby"?: string;
  "aria-invalid"?: true;
};

const labelClass = "text-caption font-semibold text-fg";
const helpClass = "text-label text-fg-muted";
const errorClass = "text-label font-medium text-danger";

/** Empty strings and `false` are "no content", not a rendered helper/error. */
function hasContent(value: ReactNode): boolean {
  return value != null && value !== "" && value !== false;
}

function RequiredMark() {
  return (
    <>
      <span aria-hidden className="ml-0.5 text-danger">
        *
      </span>
      <span className="sr-only"> (required)</span>
    </>
  );
}

export type FormFieldProps = {
  /** Id of the control this field labels; also the base for help/error ids. */
  id: string;
  label: ReactNode;
  /**
   * Receives the ids/state that belong on the control. The control still gets
   * its own `id`, `required`, `name`, handlers, etc. from the caller.
   */
  children: (control: FieldControlAria) => ReactNode;
  /** Helper text rendered under the control and referenced by it. */
  description?: ReactNode;
  /** Validation message rendered under the control and referenced by it. */
  error?: ReactNode | null;
  /** Renders the visual/announced required marker; set `required` on the control too. */
  required?: boolean;
  className?: string;
};

export function FormField({
  id,
  label,
  children,
  description,
  error,
  required = false,
  className,
}: FormFieldProps) {
  const showDescription = hasContent(description);
  const showError = hasContent(error);
  const descriptionId = showDescription ? `${id}-description` : undefined;
  const errorId = showError ? `${id}-error` : undefined;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <label htmlFor={id} className={labelClass}>
        {label}
        {required && <RequiredMark />}
      </label>
      {children({
        "aria-describedby": describedBy,
        "aria-invalid": showError ? true : undefined,
      })}
      {showDescription && (
        <p id={descriptionId} className={helpClass}>
          {description}
        </p>
      )}
      {showError && (
        <p id={errorId} role="alert" className={errorClass}>
          {error}
        </p>
      )}
    </div>
  );
}

export type FieldGroupProps = {
  id: string;
  /** Group caption. `fieldset`/`legend` keeps checkboxes and radios grouped. */
  legend: ReactNode;
  children: ReactNode;
  description?: ReactNode;
  error?: ReactNode | null;
  required?: boolean;
  className?: string;
};

/**
 * Grouped controls (checkbox/radio sets). The description and error attach to
 * the `fieldset` itself, which stays outside the legend and the controls.
 */
export function FieldGroup({
  id,
  legend,
  children,
  description,
  error,
  required = false,
  className,
}: FieldGroupProps) {
  const showDescription = hasContent(description);
  const showError = hasContent(error);
  const descriptionId = showDescription ? `${id}-description` : undefined;
  const errorId = showError ? `${id}-error` : undefined;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <fieldset
      id={id}
      aria-describedby={describedBy}
      className={cn("flex min-w-0 flex-col gap-1", className)}
    >
      <legend className={labelClass}>
        {legend}
        {required && <RequiredMark />}
      </legend>
      <div className="flex flex-col gap-1.5">{children}</div>
      {showDescription && (
        <p id={descriptionId} className={helpClass}>
          {description}
        </p>
      )}
      {showError && (
        <p id={errorId} role="alert" className={errorClass}>
          {error}
        </p>
      )}
    </fieldset>
  );
}
