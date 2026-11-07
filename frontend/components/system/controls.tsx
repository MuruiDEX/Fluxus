import * as React from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * Shared control primitives (Slice 3A).
 *
 * One visual definition per control, so buttons and links cannot drift apart.
 * Every class here stays on the existing design tokens: brand gradients for
 * primary actions, `line`/`surface`/`fg*` for form chrome, and the global
 * focus-visible rule in `app/globals.css` for keyboard focus. Nothing in this
 * file is interactive stateful code, so it renders in Server Components too.
 */

export type ButtonVariant = "primary" | "ghost" | "destructive";
export type ButtonSize = "sm" | "md";

/** Shape shared by every button variant; variant classes add the identity. */
const buttonBaseClass =
  "relative inline-flex items-center justify-center gap-1.5 rounded-control text-body-sm transition duration-150 disabled:cursor-not-allowed disabled:opacity-60";

const buttonVariantClass: Record<ButtonVariant, string> = {
  // Existing primary identity: brand → sky gradient, white label, soft shadow.
  primary:
    "bg-gradient-to-r from-brand-500 to-sky-400 font-semibold text-white shadow-card hover:opacity-90 active:opacity-80",
  // Existing secondary/ghost identity, unchanged from the pre-slice buttons.
  ghost:
    "border border-line bg-surface font-medium text-ink-700 hover:bg-mist-100 active:bg-mist-200 dark:bg-white/5 dark:text-slate-200 dark:hover:bg-white/10",
  // A destructive action needs its own identity; token-based so it follows
  // both themes instead of hard-coding red values.
  destructive:
    "border border-danger/40 bg-danger-soft font-semibold text-danger hover:opacity-90 active:opacity-80",
};

const buttonSizeClass: Record<ButtonSize, string> = {
  sm: "h-8 px-2.5",
  md: "h-9 px-3.5",
};

const iconButtonSizeClass: Record<ButtonSize, string> = {
  sm: "h-8 w-8 p-0",
  md: "h-9 w-9 p-0",
};

export function buttonClass({
  variant = "primary",
  size = "md",
  iconOnly = false,
  className,
}: {
  variant?: ButtonVariant;
  size?: ButtonSize;
  iconOnly?: boolean;
  className?: string;
} = {}): string {
  return cn(
    buttonBaseClass,
    buttonVariantClass[variant],
    iconOnly ? iconButtonSizeClass[size] : buttonSizeClass[size],
    className,
  );
}

export type ButtonBaseProps = Omit<
  React.ButtonHTMLAttributes<HTMLButtonElement>,
  "disabled"
> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  disabled?: boolean;
  /** Busy state: disables the control and overlays a spinner without reflow. */
  loading?: boolean;
  className?: string;
};

export type ButtonProps =
  | (ButtonBaseProps & { iconOnly?: false; label?: string })
  | (ButtonBaseProps & { iconOnly: true; /** Accessible name for the icon. */ label: string });

/**
 * Native `<button>` with consistent variants. Defaults to `type="button"` so a
 * button never submits a form by accident; call sites opt in to `submit`.
 *
 * While `loading`, the label stays in the layout (visually hidden) and the
 * spinner is positioned over it, so the control keeps its width.
 */
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "primary",
    size = "md",
    iconOnly = false,
    loading = false,
    label,
    className,
    children,
    disabled,
    type = "button",
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      {...rest}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      aria-label={rest["aria-label"] ?? label}
      className={cn(buttonClass({ variant, size, iconOnly }), className)}
    >
      <span
        className={cn(
          "inline-flex items-center justify-center gap-1.5",
          loading && "invisible",
        )}
      >
        {children}
      </span>
      {loading && (
        // Absolute so the spinner does not add width or height to the button.
        <Loader2 aria-hidden className="absolute h-4 w-4 animate-spin" />
      )}
    </button>
  );
});

/** Convenience wrapper for an icon-only action; `label` is required. */
export const IconButton = React.forwardRef<
  HTMLButtonElement,
  Omit<ButtonBaseProps, "iconOnly"> & { label: string }
>(function IconButton({ label, ...props }, ref) {
  return <Button ref={ref} iconOnly label={label} {...props} />;
});

/** Shared form-chrome base: tokens give light/dark and disabled/invalid states. */
const controlBaseClass =
  "w-full rounded-control border border-line bg-surface text-body-sm text-fg transition-colors placeholder:text-fg-subtle disabled:cursor-not-allowed disabled:bg-disabled-bg disabled:text-disabled-fg aria-invalid:border-danger";

export const inputClass = cn(controlBaseClass, "h-10 px-3");

export const textareaClass = cn(controlBaseClass, "min-h-20 px-3 py-2");

export const selectClass = cn(controlBaseClass, "h-10 px-3 pr-9");

export const checkboxClass =
  "h-4 w-4 shrink-0 cursor-pointer rounded border-line accent-brand disabled:cursor-not-allowed disabled:opacity-60";

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;
export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>;
export type SelectProps = React.SelectHTMLAttributes<HTMLSelectElement>;
export type CheckboxProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, "type">;

export const Input = React.forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, ...props },
  ref,
) {
  return <input ref={ref} className={cn(inputClass, className)} {...props} />;
});

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, ...props },
  ref,
) {
  return <textarea ref={ref} className={cn(textareaClass, className)} {...props} />;
});

export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, children, ...props },
  ref,
) {
  return (
    <select ref={ref} className={cn(selectClass, className)} {...props}>
      {children}
    </select>
  );
});

/**
 * Native checkbox. The visible label belongs to the caller (usually a
 * `FormField`), so the control keeps its native semantics and event behavior.
 */
export const Checkbox = React.forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { className, ...props },
  ref,
) {
  return <input ref={ref} type="checkbox" className={cn(checkboxClass, className)} {...props} />;
});
