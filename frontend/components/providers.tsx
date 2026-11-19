"use client";

import { MotionConfig } from "framer-motion";
import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ReactNode } from "react";
import { TooltipProvider } from "@/components/system/Tooltip";

/**
 * `reducedMotion="user"` makes every Framer Motion animation honour
 * `prefers-reduced-motion` automatically: transforms become instant while
 * opacity fades stay, so state changes remain legible without movement.
 *
 * The tooltip provider sits here so every icon control shares one open/close
 * delay instead of each call site inventing its own.
 */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <NextThemesProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <MotionConfig reducedMotion="user">
        <TooltipProvider>{children}</TooltipProvider>
      </MotionConfig>
    </NextThemesProvider>
  );
}
