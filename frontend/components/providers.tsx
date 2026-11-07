"use client";

import { MotionConfig } from "framer-motion";
import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ReactNode } from "react";

/**
 * `reducedMotion="user"` makes every Framer Motion animation honour
 * `prefers-reduced-motion` automatically: transforms become instant while
 * opacity fades stay, so state changes remain legible without movement.
 */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <NextThemesProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </NextThemesProvider>
  );
}
