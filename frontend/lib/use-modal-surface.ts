"use client";

import { useEffect, type RefObject } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Shared modal/drawer surface behaviour: lock background scroll, keep Tab
 * inside the panel, and restore focus to the trigger on close.
 *
 * Escape is deliberately not handled here — each surface already owns its own
 * close semantics (the write modal has an unsaved-changes guard, for example).
 */
export function useModalSurface(
  surfaceRef: RefObject<HTMLElement | null>,
  active: boolean,
) {
  useEffect(() => {
    if (!active) return;
    const previous = document.activeElement;
    const { overflow, paddingRight } = document.body.style;
    // Compensate for the removed scrollbar so the layout does not jump.
    const scrollbar = window.innerWidth - document.documentElement.clientWidth;
    document.body.style.overflow = "hidden";
    if (scrollbar > 0) document.body.style.paddingRight = `${scrollbar}px`;

    const surface = surfaceRef.current;
    const first = surface?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !surfaceRef.current) return;
      const nodes = Array.from(surfaceRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (node) => node.offsetParent !== null || node === document.activeElement,
      );
      if (nodes.length === 0) return;
      const firstNode = nodes[0];
      const lastNode = nodes[nodes.length - 1];
      const current = document.activeElement;
      if (event.shiftKey && (current === firstNode || !surfaceRef.current.contains(current))) {
        event.preventDefault();
        lastNode.focus();
      } else if (!event.shiftKey && current === lastNode) {
        event.preventDefault();
        firstNode.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = overflow;
      document.body.style.paddingRight = paddingRight;
      if (previous instanceof HTMLElement && document.contains(previous)) previous.focus();
    };
  }, [active, surfaceRef]);
}
