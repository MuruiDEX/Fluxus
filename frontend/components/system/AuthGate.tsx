"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useSession } from "@/lib/store/auth";
import { DemoBadge } from "@/components/system/ui";

/**
 * Route guard + demo-mode banner for the authenticated app shell.
 *
 * - status unknown → skeleton while the first /auth/me resolves.
 * - authenticated → children, no banner.
 * - anonymous → redirect to /login (a reachable backend said "no session").
 * - unreachable → children + prominent DEMO banner (dev/e2e without backend;
 *   the simulation keeps working untouched and unmixed).
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const { status, refresh } = useSession();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (status === "anonymous") router.push("/login");
  }, [status, router, pathname]);

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
  return (
    <>
      {status === "unreachable" && (
        <div className="flex shrink-0 items-center justify-center gap-2 border-b border-dashed border-brand-200 bg-brand-50/70 px-3 py-1.5 dark:border-brand-500/30 dark:bg-brand-500/10">
          <DemoBadge />
          <span className="text-[12px] text-ink-500 dark:text-slate-300">
            Demo mode — simulated Northstar data, no backend connected. All figures are fictional.
          </span>
        </div>
      )}
      {children}
    </>
  );
}
