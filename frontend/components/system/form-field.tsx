import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * Shared form field scaffolding.
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

export const fieldLabelClass = "text-caption font-semibold text-fg";
export const fieldHelpClass = "text-label text-fg-muted";
export const fieldErrorClass = "text-label font-medium text-danger";

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

/**
 * Canonical field label. Standalone forms (including label-wrapped controls)
 * can share the exact label/required treatment used by `FormField`.
 */
export function FieldLabel({
  htmlFor,
  children,
  required = false,
  className,
}: {
  htmlFor?: string;
  children: ReactNode;
  required?: boolean;
  className?: string;
}) {
  return (
    <label htmlFor={htmlFor} className={cn(fieldLabelClass, className)}>
      {children}
      {required && <RequiredMark />}
    </label>
  );
}

/** Help text associated with a control through its `id`. */
export function FieldHelp({
  id,
  children,
  className,
}: {
  id?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <p id={id} className={cn(fieldHelpClass, className)}>
      {children}
    </p>
  );
}

/** Validation text announced to assistive technology as soon as it appears. */
export function FieldError({
  id,
  children,
  className,
}: {
  id?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <p id={id} role="alert" className={cn(fieldErrorClass, className)}>
      {children}
    </p>
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
      <FieldLabel htmlFor={id} required={required}>
        {label}
      </FieldLabel>
      {children({
        "aria-describedby": describedBy,
        "aria-invalid": showError ? true : undefined,
      })}
      {showDescription && <FieldHelp id={descriptionId}>{description}</FieldHelp>}
      {showError && <FieldError id={errorId}>{error}</FieldError>}
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
      <legend className={fieldLabelClass}>
        {legend}
        {required && <RequiredMark />}
      </legend>
      <div className="flex flex-col gap-1.5">{children}</div>
      {showDescription && <FieldHelp id={descriptionId}>{description}</FieldHelp>}
      {showError && <FieldError id={errorId}>{error}</FieldError>}
    </fieldset>
  );
}
