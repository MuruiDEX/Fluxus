"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface CostBar {
  key: string;
  name: string;
  color: string;
}

/**
 * Shared bar chart. Loaded through `next/dynamic` by both analytics screens so
 * Recharts (the heaviest client dependency) is fetched only when a chart is
 * actually mounted, never as part of the route's initial JavaScript.
 */
export function CostBarChart({
  data,
  bars,
  formatValue,
  angled,
  showLegend,
}: {
  data: Record<string, string | number>[];
  bars: CostBar[];
  formatValue?: (value: number) => string;
  angled?: boolean;
  showLegend?: boolean;
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
        <XAxis
          dataKey="name"
          fontSize={10}
          interval={0}
          angle={angled ? -15 : 0}
          height={angled ? 50 : 30}
        />
        <YAxis fontSize={11} />
        <Tooltip formatter={(value) => (formatValue ? formatValue(Number(value)) : String(value))} />
        {showLegend && <Legend />}
        {bars.map((bar) => (
          <Bar key={bar.key} dataKey={bar.key} name={bar.name} fill={bar.color} radius={[6, 6, 0, 0]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
