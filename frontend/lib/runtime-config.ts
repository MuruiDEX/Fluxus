/**
 * Build-time runtime mode and API origin.
 *
 * DEMO and LIVE are separate deployments. DEMO never contacts the backend and
 * renders only the simulated business. LIVE requires an explicit API origin
 * and never falls back to demo data when the backend is unavailable.
 *
 * `next build` enables the production validator; development defaults to DEMO
 * only when `NEXT_PUBLIC_APP_MODE` is unset.
 */

export type AppMode = "demo" | "live";

export interface RuntimeEnv {
  mode?: string;
  apiUrl?: string;
  nodeEnv?: string;
  allowInsecureApi?: string;
}

const DEV_API_ORIGIN = "http://127.0.0.1:8000";
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

export class RuntimeConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RuntimeConfigError";
  }
}

export function resolveAppMode(env: RuntimeEnv): AppMode {
  const raw = (env.mode ?? "").trim().toLowerCase();
  if (!raw) {
    if (env.nodeEnv === "production") {
      throw new RuntimeConfigError(
        "NEXT_PUBLIC_APP_MODE must be explicitly set to \"demo\" or \"live\" for a production build.",
      );
    }
    return "demo";
  }
  if (raw === "demo" || raw === "live") return raw;
  throw new RuntimeConfigError(
    `NEXT_PUBLIC_APP_MODE must be "demo" or "live" (received "${env.mode}").`,
  );
}

function parseOrigin(raw: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new RuntimeConfigError(`NEXT_PUBLIC_API_URL must be an absolute URL (received "${raw}").`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new RuntimeConfigError("NEXT_PUBLIC_API_URL must use http or https.");
  }
  if (url.username || url.password) {
    throw new RuntimeConfigError("NEXT_PUBLIC_API_URL must not contain credentials.");
  }
  if (url.search || url.hash) {
    throw new RuntimeConfigError("NEXT_PUBLIC_API_URL must be an origin without query or fragment.");
  }
  if (url.pathname !== "/" && url.pathname !== "") {
    throw new RuntimeConfigError(
      "NEXT_PUBLIC_API_URL must be an origin only (the client appends /api/v1/...).",
    );
  }
  return url.origin;
}

function isLoopback(origin: string): boolean {
  return LOOPBACK_HOSTS.has(new URL(origin).hostname.toLowerCase());
}

/**
 * Resolve the backend origin. Returns null in DEMO mode when no URL is set;
 * DEMO never uses it for requests. LIVE always requires an origin.
 */
export function resolveApiOrigin(env: RuntimeEnv): string | null {
  const mode = resolveAppMode(env);
  const raw = (env.apiUrl ?? "").trim();
  if (!raw) {
    if (mode === "live") {
      throw new RuntimeConfigError("NEXT_PUBLIC_API_URL is required when NEXT_PUBLIC_APP_MODE=live.");
    }
    return DEV_API_ORIGIN;
  }

  const origin = parseOrigin(raw);
  if (mode === "live" && env.nodeEnv === "production") {
    const loopback = isLoopback(origin);
    const insecureAllowed = env.allowInsecureApi === "1";
    if (loopback && !insecureAllowed) {
      throw new RuntimeConfigError(
        "NEXT_PUBLIC_API_URL must not point at loopback in a production LIVE build.",
      );
    }
    if (origin.startsWith("http:") && !(loopback && insecureAllowed)) {
      throw new RuntimeConfigError(
        "NEXT_PUBLIC_API_URL must use https in a production LIVE build.",
      );
    }
  }
  return origin;
}

export function readRuntimeEnv(): RuntimeEnv {
  return {
    mode: process.env.NEXT_PUBLIC_APP_MODE,
    apiUrl: process.env.NEXT_PUBLIC_API_URL,
    nodeEnv: process.env.NODE_ENV,
    allowInsecureApi: process.env.NEXT_PUBLIC_ALLOW_INSECURE_API,
  };
}

export const runtimeMode: AppMode = resolveAppMode(readRuntimeEnv());

/** DEMO blocks backend calls in the browser; unit tests still exercise the client. */
export function backendRequestsEnabled(): boolean {
  return runtimeMode === "live" || process.env.NODE_ENV === "test";
}
