import { Card, GhostLink } from "@/components/system/ui";

export default function NotFound() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-mist-50 px-4 dark:bg-night-950">
      <Card className="w-full max-w-md p-6">
        <p className="text-[12px] font-bold uppercase tracking-wide text-ink-400">404</p>
        <h1 className="mt-1 text-[18px] font-bold text-ink-950 dark:text-white">
          This page does not exist
        </h1>
        <p className="mt-1 text-[13px] text-ink-500 dark:text-slate-300">
          The link may be outdated. Your business data has not changed.
        </p>
        <div className="mt-4">
          <GhostLink href="/">Go to workspace</GhostLink>
        </div>
      </Card>
    </div>
  );
}
