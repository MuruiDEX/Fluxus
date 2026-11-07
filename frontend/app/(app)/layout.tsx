import { Suspense } from "react";
import { AuthGate } from "@/components/system/AuthGate";
import { CommandBar } from "@/components/system/CommandBar";
import { DevEngineHook } from "@/components/system/DevEngineHook";
import { PageTransition } from "@/components/system/motion";
import { Sidebar } from "@/components/system/Sidebar";
import { TopBar } from "@/components/system/TopBar";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen overflow-hidden bg-canvas">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <Suspense>
          <AuthGate>
            <main className="min-w-0 flex-1 overflow-y-auto px-4 py-5 lg:px-6">
              <PageTransition className="mx-auto max-w-(--flux-page-max)">{children}</PageTransition>
            </main>
          </AuthGate>
        </Suspense>
      </div>
      <CommandBar />
      <DevEngineHook />
    </div>
  );
}
