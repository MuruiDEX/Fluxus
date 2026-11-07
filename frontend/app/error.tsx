"use client";

import { useEffect } from "react";
import { Card, PrimaryButton } from "@/components/system/ui";

/** Root boundary: covers /login and /onboarding, which sit outside the app shell. */
export default function RootError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Fluxus root error:", error);
  }, [error]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-mist-50 px-4 dark:bg-night-950">
      <Card className="w-full max-w-md p-5">
        <h1 className="text-[16px] font-bold text-ink-950 dark:text-white">Something went wrong</h1>
        <p className="mt-1 text-[13px] text-ink-500 dark:text-slate-300">
          The screen failed to load. Retrying usually fixes a transient failure.
        </p>
        {error.digest && (
          <p className="mt-2 font-mono text-[11px] text-ink-400">ref: {error.digest}</p>
        )}
        <div className="mt-4">
          <PrimaryButton onClick={reset}>Try again</PrimaryButton>
        </div>
      </Card>
    </div>
  );
}
