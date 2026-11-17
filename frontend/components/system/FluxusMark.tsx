import { cn } from "@/lib/cn";

/**
 * Fluxus identity mark — the four-point signature star with orbital rings.
 *
 * Presentational and server-safe: no hooks, no client state. The mark sits on
 * the page instead of a filled tile, so it reads as a brand symbol in the
 * sidebar, the mobile header and the hero. Every colour comes from the
 * semantic tokens, which keeps light and dark mode in one definition.
 */
export function FluxusMark({
  size = 32,
  className,
}: {
  size?: number;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn("relative inline-flex shrink-0 items-center justify-center", className)}
      style={{ width: size, height: size }}
    >
      <span
        className="pointer-events-none absolute inset-[6%] rounded-full opacity-70"
        style={{
          background:
            "radial-gradient(circle, color-mix(in srgb, var(--flux-accent) 55%, transparent) 0%, transparent 68%)",
        }}
      />
      <svg
        viewBox="0 0 48 48"
        width={size}
        height={size}
        fill="none"
        className="relative"
        style={{
          filter: "drop-shadow(0 0 6px color-mix(in srgb, var(--flux-accent) 42%, transparent))",
        }}
      >
        <ellipse
          cx="24"
          cy="24"
          rx="20"
          ry="7.6"
          transform="rotate(-30 24 24)"
          stroke="var(--flux-accent)"
          strokeOpacity="0.42"
          strokeWidth="1.2"
        />
        <ellipse
          cx="24"
          cy="24"
          rx="18"
          ry="6.4"
          transform="rotate(36 24 24)"
          stroke="var(--flux-holo)"
          strokeOpacity="0.3"
          strokeWidth="0.9"
        />
        <g transform="translate(0 5.2)">
          <path
            className="flux-mark-body"
            d="M24 4.5c1.5 9.4 4.9 12.8 14.3 14.3-9.4 1.5-12.8 4.9-14.3 14.3-1.5-9.4-4.9-12.8-14.3-14.3C19.1 17.3 22.5 13.9 24 4.5Z"
            fill="var(--flux-brand-solid)"
            fillOpacity="0.95"
          />
        </g>
        <g transform="translate(0 2.6)">
          <path
            d="M24 12.4c1 5.9 3.1 8 9 9-5.9 1-8 3.1-9 9-1-5.9-3.1-8-9-9 5.9-1 8-3.1 9-9Z"
            fill="var(--flux-on-brand)"
            fillOpacity="0.96"
          />
        </g>
        <circle cx="24" cy="24" r="1.5" fill="var(--flux-accent)" />
      </svg>
    </span>
  );
}

/**
 * Large orbital ornament for the dashboard hero. Pure SVG + two rotating
 * rings; the rotation is CSS-driven and removed under reduced motion.
 */
export function FluxusOrbital({ className }: { className?: string }) {
  return (
    <div aria-hidden="true" className={cn("pointer-events-none relative", className)}>
      <div className="absolute inset-[12%] rounded-full bg-brand/15 blur-2xl dark:bg-accent/15" />
      <svg viewBox="0 0 360 360" className="relative h-full w-full">
        <defs>
          <radialGradient id="flux-orb-core" cx="38%" cy="30%" r="72%">
            <stop offset="0%" stopColor="var(--flux-accent)" stopOpacity="0.55" />
            <stop offset="48%" stopColor="var(--flux-brand)" stopOpacity="0.24" />
            <stop offset="100%" stopColor="var(--flux-brand)" stopOpacity="0.02" />
          </radialGradient>
          <linearGradient id="flux-orb-ring" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="var(--flux-accent)" stopOpacity="0.1" />
            <stop offset="48%" stopColor="var(--flux-accent)" stopOpacity="0.8" />
            <stop offset="100%" stopColor="var(--flux-holo)" stopOpacity="0.15" />
          </linearGradient>
          <linearGradient id="flux-orb-ring-soft" x1="1" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--flux-holo)" stopOpacity="0.05" />
            <stop offset="55%" stopColor="var(--flux-holo)" stopOpacity="0.55" />
            <stop offset="100%" stopColor="var(--flux-brand)" stopOpacity="0.08" />
          </linearGradient>
          <filter id="flux-orb-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="7" />
          </filter>
        </defs>

        <circle cx="180" cy="180" r="128" fill="url(#flux-orb-core)" />
        <circle
          cx="180"
          cy="180"
          r="92"
          fill="var(--flux-surface)"
          fillOpacity="0.08"
          stroke="var(--flux-accent)"
          strokeOpacity="0.18"
        />
        <circle cx="180" cy="180" r="92" fill="none" stroke="var(--flux-accent)" strokeOpacity="0.12" strokeWidth="8" filter="url(#flux-orb-glow)" />

        <g className="flux-orbit-ring">
          <ellipse
            cx="180"
            cy="180"
            rx="154"
            ry="48"
            transform="rotate(-26 180 180)"
            fill="none"
            stroke="url(#flux-orb-ring)"
            strokeWidth="1.6"
          />
          <ellipse
            cx="180"
            cy="180"
            rx="144"
            ry="40"
            transform="rotate(-26 180 180)"
            fill="none"
            stroke="var(--flux-accent)"
            strokeOpacity="0.18"
            strokeWidth="0.8"
          />
        </g>
        <g className="flux-orbit-ring-reverse">
          <ellipse
            cx="180"
            cy="180"
            rx="138"
            ry="58"
            transform="rotate(38 180 180)"
            fill="none"
            stroke="url(#flux-orb-ring-soft)"
            strokeWidth="1.2"
          />
        </g>

        {/* Four-point star: the Fluxus signature, centred in the orb. */}
        <g transform="translate(0 32)">
          <path
            d="M180 78c7.2 44.6 23.4 60.8 68 68-44.6 7.2-60.8 23.4-68 68-7.2-44.6-23.4-60.8-68-68 44.6-7.2 60.8-23.4 68-68Z"
            fill="var(--flux-accent)"
            fillOpacity="0.16"
            filter="url(#flux-orb-glow)"
          />
          <path
            d="M180 92c6 37.3 19.7 51 57 57-37.3 6-51 19.7-57 57-6-37.3-19.7-51-57-57 37.3-6 51-19.7 57-57Z"
            fill="var(--flux-on-brand)"
            fillOpacity="0.92"
          />
          <path
            d="M180 104c4.8 30.2 15.8 41.2 46 46-30.2 4.8-41.2 15.8-46 46-4.8-30.2-15.8-41.2-46-46 30.2-4.8 41.2-15.8 46-46Z"
            fill="var(--flux-accent)"
            fillOpacity="0.38"
          />
        </g>
        <circle cx="180" cy="180" r="9" fill="var(--flux-accent)" fillOpacity="0.55" filter="url(#flux-orb-glow)" />
        <circle cx="180" cy="180" r="4" fill="var(--flux-on-brand)" />
      </svg>
    </div>
  );
}
