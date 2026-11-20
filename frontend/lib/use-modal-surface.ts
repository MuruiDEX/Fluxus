"use client";

import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [contenteditable]:not([contenteditable="false"]), audio[controls], video[controls], [tabindex]:not([tabindex="-1"])';

/** Form controls are the intent of a form dialog; a close button is not. */
const FORM_CONTROL =
  'input:not([type="hidden"]):not([disabled]), textarea:not([disabled]), select:not([disabled])';

/**
 * Background scroll is a document-level lock. Two surfaces can be mounted at
 * once (a drawer behind a dialog), so the lock is reference-counted: the last
 * surface to close restores the original inline styles, and an inner surface
 * closing early cannot unlock the page underneath it.
 */
let locks = 0;
let savedOverflow = "";
let savedPaddingRight = "";

/** Only the topmost surface owns Escape and focus containment. */
const surfaceStack: object[] = [];

type ModalSurfaceOptions = {
  /**
   * Escape is delegated to the surface because each one owns its close
   * semantics (the write modal has an unsaved-changes guard, for example).
   */
  onEscape?: () => void;
  /** Preferred initial focus, e.g. the field a dialog exists for. */
  initialFocus?: RefObject<HTMLElement | null>;
  /** Skip restoring focus to the trigger (rare; used when the trigger vanishes). */
  returnFocus?: boolean;
};

/**
 * Shared modal/drawer surface behaviour: lock background scroll, keep Tab
 * inside the panel, and restore focus to the trigger on close.
 *
 * Initial focus prefers an explicit target, then a `[data-autofocus]` node,
 * then the first form control, then the first focusable node. Without the
 * form-control step a form dialog opened on its own close button, which is the
 * one control a keyboard user should reach last.
 */
export function useModalSurface(
  surfaceRef: RefObject<HTMLElement | null>,
  active: boolean,
  options: ModalSurfaceOptions = {},
) {
  const { initialFocus, returnFocus = true } = options;
  // Kept in a ref so an inline callback does not re-run the focus effect.
  const onEscapeRef = useRef(options.onEscape);
  useEffect(() => {
    onEscapeRef.current = options.onEscape;
  }, [options.onEscape]);

  useEffect(() => {
    if (!active) return;
    const token = {};
    surfaceStack.push(token);
    const isTopmost = () => surfaceStack[surfaceStack.length - 1] === token;
    const previous = document.activeElement;
    const body = document.body;
    if (locks === 0) {
      savedOverflow = body.style.overflow;
      savedPaddingRight = body.style.paddingRight;
      // Compensate for the removed scrollbar so the layout does not jump.
      const scrollbar = window.innerWidth - document.documentElement.clientWidth;
      body.style.overflow = "hidden";
      if (scrollbar > 0) body.style.paddingRight = `${scrollbar}px`;
    }
    locks += 1;

    const surface = surfaceRef.current;
    const target =
      initialFocus?.current ??
      surface?.querySelector<HTMLElement>("[data-autofocus]") ??
      surface?.querySelector<HTMLElement>(FORM_CONTROL) ??
      surface?.querySelector<HTMLElement>(FOCUSABLE);
    target?.focus({ preventScroll: true });

    const onKeyDown = (event: KeyboardEvent) => {
      if (!isTopmost()) return;
      // An IME composition owns Escape (it cancels the candidate list).
      if (event.key === "Escape" && !event.isComposing && !event.defaultPrevented) {
        event.preventDefault();
        onEscapeRef.current?.();
        return;
      }
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
      } else if (!event.shiftKey && (current === lastNode || !surfaceRef.current.contains(current))) {
        event.preventDefault();
        firstNode.focus();
      }
    };

    // Pointer/programmatic focus cannot escape while this surface is topmost.
    const onFocusIn = (event: FocusEvent) => {
      const currentSurface = surfaceRef.current;
      const targetNode = event.target;
      if (
        !isTopmost() ||
        !currentSurface ||
        !(targetNode instanceof Node) ||
        currentSurface.contains(targetNode)
      ) {
        return;
      }
      currentSurface.querySelector<HTMLElement>(FOCUSABLE)?.focus({ preventScroll: true });
    };

    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("focusin", onFocusIn);
      const stackIndex = surfaceStack.lastIndexOf(token);
      if (stackIndex !== -1) surfaceStack.splice(stackIndex, 1);
      locks = Math.max(0, locks - 1);
      if (locks === 0) {
        body.style.overflow = savedOverflow;
        body.style.paddingRight = savedPaddingRight;
      }
      if (returnFocus && previous instanceof HTMLElement && document.contains(previous)) {
        previous.focus();
      }
    };
  }, [active, surfaceRef, initialFocus, returnFocus]);
}
