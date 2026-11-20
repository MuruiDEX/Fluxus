"use client";

import * as RadixTooltip from "@radix-ui/react-tooltip";
import type { ReactElement, ReactNode } from "react";

/**
 * Shared tooltip on top of the Radix primitive that was already a project
 * dependency. Native `title` still exists on some elements, but it never
 * appears for keyboard users and cannot be styled; this one opens on focus,
 * closes on Escape, and stays in the semantic token system.
 *
 * Content is portalled and rendered only while open, so it cannot shift the
 * page it annotates.
 */
export function TooltipProvider({ children }: { children: ReactNode }) {
  return (
    <RadixTooltip.Provider delayDuration={350} skipDelayDuration={150}>
      {children}
    </RadixTooltip.Provider>
  );
}

export function Tooltip({
  label,
  children,
  side = "bottom",
}: {
  /** Short, plain-language description of the control. */
  label: string;
  /** A single element that can hold a ref (button, link, icon wrapper). */
  children: ReactElement;
  side?: "top" | "right" | "bottom" | "left";
}) {
  return (
    <RadixTooltip.Root>
      <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
      <RadixTooltip.Portal>
        <RadixTooltip.Content
          side={side}
          sideOffset={7}
          align="center"
          avoidCollisions
          collisionPadding={10}
          hideWhenDetached
          className="flux-tooltip z-50 max-w-56 rounded-lg border border-line-subtle bg-surface-inverse px-2 py-1 text-label font-medium text-fg-inverse shadow-pop"
        >
          {label}
          <RadixTooltip.Arrow className="flux-tooltip-arrow" width={10} height={5} />
        </RadixTooltip.Content>
      </RadixTooltip.Portal>
    </RadixTooltip.Root>
  );
}
