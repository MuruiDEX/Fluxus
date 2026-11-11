"use client";

import { useEffect, useMemo } from "react";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import type { ReactNode } from "react";
import { staggerContainer, staggerItem, transition } from "@/lib/motion";
import { cn } from "@/lib/cn";

/**
 * Entrance animations must never hide server-rendered content: Framer sets
 * `opacity: 0` as an inline style, which would keep the page blank until
 * hydration finishes (an LCP regression, and a blank page if JS fails).
 *
 * So the first paint of the session is rendered static; from the first client
 * navigation onward the same primitives animate. The flag is module state on
 * purpose — it is presentation-only and identical for every mount.
 */
let animated = false;

export function PageTransition({ children, className }: { children: ReactNode; className?: string }) {
  const pathname = usePathname();
  // Read per render: the first paint stays static, later navigations animate.
  const animate = animated;

  useEffect(() => {
    animated = true;
  }, []);

  return (
    <motion.div
      key={pathname}
      initial={animate ? { opacity: 0, y: 6 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={transition.slow}
      className={cn("flex flex-col gap-4", className)}
    >
      {children}
    </motion.div>
  );
}

/**
 * Staggered list/grid container. Pair with `<StaggerItem>` children; the
 * stagger is small enough that a 3-row grid settles in ~100ms.
 */
export function StaggerGroup({
  children,
  className,
  stagger,
}: {
  children: ReactNode;
  className?: string;
  stagger?: number;
}) {
  const variants = useMemo(() => staggerContainer(stagger), [stagger]);
  return (
    <motion.div
      initial={animated ? "hidden" : false}
      animate="show"
      variants={variants}
      className={className}
    >
      {children}
    </motion.div>
  );
}

export function StaggerItem({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <motion.div variants={staggerItem} className={className}>
      {children}
    </motion.div>
  );
}
