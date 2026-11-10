import * as React from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * Shared control primitives.
 *
 * One visual definition per control, so buttons and links cannot drift apart.
 * Every class here stays on the Slice 1 semantic tokens: `brand-solid` for
 * action fills, `fg*` for text, `line*` for borders, `focus` for keyboard
 * focus, `disabled-*` for inactive controls, and the canonical type scale.
 * Nothing in this file is interactive stateful code, so it renders in Server
 * Components too.
 */

export type ButtonVariant = "primary" | "secondary" | "outline" | "ghost" | "destructive";
export type ButtonSize = "sm" | "md" | "lg";

/** Shape shared by every button variant; variant classes add the identity. */
const buttonBaseClass =
  "relative inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-control text-body-sm transition-[background-color,border-color,color,box-shadow] duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus disabled:cursor-not-allowed disabled:border-disabled-line disabled:bg-disabled-bg disabled:text-disabled-fg disabled:opacity-60 disabled:shadow-none";

const buttonVariantClass: Record<ButtonVariant, string> = {
  // Solid action fill. Brand text/accent tokens are intentionally not used
  // here: text ink and action fill are separate semantic roles.
  primary:
    "border border-transparent bg-brand-solid font-semibold text-on-brand shadow-card hover:bg-brand-solid-hover active:bg-brand-solid-hover",
  // The historical GhostButton look: an elevated neutral surface.
  secondary:
    "border border-line bg-surface font-medium text-fg hover:bg-surface-hover active:bg-surface-sunken",
  // Outlined neutral action for secondary placement without a filled surface.
  outline:
    "border border-line bg-transparent font-medium text-fg hover:bg-surface-hover active:bg-surface-sunken",
  // Lowest-emphasis action: no border or fill until hover/focus.
  ghost:
    "border border-transparent bg-transparent font-medium text-fg-muted hover:bg-surface-hover hover:text-fg active:bg-surface-sunken",
  // Destructive actions keep their own status identity in both themes.
  destructive:
    "border border-danger-line bg-danger-soft font-semibold text-danger-fg hover:border-danger/60 active:opacity-80",
};

const buttonSizeClass: Record<ButtonSize, string> = {
  sm: "h-8 px-2.5",
  md: "h-9 px-3.5",
  lg: "h-11 px-4",
};

const iconButtonSizeClass: Record<ButtonSize, string> = {
  sm: "h-8 w-8 p-0",
  md: "h-9 w-9 p-0",
  lg: "h-11 w-11 p-0",
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

/** Shared form-chrome base: tokens give light/dark, focus, disabled, read-only and invalid states. */
const controlBaseClass =
  "w-full rounded-control border border-line bg-surface text-body-sm text-fg transition-colors placeholder:text-fg-subtle focus:border-focus read-only:cursor-default read-only:bg-surface-sunken read-only:text-fg-muted disabled:cursor-not-allowed disabled:border-disabled-line disabled:bg-disabled-bg disabled:text-disabled-fg aria-invalid:border-danger aria-invalid:ring-1 aria-invalid:ring-danger/30";

export const inputClass = cn(controlBaseClass, "h-10 px-3");

export const textareaClass = cn(controlBaseClass, "min-h-20 px-3 py-2");

export const selectClass = cn(controlBaseClass, "h-10 cursor-pointer px-3 pr-9");

export const checkboxClass =
  "h-4 w-4 shrink-0 cursor-pointer rounded border-line align-middle accent-brand-solid focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus disabled:cursor-not-allowed disabled:opacity-60 aria-invalid:outline aria-invalid:outline-2 aria-invalid:outline-offset-2 aria-invalid:outline-danger";

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
