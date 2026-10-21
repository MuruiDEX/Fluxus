import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const target = process.argv[2];
const env = { ...process.env };

if (target === "demo") {
  env.NEXT_PUBLIC_APP_MODE = "demo";
} else if (target === "live-e2e") {
  env.NEXT_PUBLIC_APP_MODE = "live";
  env.NEXT_PUBLIC_API_URL = "http://127.0.0.1:8000";
  env.NEXT_PUBLIC_ALLOW_INSECURE_API = "1";
} else if (target && target !== "live") {
  console.error(`Unknown build target "${target}".`);
  process.exit(1);
} else if (target === "live") {
  env.NEXT_PUBLIC_APP_MODE = "live";
}

function fail(message) {
  console.error(`Build configuration error: ${message}`);
  process.exit(1);
}

const mode = (env.NEXT_PUBLIC_APP_MODE ?? "").trim().toLowerCase();
if (!mode) {
  fail('NEXT_PUBLIC_APP_MODE must be set to "demo" or "live".');
}
if (mode !== "demo" && mode !== "live") {
  fail('NEXT_PUBLIC_APP_MODE must be "demo" or "live".');
}

if (mode === "live") {
  const raw = (env.NEXT_PUBLIC_API_URL ?? "").trim();
  if (!raw) fail("NEXT_PUBLIC_API_URL is required for a LIVE build.");

  let url;
  try {
    url = new URL(raw);
  } catch {
    fail("NEXT_PUBLIC_API_URL must be an absolute URL.");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    fail("NEXT_PUBLIC_API_URL must use http or https.");
  }
  if (url.username || url.password) {
    fail("NEXT_PUBLIC_API_URL must not contain credentials.");
  }
  if (url.search || url.hash || (url.pathname !== "/" && url.pathname !== "")) {
    fail("NEXT_PUBLIC_API_URL must be an origin only (no path, query, or fragment).");
  }
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname.toLowerCase());
  const insecureAllowed = env.NEXT_PUBLIC_ALLOW_INSECURE_API === "1";
  if (loopback && !insecureAllowed) {
    fail("NEXT_PUBLIC_API_URL must not point at loopback in a production build.");
  }
  if (url.protocol === "http:" && !(loopback && insecureAllowed)) {
    fail("NEXT_PUBLIC_API_URL must use https in a production build.");
  }
}

const nextBin = path.join(root, "node_modules", "next", "dist", "bin", "next");
const result = spawnSync(process.execPath, [nextBin, "build"], {
  cwd: root,
  env,
  stdio: "inherit",
});
process.exit(result.status ?? 1);
