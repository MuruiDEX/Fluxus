"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useSession } from "@/lib/store/auth";
import { runtimeMode } from "@/lib/runtime-config";
import { DemoBadge } from "@/components/system/ui";

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
        <div className="flex shrink-0 items-center justify-center gap-2 border-b border-dashed border-brand-200 bg-brand-50/70 px-3 py-1.5 dark:border-brand-500/30 dark:bg-brand-500/10">
          <DemoBadge />
          <span className="text-[12px] text-ink-500 dark:text-slate-300">
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
      <div className="flex flex-1 items-center justify-center p-8 text-[13px] text-ink-400">
        Checking session…
      </div>
    );
  }
  if (status === "anonymous") {
    return (
      <div className="flex flex-1 items-center justify-center p-8 text-[13px] text-ink-400">
        Redirecting to login…
      </div>
    );
  }
  if (status === "unreachable") {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <div
          role="alert"
          className="w-full max-w-md rounded-2xl border border-red-200 bg-white p-5 shadow-card dark:border-red-500/30 dark:bg-night-900"
        >
          <h2 className="text-[16px] font-bold text-ink-950 dark:text-white">
            Backend unavailable
          </h2>
          <p className="mt-1 text-[13px] text-ink-500 dark:text-slate-300">
            This is a LIVE deployment. No simulated business data is shown while the server cannot
            be reached.
            {sessionError ? ` (${sessionError})` : ""}
          </p>
          <button
            type="button"
            disabled={refreshing}
            onClick={() => void refresh()}
            className="mt-4 rounded-[10px] bg-brand-600 px-3 py-2 text-[13px] font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {refreshing ? "Retrying…" : "Retry connection"}
          </button>
        </div>
      </div>
    );
  }
  if (!currentBusinessId) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <div
          role="alert"
          className="w-full max-w-md rounded-2xl border border-mist-200 bg-white p-5 shadow-card dark:border-white/10 dark:bg-night-900"
        >
          <h2 className="text-[16px] font-bold text-ink-950 dark:text-white">
            No business access
          </h2>
          <p className="mt-1 text-[13px] text-ink-500 dark:text-slate-300">
            Your account has no business membership. Ask an operator for a grant, then retry.
          </p>
          <button
            type="button"
            disabled={refreshing}
            onClick={() => void refresh()}
            className="mt-4 rounded-[10px] border border-mist-200 px-3 py-2 text-[13px] font-semibold text-ink-700 hover:bg-mist-100 disabled:opacity-50 dark:border-white/10 dark:text-slate-200 dark:hover:bg-white/5"
          >
            {refreshing ? "Checking…" : "Check again"}
          </button>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
