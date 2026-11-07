/**
 * Fluxus motion tokens — one source of truth for timing and easing.
 *
 * Rules (see docs): motion is short, transform/opacity only, never blocking,
 * and always recedes for `prefers-reduced-motion`. Reduced motion is enforced
 * globally by `<MotionConfig reducedMotion="user">` in `components/providers`,
 * which strips transform/layout animations while keeping opacity fades; the
 * CSS in `app/globals.css` disables the remaining decorative loops.
 *
 * Durations are seconds (Framer Motion unit). Keep them small: the longest
 * user-facing transition is 0.28s.
 */

import type { Transition, Variants } from "framer-motion";

export const duration = {
  /** Press/hover feedback. */
  instant: 0.1,
  /** Small state changes: chips, badges, toasts. */
  fast: 0.16,
  /** Default: cards, panels, section swaps. */
  base: 0.22,
  /** Deliberate: page enter, larger reveals. */
  slow: 0.28,
} as const;

export const easing = {
  /** Standard ease-out for entrances. */
  out: [0.16, 1, 0.3, 1],
  /** Symmetric in/out for layout moves. */
  inOut: [0.4, 0, 0.2, 1],
} as const;

export const transition = {
  fast: { duration: duration.fast, ease: easing.out },
  base: { duration: duration.base, ease: easing.out },
  slow: { duration: duration.slow, ease: easing.out },
  /** Interactive elements that should feel physical, not slow. */
  spring: { type: "spring", stiffness: 420, damping: 34, mass: 0.8 },
  /** Overlay/dialog springs are softer so they do not snap. */
  overlay: { type: "spring", stiffness: 300, damping: 30, mass: 0.9 },
} satisfies Record<string, Transition>;

/** Enter-only variants (App Router unmounts the old tree, so no exit pass). */
export const fadeIn: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: transition.base },
};

export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 6 },
  show: { opacity: 1, y: 0, transition: transition.slow },
};

export const scaleIn: Variants = {
  hidden: { opacity: 0, scale: 0.98 },
  show: { opacity: 1, scale: 1, transition: transition.overlay },
};

/**
 * Parent for staggered children. Stagger stays under 40ms so long grids never
 * feel like they are loading item by item.
 */
export function staggerContainer(stagger = 0.035, delayChildren = 0): Variants {
  return {
    hidden: {},
    show: {
      transition: { staggerChildren: stagger, delayChildren },
    },
  };
}

/** Child variant used with `staggerContainer` (opacity + 4px only). */
export const staggerItem: Variants = {
  hidden: { opacity: 0, y: 4 },
  show: { opacity: 1, y: 0, transition: transition.base },
};
