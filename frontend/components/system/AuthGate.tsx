"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useSession } from "@/lib/store/auth";
import { runtimeMode } from "@/lib/runtime-config";
import { Card, DemoBadge, GhostButton, PrimaryButton } from "@/components/system/ui";

/**
 * Route guard + mode banner for the authenticated app shell.
 *
 * - DEMO is a deployment mode: no backend request, simulated data only.
 * - LIVE runs the session check and fails closed: unreachable backend or no
 *   business membership renders an error state, never simulated content.
 * - anonymous → redirect to /login (a reachable backend said "no session").
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const { status, refresh, refreshing, sessionError, currentBusinessId } = useSession();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (runtimeMode === "live") refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (runtimeMode === "live" && status === "anonymous") router.push("/login");
  }, [status, router, pathname]);

  if (runtimeMode === "demo") {
    return (
      <>
        <div className="flex shrink-0 items-center justify-center gap-2 border-b border-dashed border-brand/30 bg-brand-soft/70 px-3 py-1.5">
          <DemoBadge />
          <span className="text-caption text-fg-muted">
            Demo mode — simulated Northstar data. All figures are fictional and no backend is
            contacted.
          </span>
        </div>
        {children}
      </>
    );
  }

  if (status === "unknown") {
    return (
      <div role="status" className="flex flex-1 items-center justify-center p-8 text-body-sm text-fg-subtle">
        Checking session…
      </div>
    );
  }
  if (status === "anonymous") {
    return (
      <div role="status" className="flex flex-1 items-center justify-center p-8 text-body-sm text-fg-subtle">
        Redirecting to login…
      </div>
    );
  }
  if (status === "unreachable") {
    return (
      <div role="alert" className="flex flex-1 items-center justify-center p-8">
        <Card className="w-full max-w-md border-danger-line p-5">
          <h2 className="text-heading-sm font-bold text-fg">
            Backend unavailable
          </h2>
          <p className="mt-1 text-body-sm text-fg-muted">
            This is a LIVE deployment. No simulated business data is shown while the server cannot
            be reached.
            {sessionError ? ` (${sessionError})` : ""}
          </p>
          <PrimaryButton
            className="mt-4"
            disabled={refreshing}
            onClick={() => void refresh()}
          >
            {refreshing ? "Retrying…" : "Retry connection"}
          </PrimaryButton>
        </Card>
      </div>
    );
  }
  if (!currentBusinessId) {
    return (
      <div role="alert" className="flex flex-1 items-center justify-center p-8">
        <Card className="w-full max-w-md p-5">
          <h2 className="text-heading-sm font-bold text-fg">
            No business access
          </h2>
          <p className="mt-1 text-body-sm text-fg-muted">
            Your account has no business membership. Ask an operator for a grant, then retry.
          </p>
          <GhostButton
            className="mt-4"
            disabled={refreshing}
            onClick={() => void refresh()}
          >
            {refreshing ? "Checking…" : "Check again"}
          </GhostButton>
        </Card>
      </div>
    );
  }
  return <>{children}</>;
}
