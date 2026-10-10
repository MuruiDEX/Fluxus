import { Suspense } from "react";
import { AuthGate } from "@/components/system/AuthGate";
import { CommandBar } from "@/components/system/CommandBar";
import { DevEngineHook } from "@/components/system/DevEngineHook";
import { Sidebar } from "@/components/system/Sidebar";
import { TopBar } from "@/components/system/TopBar";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen overflow-hidden bg-mist-50 dark:bg-night-950">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <Suspense>
          <AuthGate>
            <main className="min-w-0 flex-1 overflow-y-auto px-4 py-5 lg:px-6">
              <div className="mx-auto flex max-w-[1440px] flex-col gap-4">{children}</div>
            </main>
          </AuthGate>
        </Suspense>
      </div>
      <CommandBar />
      <DevEngineHook />
    </div>
  );
}
