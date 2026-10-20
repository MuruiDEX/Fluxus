/**
 * LIVE E2E global setup (Stage 9) — prepares the dev database before the
 * web servers start. Everything is idempotent: docker compose up, wait for
 * :5432, alembic upgrade head, then the isolated biz_e2e hiring reset.
 */
import { execFile } from "node:child_process";
import { rm, writeFile } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const BACKEND_DIR = path.resolve(__dirname, "..", "..", "backend");
const DEV_SYNC_URL = "postgresql://muse:muse@127.0.0.1:5432/muse";
const DEV_ASYNC_URL = "postgresql+asyncpg://muse:muse@127.0.0.1:5432/muse";

function waitPort(host: string, port: number, timeoutMs: number): Promise<void> {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const probe = () => {
      const socket = net.connect(port, host);
      socket.once("connect", () => {
        socket.end();
        resolve();
      });
      socket.once("error", () => {
        socket.destroy();
        if (Date.now() - started > timeoutMs) {
          reject(new Error(`timed out waiting for ${host}:${port}`));
        } else {
          setTimeout(probe, 1000);
        }
      });
    };
    probe();
  });
}

export default async function globalSetup(): Promise<void> {
  // NOTE: Playwright starts webServer entries BEFORE globalSetup, so no port
  // guards here — freshness is guaranteed by reuseExistingServer:false.
  // Cookies are single-run secrets: drop any saved session, then write an
  // EMPTY storage state so specs can share one UI login (or self-heal with
  // a fresh login when run standalone) without ENOENT failures.
  const statePath = path.resolve(__dirname, ".auth-state.json");
  await rm(statePath, { force: true });
  await writeFile(statePath, JSON.stringify({ cookies: [], origins: [] }));
  await execFileAsync("docker", ["compose", "up", "-d", "postgres"], { cwd: BACKEND_DIR });
  await waitPort("127.0.0.1", 5432, 120_000);
  await execFileAsync("alembic", ["upgrade", "head"], {
    cwd: BACKEND_DIR,
    env: { ...process.env, APP_DATABASE_URL: DEV_SYNC_URL },
  });
  await execFileAsync("python", ["scripts/e2e_live_setup.py"], {
    cwd: BACKEND_DIR,
    env: { ...process.env, APP_DATABASE_URL: DEV_ASYNC_URL },
  });
}
