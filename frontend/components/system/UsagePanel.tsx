"use client";

import { useEffect } from "react";
import { Card } from "@/components/system/ui";
import { useLiveUsage } from "@/lib/store/live";

/**
 * LIVE AI usage ledger (Stage 16) — append-only rows plus the derived money
 * summary. Unknown cost renders as "—", never as $0.00: an unpriced call is
 * honestly unpriced, not free. Rendered only in LIVE branches.
 */
function money(n: number): string {
  return `$${n.toFixed(2)}`;
}

function costCell(cost: number | null): string {
  return cost == null ? "—" : money(cost);
}

const KIND_LABELS: Record<string, string> = {
  task_execution: "Task execution",
  brief_questions: "Brief questions",
  brief_summary: "Brief summary",
  plan_generation: "Plan generation",
  brief_finalize: "Brief finalize",
  "": "Legacy (untagged)",
};

function kindLabel(kind: string | null): string {
  return KIND_LABELS[kind ?? ""] ?? kind ?? "Legacy (untagged)";
}

export function UsagePanel({
  businessId,
  projectId,
}: {
  businessId: string;
  projectId?: string;
}) {
  const { status, records, total, summary, error, load, reset } = useLiveUsage();

  useEffect(() => {
    void load({ businessId, projectId });
    return () => reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId, projectId]);

  const pending = summary.reservations.active + summary.reservations.ambiguous;
  const kindEntries = Object.entries(summary.byCallKind).sort(([a], [b]) =>
    kindLabel(a).localeCompare(kindLabel(b)),
  );

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-[13px] font-bold">
          AI usage ledger{projectId ? "" : " · business-wide"}
        </h3>
        <span className="text-[11.5px] text-ink-400">
          {total} accounted call{total === 1 ? "" : "s"}
        </span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 text-[12.5px] sm:grid-cols-4">
        <Stat label="Confirmed" value={money(summary.confirmedCost)} accent="text-emerald-700 dark:text-emerald-300" />
        <Stat label="Estimated" value={money(summary.estimatedCost)} accent="text-amber-700 dark:text-amber-300" />
        <Stat label="Unpriced calls" value={String(summary.unknownCount)} accent="text-ink-500" />
        <Stat label="Reserved (pending)" value={money(pending)} accent="text-brand-500" />
      </div>

      {kindEntries.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {kindEntries.map(([kind, item]) => (
            <span
              key={kind}
              className="rounded-full bg-mist-100 px-2 py-1 text-[11px] text-ink-600 dark:bg-white/5 dark:text-slate-300"
            >
              {kindLabel(kind)}: <b className="tabular-nums">${item.confirmedCost.toFixed(2)}</b>
              {item.estimatedCost > 0 && (
                <span className="text-amber-700 dark:text-amber-300">
                  {" "}+ ${item.estimatedCost.toFixed(2)} est
                </span>
              )}
              {item.unknownCount > 0 && (
                <span className="text-ink-400"> · {item.unknownCount} unpriced</span>
              )}
            </span>
          ))}
        </div>
      )}

      {status === "error" && (
        <p className="mt-3 text-[12.5px] text-amber-700 dark:text-amber-300">
          Usage unavailable ({error ?? "load failed"}).
        </p>
      )}

      {status !== "error" && records.length === 0 && (
        <p className="mt-3 text-[12.5px] text-ink-400">
          {status === "loading"
            ? "Loading usage…"
            : projectId
              ? "No AI calls recorded yet for this project."
              : "No AI calls recorded yet for this business."}
        </p>
      )}

      {records.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-[10.5px] uppercase tracking-wide text-ink-400">
                <th className="py-1.5 pr-2">Kind</th>
                <th className="py-1.5 pr-2">Provider</th>
                <th className="py-1.5 pr-2">Model</th>
                <th className="py-1.5 pr-2 text-right">Tokens</th>
                <th className="py-1.5 pr-2">Cost source</th>
                <th className="py-1.5 text-right">Cost</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr key={r.id} className="border-t border-mist-100 dark:border-white/5">
                  <td className="py-1.5 pr-2 text-ink-500">{kindLabel(r.callKind)}</td>
                  <td className="py-1.5 pr-2 font-medium">{r.provider}</td>
                  <td className="py-1.5 pr-2 text-ink-500">{r.model}</td>
                  <td className="py-1.5 pr-2 text-right tabular-nums">{r.totalTokens}</td>
                  <td className="py-1.5 pr-2 text-ink-500">
                    {r.costSource}
                    {r.usageStatus === "estimated" && (
                      <span className="ml-1 text-amber-700 dark:text-amber-300">(est)</span>
                    )}
                    {r.usageStatus === "unknown" && (
                      <span className="ml-1 text-ink-400">(unpriced)</span>
                    )}
                  </td>
                  <td className="py-1.5 text-right tabular-nums">{costCell(r.costAmount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent: string }) {
  return (
    <div className="rounded-xl bg-mist-100 px-2.5 py-2 dark:bg-white/5">
      <div className="text-[10.5px] uppercase tracking-wide text-ink-400">{label}</div>
      <div className={`mt-0.5 text-[14px] font-bold tabular-nums ${accent}`}>{value}</div>
    </div>
  );
}
