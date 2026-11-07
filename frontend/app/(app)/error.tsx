"use client";

import { useEffect } from "react";
import { CircleAlert } from "lucide-react";
import { Card, GhostLink, PrimaryButton } from "@/components/system/ui";

/**
 * Route-level error boundary for the app shell. Without it a render error
 * replaced the whole app with a blank production page. Recovery stays local:
 * `reset()` re-renders the segment, and navigation stays available.
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Server errors arrive with a digest only; keep the client report local.
    console.error("Fluxus route error:", error);
  }, [error]);

  return (
    <div className="flex flex-1 items-center justify-center py-8">
      <Card className="w-full max-w-md p-5">
        <div className="flex items-start gap-2.5">
          <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
          <div>
            <h2 className="text-[16px] font-bold text-ink-950 dark:text-white">
              This screen could not render
            </h2>
            <p className="mt-1 text-[13px] text-ink-500 dark:text-slate-300">
              Your data is untouched. Retry the screen, or go back to the workspace.
            </p>
            {error.digest && (
              <p className="mt-2 font-mono text-[11px] text-ink-400">ref: {error.digest}</p>
            )}
          </div>
        </div>
        <div className="mt-4 flex gap-2">
          <PrimaryButton onClick={reset}>Retry screen</PrimaryButton>
          <GhostLink href="/">Back to home</GhostLink>
        </div>
      </Card>
    </div>
  );
}
